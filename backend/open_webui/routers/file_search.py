import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from open_webui.env import SRC_LOG_LEVELS
from open_webui.models.file_search import FileSearchCredentials
from open_webui.utils.auth import get_verified_user
from open_webui.utils.file_search import (
    clear_file_search_cache,
    decrypt_password,
    encrypt_password,
    get_user_store,
    list_cached_directories,
    runtime_config,
    validate_relative_directory,
)


log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MAIN"])
router = APIRouter()


class FileSearchConfigForm(BaseModel):
    username: str = Field(min_length=1, max_length=320)
    password: Optional[str] = Field(default=None, max_length=2048)
    default_directory: str = Field(default="", max_length=2048)


class FileSearchConnectionForm(BaseModel):
    username: str = Field(min_length=1, max_length=320)
    password: Optional[str] = Field(default=None, max_length=2048)


def _public_config(user_id: str) -> dict:
    config = runtime_config()
    credential = FileSearchCredentials.get_by_user_id(user_id)
    return {
        "configured": credential is not None,
        "server": config.server,
        "share": config.share,
        "root": config.root,
        "username": credential.username if credential else "",
        "password_configured": credential is not None,
        "default_directory": credential.default_directory if credential else "",
    }


def _resolve_password(user_id: str, password: Optional[str]) -> str:
    if password:
        return password
    existing = FileSearchCredentials.get_by_user_id(user_id)
    if existing:
        return decrypt_password(existing.encrypted_password)
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Password is required when configuring File Search Agent",
    )


def _normalize_username(username: str) -> str:
    value = username.strip()
    if not value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username is required",
        )
    return value


async def _validate_connection(
    username: str, password: str, directory: str = ""
) -> None:
    from open_webui.utils.file_search import SMBFileSearchStore

    store = SMBFileSearchStore(runtime_config(), username, password)
    try:
        await asyncio.to_thread(store.check_access, directory)
    except Exception as exc:
        log.warning("File Search Agent connection validation failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unable to authenticate or access the SMB directory",
        ) from exc
    finally:
        store.close()


@router.get("/config")
async def get_config(user=Depends(get_verified_user)):
    return _public_config(user.id)


@router.post("/test-connection")
async def test_connection(
    form_data: FileSearchConnectionForm, user=Depends(get_verified_user)
):
    password = _resolve_password(user.id, form_data.password)
    await _validate_connection(_normalize_username(form_data.username), password)
    return {"success": True}


@router.put("/config")
async def update_config(
    form_data: FileSearchConfigForm, user=Depends(get_verified_user)
):
    try:
        default_directory = validate_relative_directory(form_data.default_directory)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    password = _resolve_password(user.id, form_data.password)
    username = _normalize_username(form_data.username)
    await _validate_connection(username, password, default_directory)

    FileSearchCredentials.upsert(
        user_id=user.id,
        username=username,
        encrypted_password=encrypt_password(password),
        default_directory=default_directory,
    )
    clear_file_search_cache(user.id)
    return _public_config(user.id)


@router.delete("/config")
async def delete_config(user=Depends(get_verified_user)):
    deleted = FileSearchCredentials.delete_by_user_id(user.id)
    clear_file_search_cache(user.id)
    return {"deleted": deleted}


@router.get("/directories")
async def list_directories(
    path: str = Query(default="", max_length=2048),
    user=Depends(get_verified_user),
):
    try:
        current = validate_relative_directory(path)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    store, credential = get_user_store(user.id)
    try:
        directories = await asyncio.to_thread(
            list_cached_directories,
            store,
            user.id,
            credential.updated_at,
            current,
        )
    except Exception as exc:
        log.warning("File Search Agent directory listing failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Directory is not accessible with the configured account",
        ) from exc
    finally:
        store.close()

    parent = str(current.rsplit("\\", 1)[0]) if "\\" in current else ""
    return {
        "current": current,
        "parent": parent,
        "directories": directories,
        "default_directory": credential.default_directory,
    }
