"""Authenticated, bounded diagnostics for client-side report exports."""

import json
import logging
import re
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from open_webui.utils.auth import get_verified_user

log = logging.getLogger(__name__)
router = APIRouter()


class ReportMessage(BaseModel):
    role: Literal['system', 'user']
    content: str = Field(min_length=1, max_length=500000)


class ReportCompletionForm(BaseModel):
    model: str = Field(min_length=1, max_length=256)
    messages: list[ReportMessage] = Field(min_length=1, max_length=2)
    stream: Literal[False] = False


@router.post('/chat/completions')
async def report_completion(request: Request, form_data: ReportCompletionForm, user=Depends(get_verified_user)):
    # Export subtasks have their own output contract. A saved model prompt such as
    # "always return a complete SOP" must not be appended to layout/repair requests.
    # Model access checks and configured inlet filters still apply.
    from open_webui.routers.pipelines import process_pipeline_inlet_filter
    from open_webui.config import BYPASS_ADMIN_ACCESS_CONTROL
    from open_webui.env import BYPASS_MODEL_ACCESS_CONTROL
    from open_webui.utils.chat import generate_chat_completion
    from open_webui.utils.models import check_model_access, get_all_models

    if not request.app.state.MODELS:
        await get_all_models(request, user=user)
    model = request.app.state.MODELS.get(form_data.model)
    if model is None:
        raise HTTPException(status_code=404, detail='Report model not found')
    # Match the chat entry point: provider models need not have a workspace
    # database row when the existing admin/global access-control bypass applies.
    if not BYPASS_MODEL_ACCESS_CONTROL and (user.role != 'admin' or not BYPASS_ADMIN_ACCESS_CONTROL):
        try:
            await check_model_access(user, model)
        except HTTPException:
            raise
        except Exception as error:
            if str(error) != 'Model not found':
                raise
            log.warning('report_export model unavailable: user_id=%r model_id=%r', user.id, form_data.model)
            raise HTTPException(status_code=404, detail='Report model not found or access denied') from error
    payload = form_data.model_dump()
    payload = await process_pipeline_inlet_filter(request, payload, user, request.app.state.MODELS)
    return await generate_chat_completion(request, payload, user, bypass_system_prompt=True)


class ReportDiagnosticForm(BaseModel):
    traceId: str = Field(min_length=1, max_length=80, pattern=r'^[a-zA-Z0-9_-]+$')
    event: Literal['failed', 'retry']
    stage: str = Field(min_length=1, max_length=80, pattern=r'^[a-zA-Z0-9_-]+$')
    message: str = Field(max_length=1200)
    code: str = Field(max_length=80)
    model: str | None = Field(default=None, max_length=256)
    requestId: str | None = Field(default=None, max_length=256)
    status: int | None = Field(default=None, ge=100, le=599)
    section: int | None = Field(default=None, ge=1, le=1000)
    attempt: int | None = Field(default=None, ge=1, le=1000)
    elapsedMs: int | None = Field(default=None, ge=0)


@router.post('/diagnostics')
async def report_diagnostic(form_data: ReportDiagnosticForm, user=Depends(get_verified_user)):
    diagnostic = form_data.model_dump(exclude_none=True)
    # Never accept prompts, conversation bodies, generated reports or credential fields.
    message = re.sub(r'Bearer\s+\S+', 'Bearer [redacted]', diagnostic['message'], flags=re.I)
    message = re.sub(r'\bsk-[\w-]+', '[redacted]', message)
    diagnostic['message'] = message
    diagnostic['user_id'] = user.id
    log.warning('report_export %s', json.dumps(diagnostic, ensure_ascii=True))
    return {'ok': True, 'traceId': form_data.traceId}
