# Brain Agent MVP

This service uses the local LiveKit Agents SDK at `/Users/jiamin/git/agents` and connects
directly to internal STT, LLM, and TTS model endpoints. It never calls Open WebUI's audio or
chat proxy APIs.

## Run

1. Copy `.env.example` to `.env` and configure the Open WebUI runtime-config URL.
2. Start a LiveKit server with the same URL/key/secret configured in Open WebUI.
3. From this directory run `uv sync`, then `uv run python brain_agent.py dev`.
4. Configure LiveKit and select a language model in Open WebUI Admin Settings > Brain.
5. Open `/brain`.

Brain inherits STT and TTS from Open WebUI's Audio settings and connects directly to those model
endpoints. The MVP expects OpenAI-compatible transcription and speech engines; it does not call
Open WebUI's audio proxy APIs.

The language model is selected in Brain settings. Its direct URL and credentials are inherited
from the corresponding OpenAI-compatible Connection.
