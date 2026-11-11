import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel


from open_webui.models.config import Config
from open_webui.utils.access_control import has_permission
from open_webui.utils.auth import get_admin_user, get_verified_user
from open_webui.socket.main import get_event_emitter
from open_webui.retrieval.deep_search import generate_deep_search_report

log = logging.getLogger(__name__)

router = APIRouter()

DEFAULT_DEEP_SEARCH_BREADTH = 3
DEFAULT_DEEP_SEARCH_RESULT_COUNT = 5
DEFAULT_DEEP_SEARCH_CONCURRENCY = 2


class DeepSearchConfigForm(BaseModel):
    ENABLE_DEEP_SEARCH: Optional[bool] = None
    SERPAPI_API_KEY: Optional[str] = None
    SERPAPI_ENGINE: Optional[str] = None
    DEEP_SEARCH_MAX_ITERATIONS: Optional[int] = None


class DeepSearchReportForm(BaseModel):
    query: str
    model: str
    system: Optional[str] = None
    messages: Optional[list[dict]] = None
    depth: Optional[int] = None
    breadth: Optional[int] = None
    chat_id: Optional[str] = None
    message_id: Optional[str] = None
    session_id: Optional[str] = None


DEEP_SEARCH_CONFIG_KEYS = {
    "ENABLE_DEEP_SEARCH": "deep_search.enable",
    "SERPAPI_API_KEY": "web.search.serpapi_api_key",
    "SERPAPI_ENGINE": "web.search.serpapi_engine",
    "DEEP_SEARCH_MAX_ITERATIONS": "deep_search.max_iterations",
}


async def get_deep_search_settings() -> dict:
    values = await Config.get_many(*DEEP_SEARCH_CONFIG_KEYS.values())
    return {field: values.get(key) for field, key in DEEP_SEARCH_CONFIG_KEYS.items()}


@router.get("/config")
async def get_deep_search_config(request: Request, user=Depends(get_admin_user)):
    return await get_deep_search_settings()


@router.post("/config/update")
async def update_deep_search_config(
    request: Request, form_data: DeepSearchConfigForm, user=Depends(get_admin_user)
):
    updates = form_data.model_dump(exclude_none=True)
    if "DEEP_SEARCH_MAX_ITERATIONS" in updates:
        updates["DEEP_SEARCH_MAX_ITERATIONS"] = max(1, updates["DEEP_SEARCH_MAX_ITERATIONS"])
    if updates:
        await Config.upsert({DEEP_SEARCH_CONFIG_KEYS[field]: value for field, value in updates.items()})
    return await get_deep_search_settings()


@router.post("/report")
async def generate_report(
    request: Request, form_data: DeepSearchReportForm, user=Depends(get_verified_user)
):
    settings = await get_deep_search_settings()
    if not settings["ENABLE_DEEP_SEARCH"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Deep search is disabled",
        )

    if user.role != "admin" and not await has_permission(
        user.id, "features.web_search", await Config.get("user.permissions")
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient permissions to use deep search",
        )

    if not settings["SERPAPI_API_KEY"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="SerpAPI key is not configured",
        )

    model_id = form_data.model
    models = request.app.state.MODELS
    if model_id not in models:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Model not found"
        )

    depth = form_data.depth or settings["DEEP_SEARCH_MAX_ITERATIONS"]
    breadth = max(1, form_data.breadth) if form_data.breadth else DEFAULT_DEEP_SEARCH_BREADTH

    event_emitter = None
    if form_data.chat_id and form_data.message_id:
        event_emitter = await get_event_emitter(
            {
                "chat_id": form_data.chat_id,
                "message_id": form_data.message_id,
                "session_id": form_data.session_id,
                "user_id": user.id,
            }
        )

    result = await generate_deep_search_report(
        request=request,
        user=user,
        model_id=model_id,
        query=form_data.query,
        system_prompt=form_data.system,
        messages=form_data.messages,
        depth=depth,
        breadth=breadth,
        result_count=DEFAULT_DEEP_SEARCH_RESULT_COUNT,
        concurrency=DEFAULT_DEEP_SEARCH_CONCURRENCY,
        max_iterations=settings["DEEP_SEARCH_MAX_ITERATIONS"],
        on_progress=event_emitter,
    )

    return PlainTextResponse(result.get("answer", ""))
