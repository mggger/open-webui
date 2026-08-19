import asyncio
import json
import logging
import os

import httpx
from dotenv import load_dotenv
from livekit.agents import Agent, AgentServer, AgentSession, JobContext, cli, mcp
from livekit.agents.llm import ToolFlag
from livekit.plugins import openai


load_dotenv()
logger = logging.getLogger("brain-agent")


def required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def api_key(name: str) -> str:
    # OpenAI SDK requires a non-empty value even when an internal gateway ignores auth.
    return os.getenv(name, "internal-no-key")


async def load_config() -> dict[str, object]:
    url = os.getenv("BRAIN_CONFIG_URL", "").strip()
    if not url:
        return {}
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(
            url,
            headers={"X-Brain-Secret": required("BRAIN_AGENT_SHARED_SECRET")},
        )
        response.raise_for_status()
        return response.json()


def value(config: dict[str, object], name: str, default: str = "") -> str:
    configured = config.get(name)
    if configured is not None and str(configured).strip():
        return str(configured).strip()
    return os.getenv(f"BRAIN_{name}", default).strip()


def required_value(config: dict[str, object], name: str) -> str:
    result = value(config, name)
    if not result:
        raise RuntimeError(f"BRAIN_{name} is required")
    return result


def build_tools(config: dict[str, object]) -> list[mcp.MCPToolset]:
    url = value(config, "MCP_URL")
    if not url:
        return []

    allowed = [item.strip() for item in value(config, "MCP_ALLOWED_TOOLS").split(",")]
    allowed = [item for item in allowed if item]
    headers = json.loads(value(config, "MCP_HEADERS", "{}"))
    cancellable = [
        item.strip() for item in os.getenv("BRAIN_MCP_CANCELLABLE_TOOLS", "").split(",")
    ]
    tool_options = {
        name: {"flags": ToolFlag.CANCELLABLE, "report_progress": True}
        for name in cancellable
        if name
    }
    return [
        mcp.MCPToolset(
            id="internal_mcp",
            mcp_server=mcp.MCPServerHTTP(
                url=url,
                transport_type="streamable_http",
                allowed_tools=allowed or None,
                headers=headers,
                client_session_timeout_seconds=float(
                    os.getenv("BRAIN_MCP_TIMEOUT_SECONDS", "120")
                ),
            ),
            tool_options=tool_options,
        )
    ]


class BrainAgent(Agent):
    def __init__(self, config: dict[str, object]) -> None:
        super().__init__(
            instructions=value(
                config,
                "INSTRUCTIONS",
                "You are Brain, the company's voice assistant. Speak in concise, natural English. "
                "Use the available tools when internal information or an action is required, and "
                "never invent tool results. Do not use Markdown, tables, code blocks, or emoji. "
                "Ask no more than one follow-up question at a time.",
            ),
        )

    async def on_enter(self) -> None:
        self.session.generate_reply(
            instructions="Briefly greet the user and ask how you can help."
        )


server = AgentServer()


@server.rtc_session()
async def entrypoint(ctx: JobContext) -> None:
    ctx.log_context_fields = {"room": ctx.room.name}
    config = await load_config()
    stt_engine = value(config, "STT_ENGINE")
    tts_engine = value(config, "TTS_ENGINE")
    if stt_engine not in ("openai",):
        raise RuntimeError(
            "Brain currently requires an OpenAI-compatible STT engine in Open WebUI Audio settings"
        )
    if tts_engine not in ("openai",):
        raise RuntimeError(
            "Brain currently requires an OpenAI-compatible TTS engine in Open WebUI Audio settings"
        )
    session_tools = build_tools(config)
    if value(config, "MCP_URL"):
        try:
            await asyncio.gather(*(toolset.setup() for toolset in session_tools))
        except Exception as exc:
            logger.exception("Brain MCP initialization failed")
            raise RuntimeError(
                f"Brain could not connect to the configured MCP server: {exc}"
            ) from exc
        available_tools = [
            tool.id for toolset in session_tools for tool in toolset.tools
        ]
        if not available_tools:
            raise RuntimeError(
                "Brain connected to MCP, but no allowed tools were returned"
            )
        logger.info(
            "Brain MCP ready with %d tools: %s",
            len(available_tools),
            ", ".join(available_tools),
        )
    session = AgentSession(
        stt=openai.STT(
            model=value(config, "STT_MODEL", "whisper-1"),
            language=value(config, "STT_LANGUAGE", "en"),
            base_url=required_value(config, "STT_BASE_URL"),
            api_key=value(config, "STT_API_KEY", "internal-no-key"),
        ),
        llm=openai.LLM(
            model=required_value(config, "LLM_MODEL"),
            base_url=required_value(config, "LLM_BASE_URL"),
            api_key=value(config, "LLM_API_KEY", "internal-no-key"),
        ),
        tts=openai.TTS(
            # Select the OpenAI binary audio protocol for compatible internal TTS servers.
            model="tts-1",
            voice=value(config, "TTS_VOICE", "default"),
            base_url=required_value(config, "TTS_BASE_URL"),
            api_key=value(config, "TTS_API_KEY", "internal-no-key"),
            response_format="wav",
        ),
        tools=session_tools,
        turn_handling={
            "interruption": {
                "enabled": False,
                "discard_audio_if_uninterruptible": True,
            }
        },
    )

    @session.on("agent_state_changed")
    def _gate_microphone(event) -> None:
        # Only Listening is an input state. Audio received while the agent
        # is thinking or speaking must never reach VAD/STT or a later turn.
        session.input.set_audio_enabled(event.new_state == "listening")

    await session.start(agent=BrainAgent(config), room=ctx.room)


if __name__ == "__main__":
    cli.run_app(server)
