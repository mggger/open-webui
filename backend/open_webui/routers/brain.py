import json
import os
import time
import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from open_webui.models.config import Config
from open_webui.utils.auth import get_admin_user, get_verified_user

try:
    from livekit import api
except ImportError:  # pragma: no cover - handled with a useful API error
    api = None


router = APIRouter()


class BrainSessionResponse(BaseModel):
    session_id: str
    room_name: str
    livekit_url: str
    token: str
    expires_at: int


class BrainMCPServerSettings(BaseModel):
    ID: str = ""
    NAME: str = "MCP Server"
    URL: str = ""
    ALLOWED_TOOLS: str = ""
    HEADERS: str = "{}"


class BrainSettings(BaseModel):
    NAME: str = "Brain"
    LIVEKIT_URL: str = "ws://localhost:7880"
    LIVEKIT_API_KEY: str = ""
    LIVEKIT_API_SECRET: str = ""
    LLM_MODEL: str = ""
    STT_LANGUAGE: str = "en"
    MCP_URL: str = ""
    MCP_ALLOWED_TOOLS: str = ""
    MCP_HEADERS: str = "{}"
    MCP_SERVERS: list[BrainMCPServerSettings] = Field(default_factory=list)
    INSTRUCTIONS: str = ""


class BrainRuntimeSettings(BrainSettings):
    LLM_BASE_URL: str = ""
    LLM_API_KEY: str = ""
    STT_ENGINE: str = ""
    STT_BASE_URL: str = ""
    STT_API_KEY: str = ""
    STT_MODEL: str = "whisper-1"
    TTS_ENGINE: str = ""
    TTS_BASE_URL: str = ""
    TTS_API_KEY: str = ""
    TTS_MODEL: str = ""
    TTS_VOICE: str = "default"


class MCPToolsRequest(BaseModel):
    url: str
    headers: str = "{}"


class MCPToolInfo(BaseModel):
    name: str
    description: str = ""


def describe_exception(exc: BaseException) -> str:
    """Return useful leaf errors instead of an opaque ExceptionGroup message."""
    nested = getattr(exc, "exceptions", None)
    if nested:
        messages = [describe_exception(item) for item in nested]
        return "; ".join(dict.fromkeys(message for message in messages if message))
    message = str(exc).strip()
    return message or exc.__class__.__name__


async def resolve_llm_connection(
    request: Request, selected_model_id: str
) -> tuple[str, str, str]:
    if not selected_model_id:
        raise HTTPException(status_code=503, detail="No Brain model selected")

    models = request.app.state.MODELS
    model = models.get(selected_model_id) if models else None
    if not model:
        raise HTTPException(
            status_code=503,
            detail="The selected Brain model is unavailable. Refresh the model list and try again.",
        )

    base_model_id = (model.get("info") or {}).get("base_model_id")
    if base_model_id:
        base_model = models.get(base_model_id)
        if base_model:
            model = base_model
            selected_model_id = base_model_id

    url_idx = model.get("urlIdx")
    if url_idx is None or model.get("owned_by") != "openai":
        raise HTTPException(
            status_code=503,
            detail="Brain currently supports models from OpenAI-compatible Connections only.",
        )

    url_idx = int(url_idx)
    openai_config = await Config.get_many(
        "openai.api_base_urls", "openai.api_keys", "openai.api_configs"
    )
    urls = openai_config.get("openai.api_base_urls") or []
    keys = openai_config.get("openai.api_keys") or []
    api_configs = openai_config.get("openai.api_configs") or {}
    if url_idx >= len(urls):
        raise HTTPException(
            status_code=503, detail="The selected model connection is unavailable"
        )

    api_config = api_configs.get(str(url_idx), api_configs.get(urls[url_idx], {}))
    prefix = api_config.get("prefix_id")
    direct_model_id = selected_model_id
    if prefix and direct_model_id.startswith(f"{prefix}."):
        direct_model_id = direct_model_id[len(prefix) + 1 :]

    return urls[url_idx], keys[url_idx] if url_idx < len(keys) else "", direct_model_id


BRAIN_CONFIG_NAMESPACE = "brain"

AUDIO_CONFIG_KEYS = {
    "STT_ENGINE": "audio.stt.engine",
    "STT_OPENAI_API_BASE_URL": "audio.stt.openai.api_base_url",
    "STT_OPENAI_API_KEY": "audio.stt.openai.api_key",
    "STT_MODEL": "audio.stt.model",
    "TTS_ENGINE": "audio.tts.engine",
    "TTS_OPENAI_API_BASE_URL": "audio.tts.openai.api_base_url",
    "TTS_OPENAI_API_KEY": "audio.tts.openai.api_key",
    "TTS_MODEL": "audio.tts.model",
    "TTS_VOICE": "audio.tts.voice",
}


async def get_audio_settings() -> dict:
    values = await Config.get_many(*AUDIO_CONFIG_KEYS.values())
    return {field: values.get(key) for field, key in AUDIO_CONFIG_KEYS.items()}


async def current_settings() -> BrainSettings:
    # Brain settings are stored as one config row per field ("brain.<FIELD>").
    # Installs upgraded from the old JSON-blob config are migrated to this shape
    # by upstream's per-key config migration.
    prefix = f"{BRAIN_CONFIG_NAMESPACE}."
    stored = {
        key[len(prefix):]: value
        for key, value in (await Config.get_namespace(BRAIN_CONFIG_NAMESPACE)).items()
        if value is not None
    }
    defaults = BrainSettings(
        NAME=os.getenv("BRAIN_NAME", "Brain"),
        LIVEKIT_URL=os.getenv("LIVEKIT_URL", "ws://localhost:7880"),
        LIVEKIT_API_KEY=os.getenv("LIVEKIT_API_KEY", ""),
        LIVEKIT_API_SECRET=os.getenv("LIVEKIT_API_SECRET", ""),
    )
    merged = {**defaults.model_dump(), **stored}
    if not merged.get("MCP_SERVERS") and merged.get("MCP_URL"):
        merged["MCP_SERVERS"] = [
            {
                "ID": "legacy_mcp",
                "NAME": "Internal MCP",
                "URL": merged.get("MCP_URL", ""),
                "ALLOWED_TOOLS": merged.get("MCP_ALLOWED_TOOLS", ""),
                "HEADERS": merged.get("MCP_HEADERS", "{}"),
            }
        ]
    return BrainSettings(**merged)


@router.get("/config")
async def get_brain_config(user=Depends(get_verified_user)):
    settings = await current_settings()
    return {
        "enabled": True,
        "name": settings.NAME,
    }


@router.get("/status")
async def get_brain_status(request: Request, user=Depends(get_verified_user)):
    manager = getattr(request.app.state, "brain_agent", None)
    return {
        "status": manager.status if manager else "unavailable",
        "error": manager.error if manager else "Brain Agent manager is unavailable",
    }


@router.get("/settings", response_model=BrainSettings)
async def get_brain_settings(user=Depends(get_admin_user)):
    return await current_settings()


@router.post("/mcp/tools", response_model=list[MCPToolInfo])
async def get_mcp_tools(form_data: MCPToolsRequest, user=Depends(get_admin_user)):
    url = form_data.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="Enter an MCP server URL")

    try:
        headers = json.loads(form_data.headers or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid headers JSON: {exc.msg}")
    if not isinstance(headers, dict) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in headers.items()
    ):
        raise HTTPException(
            status_code=400, detail="MCP headers must be a JSON object of string values"
        )

    try:
        # mcp 2.x carries its HTTP transport on httpx2 rather than httpx.
        import httpx2
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        timeout = httpx2.Timeout(15, read=30)
        async with httpx2.AsyncClient(headers=headers, timeout=timeout) as client:
            async with streamable_http_client(url, http_client=client) as streams:
                async with ClientSession(streams[0], streams[1]) as session:
                    await session.initialize()
                    result = await session.list_tools()
        return [
            MCPToolInfo(name=tool.name, description=tool.description or "")
            for tool in result.tools
        ]
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=(
                "Unable to fetch tools from the MCP server: "
                f"{describe_exception(exc)}"
            ),
        ) from exc


@router.post("/settings", response_model=BrainSettings)
async def update_brain_settings(
    request: Request, form_data: BrainSettings, user=Depends(get_admin_user)
):
    seen_ids: set[str] = set()
    for index, server in enumerate(form_data.MCP_SERVERS):
        server.ID = server.ID.strip() or f"mcp_{uuid.uuid4().hex}"
        if server.ID in seen_ids:
            server.ID = f"mcp_{uuid.uuid4().hex}"
        seen_ids.add(server.ID)
        server.NAME = server.NAME.strip() or f"MCP Server {index + 1}"
        server.URL = server.URL.strip()
        try:
            headers = json.loads(server.HEADERS or "{}")
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid headers JSON for {server.NAME}: {exc.msg}",
            ) from exc
        if not isinstance(headers, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in headers.items()
        ):
            raise HTTPException(
                status_code=400,
                detail=f"Headers for {server.NAME} must be a JSON object of string values",
            )

    # Keep legacy fields synchronized for rolling upgrades, while the runtime uses MCP_SERVERS.
    first_server = form_data.MCP_SERVERS[0] if form_data.MCP_SERVERS else None
    form_data.MCP_URL = first_server.URL if first_server else ""
    form_data.MCP_ALLOWED_TOOLS = first_server.ALLOWED_TOOLS if first_server else ""
    form_data.MCP_HEADERS = first_server.HEADERS if first_server else "{}"
    try:
        await Config.upsert(
            {
                f"{BRAIN_CONFIG_NAMESPACE}.{field}": value
                for field, value in form_data.model_dump().items()
            }
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Failed to save Brain settings") from exc
    manager = getattr(request.app.state, "brain_agent", None)
    if manager is not None:
        await manager.restart()
    return form_data


@router.get("/runtime-config", response_model=BrainRuntimeSettings)
async def get_runtime_config(
    request: Request, x_brain_secret: str = Header(default="")
):
    expected = os.getenv("BRAIN_AGENT_SHARED_SECRET", "")
    if not expected or x_brain_secret != expected:
        raise HTTPException(status_code=401, detail="Invalid Brain agent secret")
    brain = (await current_settings()).model_dump()
    audio = await get_audio_settings()
    llm_base_url, llm_api_key, llm_model = await resolve_llm_connection(
        request, brain["LLM_MODEL"]
    )
    brain["LLM_MODEL"] = llm_model
    return BrainRuntimeSettings(
        **brain,
        LLM_BASE_URL=llm_base_url,
        LLM_API_KEY=llm_api_key,
        STT_ENGINE=audio["STT_ENGINE"] or "",
        STT_BASE_URL=audio["STT_OPENAI_API_BASE_URL"] or "",
        STT_API_KEY=audio["STT_OPENAI_API_KEY"] or "",
        STT_MODEL=audio["STT_MODEL"] or "whisper-1",
        TTS_ENGINE=audio["TTS_ENGINE"] or "",
        TTS_BASE_URL=audio["TTS_OPENAI_API_BASE_URL"] or "",
        TTS_API_KEY=audio["TTS_OPENAI_API_KEY"] or "",
        TTS_MODEL=audio["TTS_MODEL"] or "",
        TTS_VOICE=audio["TTS_VOICE"] or "default",
    )


@router.post("/sessions", response_model=BrainSessionResponse)
async def create_brain_session(request: Request, user=Depends(get_verified_user)):
    settings = await current_settings()
    if api is None:
        raise HTTPException(status_code=503, detail="livekit-api is not installed")

    manager = getattr(request.app.state, "brain_agent", None)
    if manager is None or manager.status != "running":
        detail = (
            manager.error if manager and manager.error else "Brain Agent is not ready"
        )
        raise HTTPException(status_code=503, detail=detail)

    if not settings.LLM_MODEL.strip():
        raise HTTPException(
            status_code=503,
            detail="Select a language model in Admin Settings > Brain before starting a conversation.",
        )

    audio = await get_audio_settings()
    if (
        audio["STT_ENGINE"] != "openai"
        or not audio["STT_OPENAI_API_BASE_URL"]
        or not audio["STT_MODEL"]
    ):
        raise HTTPException(
            status_code=503,
            detail="Configure an OpenAI-compatible STT model in Admin Settings > Audio.",
        )
    if (
        audio["TTS_ENGINE"] != "openai"
        or not audio["TTS_OPENAI_API_BASE_URL"]
        or not audio["TTS_MODEL"]
    ):
        raise HTTPException(
            status_code=503,
            detail="Configure an OpenAI-compatible TTS model in Admin Settings > Audio.",
        )

    livekit_url = settings.LIVEKIT_URL.strip()
    api_key = settings.LIVEKIT_API_KEY.strip()
    api_secret = settings.LIVEKIT_API_SECRET.strip()
    if not livekit_url or not api_key or not api_secret:
        raise HTTPException(
            status_code=503,
            detail="Configure the LiveKit URL, API key, and API secret in Admin Settings > Brain.",
        )

    session_id = f"brain_{uuid.uuid4().hex}"
    room_name = session_id
    ttl = timedelta(minutes=5)
    metadata = json.dumps(
        {
            "session_id": session_id,
            "user_id": user.id,
            "user_name": user.name,
            "role": user.role,
        },
        ensure_ascii=False,
    )
    token = (
        api.AccessToken(api_key, api_secret)
        .with_identity(f"user:{user.id}")
        .with_name(user.name or "Brain user")
        .with_metadata(metadata)
        .with_ttl(ttl)
        .with_grants(
            api.VideoGrants(
                room_join=True,
                room=room_name,
                can_publish=True,
                can_subscribe=True,
                can_publish_data=True,
            )
        )
        .to_jwt()
    )
    return BrainSessionResponse(
        session_id=session_id,
        room_name=room_name,
        livekit_url=livekit_url,
        token=token,
        expires_at=int(time.time() + ttl.total_seconds()),
    )
