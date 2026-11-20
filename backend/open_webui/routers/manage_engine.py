"""Admin settings for the ManageEngine ServiceDesk Plus chat tools.

The chat tools themselves live in open_webui/tools/builtin.py and are injected when the
user switches "ManageEngine" on in the chat input's + menu (features.manage_engine).
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from pydantic import BaseModel

from open_webui.models.config import Config
from open_webui.utils.auth import get_admin_user
from open_webui.utils.manage_engine import (
    CONFIG_KEYS,
    ManageEngineClient,
    ManageEngineError,
    load_settings,
)

log = logging.getLogger(__name__)

router = APIRouter()


class ManageEngineConfigForm(BaseModel):
    MANAGE_ENGINE_ENDPOINT: Optional[str] = None
    MANAGE_ENGINE_API_KEY: Optional[str] = None
    MANAGE_ENGINE_VERIFY_SSL: Optional[bool] = None
    MANAGE_ENGINE_TIMEZONE: Optional[str] = None
    MANAGE_ENGINE_ADMIN_ONLY: Optional[bool] = None


FORM_KEYS = {
    'MANAGE_ENGINE_ENDPOINT': CONFIG_KEYS['endpoint'],
    'MANAGE_ENGINE_API_KEY': CONFIG_KEYS['api_key'],
    'MANAGE_ENGINE_VERIFY_SSL': CONFIG_KEYS['verify_ssl'],
    'MANAGE_ENGINE_TIMEZONE': CONFIG_KEYS['timezone'],
    'MANAGE_ENGINE_ADMIN_ONLY': CONFIG_KEYS['admin_only'],
}


async def _snapshot() -> dict:
    settings = await load_settings()
    return {
        'MANAGE_ENGINE_ENDPOINT': settings['endpoint'],
        # The API key is write-only: report whether one is saved, never its value.
        'MANAGE_ENGINE_API_KEY_SET': bool(settings['api_key']),
        'MANAGE_ENGINE_VERIFY_SSL': settings['verify_ssl'],
        'MANAGE_ENGINE_TIMEZONE': settings['timezone'],
        'MANAGE_ENGINE_ADMIN_ONLY': settings['admin_only'],
    }


@router.get('/config')
async def get_manage_engine_config(user=Depends(get_admin_user)):
    return await _snapshot()


def _validated_updates(form_data: ManageEngineConfigForm) -> dict:
    updates = {}
    for field, value in form_data.model_dump(exclude_none=True).items():
        if isinstance(value, str):
            value = value.strip()
        if field == 'MANAGE_ENGINE_API_KEY' and not value:
            continue  # blank means "keep the saved key"
        if field == 'MANAGE_ENGINE_ENDPOINT' and value:
            url = urlsplit(value)
            if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password or url.query or url.fragment:
                raise HTTPException(400, 'Use an HTTP(S) endpoint without credentials, query or fragment')
        if field == 'MANAGE_ENGINE_TIMEZONE':
            try:
                ZoneInfo(value)
            except (ZoneInfoNotFoundError, ValueError):
                raise HTTPException(400, 'Invalid IANA timezone')
        updates[FORM_KEYS[field]] = value
    return updates


@router.post('/config/update')
async def update_manage_engine_config(form_data: ManageEngineConfigForm, user=Depends(get_admin_user)):
    updates = _validated_updates(form_data)
    if updates:
        await Config.upsert(updates)
    return await _snapshot()


@router.post('/config/test')
async def test_manage_engine_config(
    form_data: Optional[ManageEngineConfigForm] = None, user=Depends(get_admin_user)
):
    settings = await load_settings()
    if form_data is not None:
        updates = _validated_updates(form_data)
        settings.update({name: updates[key] for name, key in CONFIG_KEYS.items() if key in updates})
    try:
        # Test unsaved inputs in memory; a blank API key reuses the saved secret.
        client = ManageEngineClient(settings)
        total = await client.test_connection()
        return {'ok': True, 'total_requests': total}
    except ManageEngineError as e:
        return {'ok': False, 'error': str(e)}
    except Exception as e:
        log.exception('ManageEngine connection test failed')
        return {'ok': False, 'error': str(e)}
