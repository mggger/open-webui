import asyncio
import json
import logging
from typing import Any

from starlette.requests import Request

from open_webui.routers.brain import current_settings, resolve_llm_connection


log = logging.getLogger(__name__)


class EmbeddedBrainAgent:
    def __init__(self, app: Any) -> None:
        self.app = app
        self.server = None
        self.task: asyncio.Task | None = None
        self.status = "stopped"
        self.error = ""

    def _runtime_config(self) -> dict[str, Any]:
        settings = current_settings()
        request = Request({"type": "http", "app": self.app})
        llm_url, llm_key, llm_model = resolve_llm_connection(
            request, settings.LLM_MODEL
        )
        audio = self.app.state.config
        return {
            **settings.model_dump(),
            "LLM_BASE_URL": llm_url,
            "LLM_API_KEY": llm_key,
            "LLM_MODEL": llm_model,
            "STT_ENGINE": audio.STT_ENGINE or "",
            "STT_BASE_URL": audio.STT_OPENAI_API_BASE_URL or "",
            "STT_API_KEY": audio.STT_OPENAI_API_KEY or "",
            "STT_MODEL": audio.STT_MODEL or "whisper-1",
            "STT_LANGUAGE": settings.STT_LANGUAGE or "en",
            "TTS_ENGINE": audio.TTS_ENGINE or "",
            "TTS_BASE_URL": audio.TTS_OPENAI_API_BASE_URL or "",
            "TTS_API_KEY": audio.TTS_OPENAI_API_KEY or "",
            "TTS_MODEL": audio.TTS_MODEL or "",
            "TTS_VOICE": audio.TTS_VOICE or "default",
        }

    async def start(self) -> None:
        if self.task and not self.task.done():
            return

        settings = current_settings()
        if not all(
            [
                settings.LIVEKIT_URL.strip(),
                settings.LIVEKIT_API_KEY.strip(),
                settings.LIVEKIT_API_SECRET.strip(),
                settings.LLM_MODEL.strip(),
            ]
        ):
            self.status = "not_configured"
            log.info("Embedded Brain Agent is waiting for Brain settings")
            return

        try:
            from livekit.agents import (
                Agent,
                AgentServer,
                AgentSession,
                JobContext,
                JobExecutorType,
                mcp,
            )
            from livekit.plugins import openai
        except ImportError as exc:
            self.status = "unavailable"
            self.error = str(exc)
            log.warning("Embedded Brain Agent dependencies are unavailable: %s", exc)
            return

        manager = self

        class BrainAgent(Agent):
            def __init__(self, config: dict[str, Any]) -> None:
                super().__init__(
                    instructions=config.get("INSTRUCTIONS")
                    or "You are Brain, the company's voice assistant. Speak naturally and concisely. "
                    "Use tools for internal information and never invent tool results."
                )

            async def on_enter(self) -> None:
                self.session.generate_reply(
                    instructions="Briefly greet the user and ask how you can help."
                )

        def tools(config: dict[str, Any]):
            if not config.get("MCP_URL"):
                return []
            allowed = [
                item.strip()
                for item in str(config.get("MCP_ALLOWED_TOOLS", "")).split(",")
                if item.strip()
            ]
            return [
                mcp.MCPToolset(
                    id="internal_mcp",
                    mcp_server=mcp.MCPServerHTTP(
                        url=config["MCP_URL"],
                        transport_type="streamable_http",
                        allowed_tools=allowed or None,
                        headers=json.loads(config.get("MCP_HEADERS") or "{}"),
                    ),
                    tool_options={},
                )
            ]

        self.server = AgentServer(
            ws_url=settings.LIVEKIT_URL,
            api_key=settings.LIVEKIT_API_KEY,
            api_secret=settings.LIVEKIT_API_SECRET,
            job_executor_type=JobExecutorType.THREAD,
            num_idle_processes=0,
            port=0,
        )

        @self.server.rtc_session()
        async def entrypoint(ctx: JobContext) -> None:
            config = manager._runtime_config()
            if config["STT_ENGINE"] != "openai" or config["TTS_ENGINE"] != "openai":
                raise RuntimeError("Brain requires OpenAI-compatible STT and TTS")
            session_tools = tools(config)
            if config.get("MCP_URL"):
                try:
                    await asyncio.gather(
                        *(toolset.setup() for toolset in session_tools)
                    )
                except Exception as exc:
                    log.exception("Brain MCP initialization failed")
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
                log.info(
                    "Brain MCP ready with %d tools: %s",
                    len(available_tools),
                    ", ".join(available_tools),
                )
            session = AgentSession(
                stt=openai.STT(
                    model=config["STT_MODEL"],
                    language=config["STT_LANGUAGE"],
                    base_url=config["STT_BASE_URL"],
                    api_key=config["STT_API_KEY"] or "EMPTY",
                ),
                llm=openai.LLM(
                    model=config["LLM_MODEL"],
                    base_url=config["LLM_BASE_URL"],
                    api_key=config["LLM_API_KEY"] or "EMPTY",
                ),
                tts=openai.TTS(
                    # The OpenAI plugin selects binary audio vs SSE from the model name.
                    # Internal OpenAI-compatible TTS endpoints return binary WAV audio,
                    # so use the protocol-compatible model alias even when the configured
                    # implementation is Kokoro.
                    model="tts-1",
                    voice=config["TTS_VOICE"],
                    base_url=config["TTS_BASE_URL"],
                    api_key=config["TTS_API_KEY"] or "EMPTY",
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

        self.status = "starting"
        self.error = ""
        self.task = asyncio.create_task(self.server.run(devmode=True))
        self.task.add_done_callback(self._on_done)
        self.status = "running"
        log.info("Embedded Brain Agent started")

    def _on_done(self, task: asyncio.Task) -> None:
        if task.cancelled():
            return
        error = task.exception()
        if error:
            self.status = "error"
            self.error = str(error)
            log.error(
                "Embedded Brain Agent stopped unexpectedly",
                exc_info=(type(error), error, error.__traceback__),
            )

    async def restart(self) -> None:
        await self.stop()
        await self.start()

    async def stop(self) -> None:
        if self.server is not None:
            await self.server.aclose()
        if self.task is not None:
            await asyncio.gather(self.task, return_exceptions=True)
        self.server = None
        self.task = None
        self.status = "stopped"
