import asyncio
import hashlib
import json
import logging
from typing import Optional

import aiohttp
from aiocache import cached
import requests

from azure.identity import DefaultAzureCredential, get_bearer_token_provider

from fastapi import Depends, HTTPException, Request, APIRouter
from fastapi.responses import (
    FileResponse,
    StreamingResponse,
    JSONResponse,
    PlainTextResponse,
)
from pydantic import BaseModel
from starlette.background import BackgroundTask

from open_webui.models.models import Models
from open_webui.config import (
    CACHE_DIR,
)
from open_webui.env import (
    MODELS_CACHE_TTL,
    AIOHTTP_CLIENT_SESSION_SSL,
    AIOHTTP_CLIENT_TIMEOUT,
    AIOHTTP_CLIENT_TIMEOUT_MODEL_LIST,
    ENABLE_FORWARD_USER_INFO_HEADERS,
    BYPASS_MODEL_ACCESS_CONTROL,
)
from open_webui.models.users import UserModel

from open_webui.constants import ERROR_MESSAGES
from open_webui.env import SRC_LOG_LEVELS


from open_webui.utils.payload import (
    apply_model_params_to_body_openai,
    apply_system_prompt_to_body,
)
from open_webui.utils.misc import (
    convert_logit_bias_input_to_json,
    stream_chunks_handler,
)
from open_webui.utils.context_window import (
    apply_context_budget,
    count_messages_tokens,
    format_truncation_notice,
)

from open_webui.utils.auth import get_admin_user, get_verified_user
from open_webui.utils.access_control import has_access
from open_webui.utils.headers import include_user_info_headers


log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["OPENAI"])


##########################################
#
# Utility functions
#
##########################################


async def send_get_request(url, key=None, user: UserModel = None):
    timeout = aiohttp.ClientTimeout(total=AIOHTTP_CLIENT_TIMEOUT_MODEL_LIST)
    try:
        async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
            headers = {
                **({"Authorization": f"Bearer {key}"} if key else {}),
            }

            if ENABLE_FORWARD_USER_INFO_HEADERS and user:
                headers = include_user_info_headers(headers, user)

            async with session.get(
                url,
                headers=headers,
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
            ) as response:
                return await response.json()
    except Exception as e:
        # Handle connection error here
        log.error(f"Connection error: {e}")
        return None


async def cleanup_response(
    response: Optional[aiohttp.ClientResponse],
    session: Optional[aiohttp.ClientSession],
):
    if response:
        response.close()
    if session:
        await session.close()


def openai_reasoning_model_handler(payload):
    """
    Handle reasoning model specific parameters
    """
    if "max_tokens" in payload:
        # Convert "max_tokens" to "max_completion_tokens" for all reasoning models
        payload["max_completion_tokens"] = payload["max_tokens"]
        del payload["max_tokens"]

    # Handle system role conversion based on model type
    if payload["messages"][0]["role"] == "system":
        model_lower = payload["model"].lower()
        # Legacy models use "user" role instead of "system"
        if model_lower.startswith("o1-mini") or model_lower.startswith("o1-preview"):
            payload["messages"][0]["role"] = "user"
        else:
            payload["messages"][0]["role"] = "developer"

    return payload


async def get_headers_and_cookies(
    request: Request,
    url,
    key=None,
    config=None,
    metadata: Optional[dict] = None,
    user: UserModel = None,
):
    cookies = {}
    headers = {
        "Content-Type": "application/json",
        **(
            {
                "HTTP-Referer": "https://openwebui.com/",
                "X-Title": "Open WebUI",
            }
            if "openrouter.ai" in url
            else {}
        ),
    }

    if ENABLE_FORWARD_USER_INFO_HEADERS and user:
        headers = include_user_info_headers(headers, user)
        if metadata and metadata.get("chat_id"):
            headers["X-OpenWebUI-Chat-Id"] = metadata.get("chat_id")

    token = None
    auth_type = config.get("auth_type")

    if auth_type == "bearer" or auth_type is None:
        # Default to bearer if not specified
        token = f"{key}"
    elif auth_type == "none":
        token = None
    elif auth_type == "session":
        cookies = request.cookies
        token = request.state.token.credentials
    elif auth_type == "system_oauth":
        cookies = request.cookies

        oauth_token = None
        try:
            if request.cookies.get("oauth_session_id", None):
                oauth_token = await request.app.state.oauth_manager.get_oauth_token(
                    user.id,
                    request.cookies.get("oauth_session_id", None),
                )
        except Exception as e:
            log.error(f"Error getting OAuth token: {e}")

        if oauth_token:
            token = f"{oauth_token.get('access_token', '')}"

    elif auth_type in ("azure_ad", "microsoft_entra_id"):
        token = get_microsoft_entra_id_access_token()

    if token:
        headers["Authorization"] = f"Bearer {token}"

    if config.get("headers") and isinstance(config.get("headers"), dict):
        headers = {**headers, **config.get("headers")}

    return headers, cookies


def get_microsoft_entra_id_access_token():
    """
    Get Microsoft Entra ID access token using DefaultAzureCredential for Azure OpenAI.
    Returns the token string or None if authentication fails.
    """
    try:
        token_provider = get_bearer_token_provider(
            DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default"
        )
        return token_provider()
    except Exception as e:
        log.error(f"Error getting Microsoft Entra ID access token: {e}")
        return None


##########################################
#
# API routes
#
##########################################

router = APIRouter()


@router.get("/config")
async def get_config(request: Request, user=Depends(get_admin_user)):
    return {
        "ENABLE_OPENAI_API": request.app.state.config.ENABLE_OPENAI_API,
        "OPENAI_API_BASE_URLS": request.app.state.config.OPENAI_API_BASE_URLS,
        "OPENAI_API_KEYS": request.app.state.config.OPENAI_API_KEYS,
        "OPENAI_API_CONFIGS": request.app.state.config.OPENAI_API_CONFIGS,
    }


class OpenAIConfigForm(BaseModel):
    ENABLE_OPENAI_API: Optional[bool] = None
    OPENAI_API_BASE_URLS: list[str]
    OPENAI_API_KEYS: list[str]
    OPENAI_API_CONFIGS: dict


@router.post("/config/update")
async def update_config(
    request: Request, form_data: OpenAIConfigForm, user=Depends(get_admin_user)
):
    request.app.state.config.ENABLE_OPENAI_API = form_data.ENABLE_OPENAI_API
    request.app.state.config.OPENAI_API_BASE_URLS = form_data.OPENAI_API_BASE_URLS
    request.app.state.config.OPENAI_API_KEYS = form_data.OPENAI_API_KEYS

    # Check if API KEYS length is same than API URLS length
    if len(request.app.state.config.OPENAI_API_KEYS) != len(
        request.app.state.config.OPENAI_API_BASE_URLS
    ):
        if len(request.app.state.config.OPENAI_API_KEYS) > len(
            request.app.state.config.OPENAI_API_BASE_URLS
        ):
            request.app.state.config.OPENAI_API_KEYS = (
                request.app.state.config.OPENAI_API_KEYS[
                    : len(request.app.state.config.OPENAI_API_BASE_URLS)
                ]
            )
        else:
            request.app.state.config.OPENAI_API_KEYS += [""] * (
                len(request.app.state.config.OPENAI_API_BASE_URLS)
                - len(request.app.state.config.OPENAI_API_KEYS)
            )

    request.app.state.config.OPENAI_API_CONFIGS = form_data.OPENAI_API_CONFIGS

    # Remove the API configs that are not in the API URLS
    keys = list(map(str, range(len(request.app.state.config.OPENAI_API_BASE_URLS))))
    request.app.state.config.OPENAI_API_CONFIGS = {
        key: value
        for key, value in request.app.state.config.OPENAI_API_CONFIGS.items()
        if key in keys
    }

    return {
        "ENABLE_OPENAI_API": request.app.state.config.ENABLE_OPENAI_API,
        "OPENAI_API_BASE_URLS": request.app.state.config.OPENAI_API_BASE_URLS,
        "OPENAI_API_KEYS": request.app.state.config.OPENAI_API_KEYS,
        "OPENAI_API_CONFIGS": request.app.state.config.OPENAI_API_CONFIGS,
    }


@router.post("/audio/speech")
async def speech(request: Request, user=Depends(get_verified_user)):
    idx = None
    try:
        idx = request.app.state.config.OPENAI_API_BASE_URLS.index(
            "https://api.openai.com/v1"
        )

        body = await request.body()
        name = hashlib.sha256(body).hexdigest()

        SPEECH_CACHE_DIR = CACHE_DIR / "audio" / "speech"
        SPEECH_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        file_path = SPEECH_CACHE_DIR.joinpath(f"{name}.mp3")
        file_body_path = SPEECH_CACHE_DIR.joinpath(f"{name}.json")

        # Check if the file already exists in the cache
        if file_path.is_file():
            return FileResponse(file_path)

        url = request.app.state.config.OPENAI_API_BASE_URLS[idx]
        key = request.app.state.config.OPENAI_API_KEYS[idx]
        api_config = request.app.state.config.OPENAI_API_CONFIGS.get(
            str(idx),
            request.app.state.config.OPENAI_API_CONFIGS.get(url, {}),  # Legacy support
        )

        headers, cookies = await get_headers_and_cookies(
            request, url, key, api_config, user=user
        )

        r = None
        try:
            r = requests.post(
                url=f"{url}/audio/speech",
                data=body,
                headers=headers,
                cookies=cookies,
                stream=True,
            )

            r.raise_for_status()

            # Save the streaming content to a file
            with open(file_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)

            with open(file_body_path, "w") as f:
                json.dump(json.loads(body.decode("utf-8")), f)

            # Return the saved file
            return FileResponse(file_path)

        except Exception as e:
            log.exception(e)

            detail = None
            if r is not None:
                try:
                    res = r.json()
                    if "error" in res:
                        detail = f"External: {res['error']}"
                except Exception:
                    detail = f"External: {e}"

            raise HTTPException(
                status_code=r.status_code if r else 500,
                detail=detail if detail else "Open WebUI: Server Connection Error",
            )

    except ValueError:
        raise HTTPException(status_code=401, detail=ERROR_MESSAGES.OPENAI_NOT_FOUND)


async def get_all_models_responses(request: Request, user: UserModel) -> list:
    if not request.app.state.config.ENABLE_OPENAI_API:
        return []

    # Check if API KEYS length is same than API URLS length
    num_urls = len(request.app.state.config.OPENAI_API_BASE_URLS)
    num_keys = len(request.app.state.config.OPENAI_API_KEYS)

    if num_keys != num_urls:
        # if there are more keys than urls, remove the extra keys
        if num_keys > num_urls:
            new_keys = request.app.state.config.OPENAI_API_KEYS[:num_urls]
            request.app.state.config.OPENAI_API_KEYS = new_keys
        # if there are more urls than keys, add empty keys
        else:
            request.app.state.config.OPENAI_API_KEYS += [""] * (num_urls - num_keys)

    request_tasks = []
    for idx, url in enumerate(request.app.state.config.OPENAI_API_BASE_URLS):
        if (str(idx) not in request.app.state.config.OPENAI_API_CONFIGS) and (
            url not in request.app.state.config.OPENAI_API_CONFIGS  # Legacy support
        ):
            request_tasks.append(
                send_get_request(
                    f"{url}/models",
                    request.app.state.config.OPENAI_API_KEYS[idx],
                    user=user,
                )
            )
        else:
            api_config = request.app.state.config.OPENAI_API_CONFIGS.get(
                str(idx),
                request.app.state.config.OPENAI_API_CONFIGS.get(
                    url, {}
                ),  # Legacy support
            )

            enable = api_config.get("enable", True)
            model_ids = api_config.get("model_ids", [])

            if enable:
                if len(model_ids) == 0:
                    request_tasks.append(
                        send_get_request(
                            f"{url}/models",
                            request.app.state.config.OPENAI_API_KEYS[idx],
                            user=user,
                        )
                    )
                else:
                    model_list = {
                        "object": "list",
                        "data": [
                            {
                                "id": model_id,
                                "name": model_id,
                                "owned_by": "openai",
                                "openai": {"id": model_id},
                                "urlIdx": idx,
                            }
                            for model_id in model_ids
                        ],
                    }

                    request_tasks.append(
                        asyncio.ensure_future(asyncio.sleep(0, model_list))
                    )
            else:
                request_tasks.append(asyncio.ensure_future(asyncio.sleep(0, None)))

    responses = await asyncio.gather(*request_tasks)

    for idx, response in enumerate(responses):
        if response:
            url = request.app.state.config.OPENAI_API_BASE_URLS[idx]
            api_config = request.app.state.config.OPENAI_API_CONFIGS.get(
                str(idx),
                request.app.state.config.OPENAI_API_CONFIGS.get(
                    url, {}
                ),  # Legacy support
            )

            connection_type = api_config.get("connection_type", "external")
            prefix_id = api_config.get("prefix_id", None)
            tags = api_config.get("tags", [])

            model_list = (
                response if isinstance(response, list) else response.get("data", [])
            )
            if not isinstance(model_list, list):
                # Catch non-list responses
                model_list = []

            for model in model_list:
                # Remove name key if its value is None #16689
                if "name" in model and model["name"] is None:
                    del model["name"]

                if prefix_id:
                    model["id"] = (
                        f"{prefix_id}.{model.get('id', model.get('name', ''))}"
                    )

                if tags:
                    model["tags"] = tags

                if connection_type:
                    model["connection_type"] = connection_type

    log.debug(f"get_all_models:responses() {responses}")
    return responses


async def get_filtered_models(models, user):
    # Filter models based on user access control
    filtered_models = []
    for model in models.get("data", []):
        model_info = Models.get_model_by_id(model["id"])
        if model_info:
            if user.id == model_info.user_id or has_access(
                user.id, type="read", access_control=model_info.access_control
            ):
                filtered_models.append(model)
    return filtered_models


@cached(
    ttl=MODELS_CACHE_TTL,
    key=lambda _, user: f"openai_all_models_{user.id}" if user else "openai_all_models",
)
async def get_all_models(request: Request, user: UserModel) -> dict[str, list]:
    log.info("get_all_models()")

    if not request.app.state.config.ENABLE_OPENAI_API:
        return {"data": []}

    responses = await get_all_models_responses(request, user=user)

    def extract_data(response):
        if response and "data" in response:
            return response["data"]
        if isinstance(response, list):
            return response
        return None

    def is_supported_openai_models(model_id):
        if any(
            name in model_id
            for name in [
                "babbage",
                "dall-e",
                "davinci",
                "embedding",
                "tts",
                "whisper",
            ]
        ):
            return False
        return True

    def get_merged_models(model_lists):
        log.debug(f"merge_models_lists {model_lists}")
        models = {}

        for idx, model_list in enumerate(model_lists):
            if model_list is not None and "error" not in model_list:
                for model in model_list:
                    model_id = model.get("id") or model.get("name")

                    if (
                        "api.openai.com"
                        in request.app.state.config.OPENAI_API_BASE_URLS[idx]
                        and not is_supported_openai_models(model_id)
                    ):
                        # Skip unwanted OpenAI models
                        continue

                    if model_id and model_id not in models:
                        models[model_id] = {
                            **model,
                            "name": model.get("name", model_id),
                            "owned_by": "openai",
                            "openai": model,
                            "connection_type": model.get("connection_type", "external"),
                            "urlIdx": idx,
                        }

        return models

    models = get_merged_models(map(extract_data, responses))
    log.debug(f"models: {models}")

    request.app.state.OPENAI_MODELS = models
    return {"data": list(models.values())}


@router.get("/models")
@router.get("/models/{url_idx}")
async def get_models(
    request: Request, url_idx: Optional[int] = None, user=Depends(get_verified_user)
):
    models = {
        "data": [],
    }

    if url_idx is None:
        models = await get_all_models(request, user=user)
    else:
        url = request.app.state.config.OPENAI_API_BASE_URLS[url_idx]
        key = request.app.state.config.OPENAI_API_KEYS[url_idx]

        api_config = request.app.state.config.OPENAI_API_CONFIGS.get(
            str(url_idx),
            request.app.state.config.OPENAI_API_CONFIGS.get(url, {}),  # Legacy support
        )

        r = None
        async with aiohttp.ClientSession(
            trust_env=True,
            timeout=aiohttp.ClientTimeout(total=AIOHTTP_CLIENT_TIMEOUT_MODEL_LIST),
        ) as session:
            try:
                headers, cookies = await get_headers_and_cookies(
                    request, url, key, api_config, user=user
                )

                if api_config.get("azure", False):
                    models = {
                        "data": api_config.get("model_ids", []) or [],
                        "object": "list",
                    }
                else:
                    async with session.get(
                        f"{url}/models",
                        headers=headers,
                        cookies=cookies,
                        ssl=AIOHTTP_CLIENT_SESSION_SSL,
                    ) as r:
                        if r.status != 200:
                            # Extract response error details if available
                            error_detail = f"HTTP Error: {r.status}"
                            res = await r.json()
                            if "error" in res:
                                error_detail = f"External Error: {res['error']}"
                            raise Exception(error_detail)

                        response_data = await r.json()

                        # Check if we're calling OpenAI API based on the URL
                        if "api.openai.com" in url:
                            # Filter models according to the specified conditions
                            response_data["data"] = [
                                model
                                for model in response_data.get("data", [])
                                if not any(
                                    name in model["id"]
                                    for name in [
                                        "babbage",
                                        "dall-e",
                                        "davinci",
                                        "embedding",
                                        "tts",
                                        "whisper",
                                    ]
                                )
                            ]

                        models = response_data
            except aiohttp.ClientError as e:
                # ClientError covers all aiohttp requests issues
                log.exception(f"Client error: {str(e)}")
                raise HTTPException(
                    status_code=500, detail="Open WebUI: Server Connection Error"
                )
            except Exception as e:
                log.exception(f"Unexpected error: {e}")
                error_detail = f"Unexpected error: {str(e)}"
                raise HTTPException(status_code=500, detail=error_detail)

    if user.role == "user" and not BYPASS_MODEL_ACCESS_CONTROL:
        models["data"] = await get_filtered_models(models, user)

    return models


class ConnectionVerificationForm(BaseModel):
    url: str
    key: str

    config: Optional[dict] = None


@router.post("/verify")
async def verify_connection(
    request: Request,
    form_data: ConnectionVerificationForm,
    user=Depends(get_admin_user),
):
    url = form_data.url
    key = form_data.key

    api_config = form_data.config or {}

    async with aiohttp.ClientSession(
        trust_env=True,
        timeout=aiohttp.ClientTimeout(total=AIOHTTP_CLIENT_TIMEOUT_MODEL_LIST),
    ) as session:
        try:
            headers, cookies = await get_headers_and_cookies(
                request, url, key, api_config, user=user
            )

            if api_config.get("azure", False):
                # Only set api-key header if not using Azure Entra ID authentication
                auth_type = api_config.get("auth_type", "bearer")
                if auth_type not in ("azure_ad", "microsoft_entra_id"):
                    headers["api-key"] = key

                api_version = api_config.get("api_version", "") or "2023-03-15-preview"
                async with session.get(
                    url=f"{url}/openai/models?api-version={api_version}",
                    headers=headers,
                    cookies=cookies,
                    ssl=AIOHTTP_CLIENT_SESSION_SSL,
                ) as r:
                    try:
                        response_data = await r.json()
                    except Exception:
                        response_data = await r.text()

                    if r.status != 200:
                        if isinstance(response_data, (dict, list)):
                            return JSONResponse(
                                status_code=r.status, content=response_data
                            )
                        else:
                            return PlainTextResponse(
                                status_code=r.status, content=response_data
                            )

                    return response_data
            else:
                async with session.get(
                    f"{url}/models",
                    headers=headers,
                    cookies=cookies,
                    ssl=AIOHTTP_CLIENT_SESSION_SSL,
                ) as r:
                    try:
                        response_data = await r.json()
                    except Exception:
                        response_data = await r.text()

                    if r.status != 200:
                        if isinstance(response_data, (dict, list)):
                            return JSONResponse(
                                status_code=r.status, content=response_data
                            )
                        else:
                            return PlainTextResponse(
                                status_code=r.status, content=response_data
                            )

                    return response_data

        except aiohttp.ClientError as e:
            # ClientError covers all aiohttp requests issues
            log.exception(f"Client error: {str(e)}")
            raise HTTPException(
                status_code=500, detail="Open WebUI: Server Connection Error"
            )
        except Exception as e:
            log.exception(f"Unexpected error: {e}")
            raise HTTPException(
                status_code=500, detail="Open WebUI: Server Connection Error"
            )


def get_azure_allowed_params(api_version: str) -> set[str]:
    allowed_params = {
        "messages",
        "temperature",
        "role",
        "content",
        "contentPart",
        "contentPartImage",
        "enhancements",
        "dataSources",
        "n",
        "stream",
        "stop",
        "max_tokens",
        "presence_penalty",
        "frequency_penalty",
        "logit_bias",
        "user",
        "function_call",
        "functions",
        "tools",
        "tool_choice",
        "top_p",
        "log_probs",
        "top_logprobs",
        "response_format",
        "seed",
        "max_completion_tokens",
        "reasoning_effort",
    }

    try:
        if api_version >= "2024-09-01-preview":
            allowed_params.add("stream_options")
    except ValueError:
        log.debug(
            f"Invalid API version {api_version} for Azure OpenAI. Defaulting to allowed parameters."
        )

    return allowed_params


def is_openai_reasoning_model(model: str) -> bool:
    return model.lower().startswith(("o1", "o3", "o4", "gpt-5"))


def convert_to_azure_payload(url, payload: dict, api_version: str):
    model = payload.get("model", "")

    # Filter allowed parameters based on Azure OpenAI API
    allowed_params = get_azure_allowed_params(api_version)

    # Special handling for o-series models
    if is_openai_reasoning_model(model):
        # Convert max_tokens to max_completion_tokens for o-series models
        if "max_tokens" in payload:
            payload["max_completion_tokens"] = payload["max_tokens"]
            del payload["max_tokens"]

        # Remove temperature if not 1 for o-series models
        if "temperature" in payload and payload["temperature"] != 1:
            log.debug(
                f"Removing temperature parameter for o-series model {model} as only default value (1) is supported"
            )
            del payload["temperature"]

    # Filter out unsupported parameters
    payload = {k: v for k, v in payload.items() if k in allowed_params}

    url = f"{url}/openai/deployments/{model}"
    return url, payload


def _prepend_truncation_to_stream(upstream, notice: dict, model: str):
    """Yield a synthetic SSE chunk carrying the truncation notice, then upstream."""
    text = format_truncation_notice(notice, lang="en")
    if not text:
        return upstream

    async def _gen():
        chunk = {
            "id": "ctx-trunc-notice",
            "object": "chat.completion.chunk",
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "delta": {"role": "assistant", "content": text},
                    "finish_reason": None,
                }
            ],
        }
        sse = "data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n"
        yield sse.encode("utf-8")
        async for item in upstream:
            yield item

    return _gen()


def _prepend_truncation_to_response(response: dict, notice: dict) -> None:
    """Mutate a non-streaming response so the notice is shown before content."""
    text = format_truncation_notice(notice, lang="en")
    if not text:
        return
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        return
    first = choices[0]
    msg = first.get("message") if isinstance(first, dict) else None
    if not isinstance(msg, dict):
        return
    content = msg.get("content")
    if isinstance(content, str):
        msg["content"] = text + content
    elif isinstance(content, list):
        msg["content"] = [{"type": "text", "text": text}] + content
    else:
        msg["content"] = text


def _context_output_budget(
    payload: dict, max_model_len: int, min_output_tokens: int
) -> int:
    requested_max = payload.get("max_tokens") or payload.get("max_completion_tokens")
    target_output = (
        requested_max if isinstance(requested_max, int) else min_output_tokens
    )
    return max(min_output_tokens, min(target_output, max_model_len))


def _message_text(message: dict) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        )
    return ""


def _format_messages_for_summary(messages: list[dict]) -> str:
    return "\n\n".join(
        f"{message.get('role', 'unknown').upper()}:\n{_message_text(message)}"
        for message in messages
    )


async def _summarize_context_messages(
    request_url: str,
    headers: dict,
    cookies: dict,
    model: str,
    messages: list[dict],
    max_model_len: int,
    summary_tokens: int,
    azure: bool,
    api_version: str,
) -> Optional[str]:
    summary_input = _format_messages_for_summary(messages)
    original_summary_chars = len(summary_input)
    summary_payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Compress the conversation history into a concise memory for the next "
                    "assistant response. Preserve user requirements, constraints, decisions, "
                    "facts, file names, code identifiers, and unresolved tasks. Do not answer "
                    "the user. Return only the compressed memory."
                ),
            },
            {"role": "user", "content": summary_input},
        ],
        "stream": False,
    }
    if is_openai_reasoning_model(model):
        summary_payload["max_completion_tokens"] = summary_tokens
    else:
        summary_payload["temperature"] = 0
        summary_payload["max_tokens"] = summary_tokens

    # Keep the summarization request inside the upstream context limit. If the
    # history itself is too large, summarize the most recent part of the history.
    while (
        count_messages_tokens(summary_payload["messages"]) + summary_tokens
        > max_model_len
        and len(summary_input) > 4000
    ):
        summary_input = summary_input[len(summary_input) // 4 :]
        summary_payload["messages"][1]["content"] = summary_input

    if len(summary_input) != original_summary_chars:
        log.info(
            "Context compression input trimmed for summarizer: original_chars=%d, final_chars=%d, max_model_len=%d",
            original_summary_chars,
            len(summary_input),
            max_model_len,
        )

    if azure:
        api_payload = {**summary_payload}
        compression_url, api_payload = convert_to_azure_payload(
            request_url, api_payload, api_version
        )
        compression_url = f"{compression_url}/chat/completions?api-version={api_version}"
    else:
        compression_url = f"{request_url}/chat/completions"
        api_payload = summary_payload

    timeout = aiohttp.ClientTimeout(total=AIOHTTP_CLIENT_TIMEOUT)
    async with aiohttp.ClientSession(trust_env=True, timeout=timeout) as session:
        async with session.post(
            compression_url,
            data=json.dumps(api_payload),
            headers=headers,
            cookies=cookies,
            ssl=AIOHTTP_CLIENT_SESSION_SSL,
        ) as response:
            data = await response.json()
            if response.status >= 400:
                raise RuntimeError(f"Context compression failed: {data}")

    choices = data.get("choices") if isinstance(data, dict) else None
    if not choices:
        return None
    message = choices[0].get("message", {})
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "\n".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        ).strip()
    return None


async def _compress_context_budget(
    payload: dict,
    request_url: str,
    headers: dict,
    cookies: dict,
    max_model_len: int,
    min_output_tokens: int,
    azure: bool,
    api_version: str,
) -> Optional[dict]:
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        return None

    target_output = _context_output_budget(payload, max_model_len, min_output_tokens)
    original_tokens = count_messages_tokens(messages)
    if original_tokens + target_output <= max_model_len:
        return None

    prompt_budget = max(0, max_model_len - target_output)
    summary_tokens = max(256, min(2048, prompt_budget // 4))
    system_messages = [m for m in messages if m.get("role") == "system"]
    other_messages = [m for m in messages if m.get("role") != "system"]
    if not other_messages:
        return None

    summary_placeholder = {
        "role": "system",
        "content": "Earlier conversation summary will be inserted here.",
    }
    kept_recent: list[dict] = []
    compress_all = False
    for message in reversed(other_messages):
        candidate = (
            system_messages
            + [
                {
                    **summary_placeholder,
                    "content": "Earlier conversation summary:\n"
                    + ("x" * summary_tokens * 4),
                }
            ]
            + list(reversed(kept_recent + [message]))
        )
        if count_messages_tokens(candidate) + target_output <= max_model_len:
            kept_recent.append(message)
        elif not kept_recent:
            # The newest message alone is too large. Compress the request instead
            # of hard-truncating it.
            compress_all = True
            break
        else:
            break

    kept_recent.reverse()
    compressed_messages = (
        other_messages
        if compress_all
        else other_messages[: len(other_messages) - len(kept_recent)]
    )
    if not compressed_messages:
        return None

    log.info(
        "Context window exceeded; attempting LLM compression: model=%s, original_tokens=%d, max_model_len=%d, reserved_output_tokens=%d, messages_to_compress=%d, recent_messages_preserved=%d, mode=%s",
        payload.get("model"),
        original_tokens,
        max_model_len,
        target_output,
        len(compressed_messages),
        len(kept_recent),
        "compress_current_request" if compress_all else "compress_older_history",
    )

    summary = await _summarize_context_messages(
        request_url=request_url,
        headers=headers,
        cookies=cookies,
        model=payload.get("model", ""),
        messages=compressed_messages,
        max_model_len=max_model_len,
        summary_tokens=summary_tokens,
        azure=azure,
        api_version=api_version,
    )
    if not summary:
        return None

    if compress_all:
        new_messages = system_messages + [
            {
                "role": "user",
                "content": "Compressed conversation and current user request:\n" + summary,
            }
        ]
    else:
        new_messages = system_messages + [
            {
                "role": "system",
                "content": "Earlier conversation summary:\n" + summary,
            }
        ] + kept_recent

    final_prompt_tokens = count_messages_tokens(new_messages)
    headroom = max_model_len - final_prompt_tokens
    output_budget = min(target_output, headroom)
    if output_budget <= 0:
        return None
    if final_prompt_tokens + output_budget > max_model_len:
        return None

    payload["messages"] = new_messages
    if "max_completion_tokens" in payload:
        payload["max_completion_tokens"] = output_budget
    elif "max_tokens" in payload:
        payload["max_tokens"] = output_budget

    return {
        "compressed_messages": len(compressed_messages),
        "preserved_recent_messages": len(kept_recent),
        "mode": "compress_current_request" if compress_all else "compress_older_history",
        "summary_tokens": count_messages_tokens(
            [{"role": "system", "content": summary}]
        ),
        "original_tokens": original_tokens,
        "final_tokens": final_prompt_tokens,
        "reserved_output_tokens": output_budget,
        "max_model_len": max_model_len,
    }


@router.post("/chat/completions")
async def generate_chat_completion(
    request: Request,
    form_data: dict,
    user=Depends(get_verified_user),
    bypass_filter: Optional[bool] = False,
):
    if BYPASS_MODEL_ACCESS_CONTROL:
        bypass_filter = True

    idx = 0

    payload = {**form_data}
    metadata = payload.pop("metadata", None)

    model_id = form_data.get("model")
    model_info = Models.get_model_by_id(model_id)

    # Check model info and override the payload
    if model_info:
        if model_info.base_model_id:
            payload["model"] = model_info.base_model_id
            model_id = model_info.base_model_id

        params = model_info.params.model_dump()

        if params:
            system = params.pop("system", None)

            payload = apply_model_params_to_body_openai(params, payload)
            payload = apply_system_prompt_to_body(system, payload, metadata, user)

        # Check if user has access to the model
        if not bypass_filter and user.role == "user":
            if not (
                user.id == model_info.user_id
                or has_access(
                    user.id, type="read", access_control=model_info.access_control
                )
            ):
                raise HTTPException(
                    status_code=403,
                    detail="Model not found",
                )
    elif not bypass_filter:
        if user.role != "admin":
            raise HTTPException(
                status_code=403,
                detail="Model not found",
            )

    await get_all_models(request, user=user)
    model = request.app.state.OPENAI_MODELS.get(model_id)
    if model:
        idx = model["urlIdx"]
    else:
        raise HTTPException(
            status_code=404,
            detail="Model not found",
        )

    # Get the API config for the model
    api_config = request.app.state.config.OPENAI_API_CONFIGS.get(
        str(idx),
        request.app.state.config.OPENAI_API_CONFIGS.get(
            request.app.state.config.OPENAI_API_BASE_URLS[idx], {}
        ),  # Legacy support
    )

    prefix_id = api_config.get("prefix_id", None)
    if prefix_id:
        payload["model"] = payload["model"].replace(f"{prefix_id}.", "")

    # Add user info to the payload if the model is a pipeline
    if "pipeline" in model and model.get("pipeline"):
        payload["user"] = {
            "name": user.name,
            "id": user.id,
            "email": user.email,
            "role": user.role,
        }

    url = request.app.state.config.OPENAI_API_BASE_URLS[idx]
    key = request.app.state.config.OPENAI_API_KEYS[idx]

    # Check if model is a reasoning model that needs special handling
    if is_openai_reasoning_model(payload["model"]):
        payload = openai_reasoning_model_handler(payload)
    elif "api.openai.com" not in url:
        # Remove "max_completion_tokens" from the payload for backward compatibility
        if "max_completion_tokens" in payload:
            payload["max_tokens"] = payload["max_completion_tokens"]
            del payload["max_completion_tokens"]

    if "max_tokens" in payload and "max_completion_tokens" in payload:
        del payload["max_tokens"]

    headers, cookies = await get_headers_and_cookies(
        request, url, key, api_config, metadata, user=user
    )
    if api_config.get("azure", False):
        auth_type = api_config.get("auth_type", "bearer")
        if auth_type not in ("azure_ad", "microsoft_entra_id"):
            headers["api-key"] = key

    # Enforce upstream context window (e.g. self-hosted vLLM with --max-model-len).
    # First try to compress older history into a summary while preserving recent
    # messages. If compression is unavailable or still does not fit, fall back to
    # dropping the oldest non-system messages.
    # Defaults to 32768 to match the in-house vLLM deployment; explicitly set to 0
    # in the connection config to disable.
    DEFAULT_MAX_MODEL_LEN = 32768
    truncation_notice = None
    compression_notice = None
    max_model_len = api_config.get("max_model_len", DEFAULT_MAX_MODEL_LEN)
    model_max_lens = api_config.get("model_max_lens") or {}
    if isinstance(model_max_lens, dict) and payload.get("model") in model_max_lens:
        max_model_len = model_max_lens[payload["model"]]
    try:
        max_model_len = int(max_model_len) if max_model_len is not None else 0
    except (TypeError, ValueError):
        max_model_len = 0
    if max_model_len > 0:
        min_output = int(api_config.get("min_output_tokens", 256) or 256)
        try:
            compression_notice = await _compress_context_budget(
                payload=payload,
                request_url=url,
                headers=headers,
                cookies=cookies,
                max_model_len=max_model_len,
                min_output_tokens=min_output,
                azure=api_config.get("azure", False),
                api_version=api_config.get("api_version", "2023-03-15-preview"),
            )
        except Exception as e:
            log.warning(
                "Context compression failed; falling back to context trimming: model=%s, max_model_len=%d, error=%s",
                payload.get("model"),
                max_model_len,
                e,
            )

        if compression_notice:
            log.info(
                "Context compression completed: model=%s, mode=%s, compressed_messages=%d, preserved_recent_messages=%d, original_tokens=%d, final_tokens=%d, summary_tokens=%d, reserved_output_tokens=%d, max_model_len=%d",
                payload.get("model"),
                compression_notice.get("mode"),
                compression_notice.get("compressed_messages", 0),
                compression_notice.get("preserved_recent_messages", 0),
                compression_notice.get("original_tokens", 0),
                compression_notice.get("final_tokens", 0),
                compression_notice.get("summary_tokens", 0),
                compression_notice.get("reserved_output_tokens", 0),
                compression_notice.get("max_model_len", 0),
            )
        else:
            log.warning(
                "Context compression unavailable or insufficient; applying context trimming fallback: model=%s, max_model_len=%d",
                payload.get("model"),
                max_model_len,
            )
            truncation_notice = apply_context_budget(
                payload, max_model_len=max_model_len, min_output_tokens=min_output
            )
        if truncation_notice:
            log.warning(
                "Context trimming fallback applied: model=%s, dropped_messages=%d, truncated_message=%s, original_tokens=%d, final_tokens=%d, max_model_len=%d",
                payload.get("model"),
                truncation_notice.get("dropped_messages", 0),
                truncation_notice.get("truncated_message", False),
                truncation_notice.get("original_tokens", 0),
                truncation_notice.get("final_tokens", 0),
                truncation_notice.get("max_model_len", 0),
            )

    # Convert the modified body back to JSON
    if "logit_bias" in payload:
        payload["logit_bias"] = json.loads(
            convert_logit_bias_input_to_json(payload["logit_bias"])
        )

    if api_config.get("azure", False):
        api_version = api_config.get("api_version", "2023-03-15-preview")
        request_url, payload = convert_to_azure_payload(url, payload, api_version)

        # Only set api-key header if not using Azure Entra ID authentication
        auth_type = api_config.get("auth_type", "bearer")
        if auth_type not in ("azure_ad", "microsoft_entra_id"):
            headers["api-key"] = key

        headers["api-version"] = api_version
        request_url = f"{request_url}/chat/completions?api-version={api_version}"
    else:
        request_url = f"{url}/chat/completions"

    model_for_notice = payload.get("model", "")
    payload = json.dumps(payload)

    r = None
    session = None
    streaming = False
    response = None

    try:
        session = aiohttp.ClientSession(
            trust_env=True, timeout=aiohttp.ClientTimeout(total=AIOHTTP_CLIENT_TIMEOUT)
        )

        r = await session.request(
            method="POST",
            url=request_url,
            data=payload,
            headers=headers,
            cookies=cookies,
            ssl=AIOHTTP_CLIENT_SESSION_SSL,
        )

        # Check if response is SSE
        if "text/event-stream" in r.headers.get("Content-Type", ""):
            streaming = True
            upstream = stream_chunks_handler(r.content)
            if truncation_notice:
                upstream = _prepend_truncation_to_stream(
                    upstream, truncation_notice, model_for_notice
                )
            return StreamingResponse(
                upstream,
                status_code=r.status,
                headers=dict(r.headers),
                background=BackgroundTask(
                    cleanup_response, response=r, session=session
                ),
            )
        else:
            try:
                response = await r.json()
            except Exception as e:
                log.error(e)
                response = await r.text()

            if r.status >= 400:
                if isinstance(response, (dict, list)):
                    return JSONResponse(status_code=r.status, content=response)
                else:
                    return PlainTextResponse(status_code=r.status, content=response)

            if truncation_notice and isinstance(response, dict):
                _prepend_truncation_to_response(response, truncation_notice)
            return response
    except Exception as e:
        log.exception(e)

        raise HTTPException(
            status_code=r.status if r else 500,
            detail="Open WebUI: Server Connection Error",
        )
    finally:
        if not streaming:
            await cleanup_response(r, session)


async def embeddings(request: Request, form_data: dict, user):
    """
    Calls the embeddings endpoint for OpenAI-compatible providers.

    Args:
        request (Request): The FastAPI request context.
        form_data (dict): OpenAI-compatible embeddings payload.
        user (UserModel): The authenticated user.

    Returns:
        dict: OpenAI-compatible embeddings response.
    """
    idx = 0
    # Prepare payload/body
    body = json.dumps(form_data)
    # Find correct backend url/key based on model
    await get_all_models(request, user=user)
    model_id = form_data.get("model")
    models = request.app.state.OPENAI_MODELS
    if model_id in models:
        idx = models[model_id]["urlIdx"]

    url = request.app.state.config.OPENAI_API_BASE_URLS[idx]
    key = request.app.state.config.OPENAI_API_KEYS[idx]
    api_config = request.app.state.config.OPENAI_API_CONFIGS.get(
        str(idx),
        request.app.state.config.OPENAI_API_CONFIGS.get(url, {}),  # Legacy support
    )

    r = None
    session = None
    streaming = False

    headers, cookies = await get_headers_and_cookies(
        request, url, key, api_config, user=user
    )
    try:
        session = aiohttp.ClientSession(trust_env=True)
        r = await session.request(
            method="POST",
            url=f"{url}/embeddings",
            data=body,
            headers=headers,
            cookies=cookies,
        )

        if "text/event-stream" in r.headers.get("Content-Type", ""):
            streaming = True
            return StreamingResponse(
                r.content,
                status_code=r.status,
                headers=dict(r.headers),
                background=BackgroundTask(
                    cleanup_response, response=r, session=session
                ),
            )
        else:
            try:
                response_data = await r.json()
            except Exception:
                response_data = await r.text()

            if r.status >= 400:
                if isinstance(response_data, (dict, list)):
                    return JSONResponse(status_code=r.status, content=response_data)
                else:
                    return PlainTextResponse(
                        status_code=r.status, content=response_data
                    )

            return response_data
    except Exception as e:
        log.exception(e)
        raise HTTPException(
            status_code=r.status if r else 500,
            detail="Open WebUI: Server Connection Error",
        )
    finally:
        if not streaming:
            await cleanup_response(r, session)


@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def proxy(path: str, request: Request, user=Depends(get_verified_user)):
    """
    Deprecated: proxy all requests to OpenAI API
    """

    body = await request.body()

    idx = 0
    url = request.app.state.config.OPENAI_API_BASE_URLS[idx]
    key = request.app.state.config.OPENAI_API_KEYS[idx]
    api_config = request.app.state.config.OPENAI_API_CONFIGS.get(
        str(idx),
        request.app.state.config.OPENAI_API_CONFIGS.get(
            request.app.state.config.OPENAI_API_BASE_URLS[idx], {}
        ),  # Legacy support
    )

    r = None
    session = None
    streaming = False

    try:
        headers, cookies = await get_headers_and_cookies(
            request, url, key, api_config, user=user
        )

        if api_config.get("azure", False):
            api_version = api_config.get("api_version", "2023-03-15-preview")

            # Only set api-key header if not using Azure Entra ID authentication
            auth_type = api_config.get("auth_type", "bearer")
            if auth_type not in ("azure_ad", "microsoft_entra_id"):
                headers["api-key"] = key

            headers["api-version"] = api_version

            payload = json.loads(body)
            url, payload = convert_to_azure_payload(url, payload, api_version)
            body = json.dumps(payload).encode()

            request_url = f"{url}/{path}?api-version={api_version}"
        else:
            request_url = f"{url}/{path}"

        session = aiohttp.ClientSession(trust_env=True)
        r = await session.request(
            method=request.method,
            url=request_url,
            data=body,
            headers=headers,
            cookies=cookies,
            ssl=AIOHTTP_CLIENT_SESSION_SSL,
        )

        # Check if response is SSE
        if "text/event-stream" in r.headers.get("Content-Type", ""):
            streaming = True
            return StreamingResponse(
                r.content,
                status_code=r.status,
                headers=dict(r.headers),
                background=BackgroundTask(
                    cleanup_response, response=r, session=session
                ),
            )
        else:
            try:
                response_data = await r.json()
            except Exception:
                response_data = await r.text()

            if r.status >= 400:
                if isinstance(response_data, (dict, list)):
                    return JSONResponse(status_code=r.status, content=response_data)
                else:
                    return PlainTextResponse(
                        status_code=r.status, content=response_data
                    )

            return response_data

    except Exception as e:
        log.exception(e)
        raise HTTPException(
            status_code=r.status if r else 500,
            detail="Open WebUI: Server Connection Error",
        )
    finally:
        if not streaming:
            await cleanup_response(r, session)
