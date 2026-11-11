from fastapi import APIRouter, Depends, HTTPException, Query, Request

from open_webui.utils.auth import get_verified_user
from open_webui.utils.big_events import get_big_events_payload, refresh_big_events

router = APIRouter()


@router.get("")
async def get_big_events(
    source_type: str | None = Query(default=None), user=Depends(get_verified_user)
):
    return get_big_events_payload(source_type=source_type)


@router.post("/refresh")
async def refresh_discovered_big_events(
    request: Request, user=Depends(get_verified_user)
):
    try:
        return await refresh_big_events(request, user=user)
    except Exception as error:
        raise HTTPException(status_code=502, detail=str(error)[:2000]) from error
