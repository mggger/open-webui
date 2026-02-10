import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from open_webui.env import SRC_LOG_LEVELS
from open_webui.utils.auth import get_admin_user, get_verified_user
from open_webui.socket.main import get_event_emitter
from open_webui.retrieval.deep_search import generate_deep_search_report

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MAIN"])

router = APIRouter()


class DeepSearchConfigForm(BaseModel):
    ENABLE_DEEP_SEARCH: Optional[bool] = None
    SERPAPI_API_KEY: Optional[str] = None
    SERPAPI_ENGINE: Optional[str] = None
    DEEP_SEARCH_DEPTH: Optional[int] = None
    DEEP_SEARCH_BREADTH: Optional[int] = None
    DEEP_SEARCH_RESULT_COUNT: Optional[int] = None
    DEEP_SEARCH_CONCURRENCY: Optional[int] = None


class DeepSearchReportForm(BaseModel):
    query: str
    model: str
    messages: Optional[list[dict]] = None
    depth: Optional[int] = None
    breadth: Optional[int] = None
    chat_id: Optional[str] = None
    message_id: Optional[str] = None
    session_id: Optional[str] = None


@router.get("/config")
async def get_deep_search_config(request: Request, user=Depends(get_admin_user)):
    return {
        "ENABLE_DEEP_SEARCH": request.app.state.config.ENABLE_DEEP_SEARCH,
        "SERPAPI_API_KEY": request.app.state.config.SERPAPI_API_KEY,
        "SERPAPI_ENGINE": request.app.state.config.SERPAPI_ENGINE,
        "DEEP_SEARCH_DEPTH": request.app.state.config.DEEP_SEARCH_DEPTH,
        "DEEP_SEARCH_BREADTH": request.app.state.config.DEEP_SEARCH_BREADTH,
        "DEEP_SEARCH_RESULT_COUNT": request.app.state.config.DEEP_SEARCH_RESULT_COUNT,
        "DEEP_SEARCH_CONCURRENCY": request.app.state.config.DEEP_SEARCH_CONCURRENCY,
    }


@router.post("/config/update")
async def update_deep_search_config(
    request: Request, form_data: DeepSearchConfigForm, user=Depends(get_admin_user)
):
    if form_data.ENABLE_DEEP_SEARCH is not None:
        request.app.state.config.ENABLE_DEEP_SEARCH = form_data.ENABLE_DEEP_SEARCH
    if form_data.SERPAPI_API_KEY is not None:
        request.app.state.config.SERPAPI_API_KEY = form_data.SERPAPI_API_KEY
    if form_data.SERPAPI_ENGINE is not None:
        request.app.state.config.SERPAPI_ENGINE = form_data.SERPAPI_ENGINE
    if form_data.DEEP_SEARCH_DEPTH is not None:
        request.app.state.config.DEEP_SEARCH_DEPTH = max(1, form_data.DEEP_SEARCH_DEPTH)
    if form_data.DEEP_SEARCH_BREADTH is not None:
        request.app.state.config.DEEP_SEARCH_BREADTH = max(
            1, form_data.DEEP_SEARCH_BREADTH
        )
    if form_data.DEEP_SEARCH_RESULT_COUNT is not None:
        request.app.state.config.DEEP_SEARCH_RESULT_COUNT = max(
            1, form_data.DEEP_SEARCH_RESULT_COUNT
        )
    if form_data.DEEP_SEARCH_CONCURRENCY is not None:
        request.app.state.config.DEEP_SEARCH_CONCURRENCY = max(
            1, form_data.DEEP_SEARCH_CONCURRENCY
        )
    return {
        "ENABLE_DEEP_SEARCH": request.app.state.config.ENABLE_DEEP_SEARCH,
        "SERPAPI_API_KEY": request.app.state.config.SERPAPI_API_KEY,
        "SERPAPI_ENGINE": request.app.state.config.SERPAPI_ENGINE,
        "DEEP_SEARCH_DEPTH": request.app.state.config.DEEP_SEARCH_DEPTH,
        "DEEP_SEARCH_BREADTH": request.app.state.config.DEEP_SEARCH_BREADTH,
        "DEEP_SEARCH_RESULT_COUNT": request.app.state.config.DEEP_SEARCH_RESULT_COUNT,
        "DEEP_SEARCH_CONCURRENCY": request.app.state.config.DEEP_SEARCH_CONCURRENCY,
    }


@router.post("/report")
async def generate_report(
    request: Request, form_data: DeepSearchReportForm, user=Depends(get_verified_user)
):
    if not request.app.state.config.ENABLE_DEEP_SEARCH:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Deep search is disabled",
        )

    if user.role != "admin" and not user.permissions.get("features", {}).get(
        "web_search", False
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient permissions to use deep search",
        )

    if not request.app.state.config.SERPAPI_API_KEY:
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

    depth = form_data.depth or request.app.state.config.DEEP_SEARCH_DEPTH
    breadth = form_data.breadth or request.app.state.config.DEEP_SEARCH_BREADTH

    event_emitter = None
    if form_data.chat_id and form_data.message_id:
        event_emitter = get_event_emitter(
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
        messages=form_data.messages,
        depth=depth,
        breadth=breadth,
        result_count=request.app.state.config.DEEP_SEARCH_RESULT_COUNT,
        concurrency=request.app.state.config.DEEP_SEARCH_CONCURRENCY,
        on_progress=event_emitter,
    )

    return PlainTextResponse(result.get("report", ""))
