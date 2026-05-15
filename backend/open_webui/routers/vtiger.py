import hashlib
import json
import logging
import threading
import time
import urllib.parse
from typing import Optional

import requests
import urllib3
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from open_webui.env import SRC_LOG_LEVELS
from open_webui.utils.auth import get_admin_user, get_verified_user

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MAIN"])

router = APIRouter()


# Session cache (process-local). Vtiger session tokens are short-lived; we
# re-login lazily on 401/expired-session and after a soft TTL.
_SESSION_TTL_SECONDS = 30 * 60
_session_lock = threading.Lock()
_session_state: dict = {"session_name": None, "fetched_at": 0.0, "base_url": None}


class VtigerConfigForm(BaseModel):
    ENABLE_VTIGER_CRM: Optional[bool] = None
    VTIGER_BASE_URL: Optional[str] = None
    VTIGER_USERNAME: Optional[str] = None
    VTIGER_ACCESS_KEY: Optional[str] = None
    VTIGER_VERIFY_SSL: Optional[bool] = None


def _config_snapshot(request: Request) -> dict:
    cfg = request.app.state.config
    return {
        "ENABLE_VTIGER_CRM": cfg.ENABLE_VTIGER_CRM,
        "VTIGER_BASE_URL": cfg.VTIGER_BASE_URL,
        "VTIGER_USERNAME": cfg.VTIGER_USERNAME,
        # Never echo the raw access key back to the client. Indicate presence only.
        "VTIGER_ACCESS_KEY_SET": bool(cfg.VTIGER_ACCESS_KEY),
        "VTIGER_VERIFY_SSL": cfg.VTIGER_VERIFY_SSL,
    }


def _webservice_url(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/webservice.php"


def _verify_option(verify_ssl: bool):
    if not verify_ssl:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        return False
    return True


def _login(base_url: str, username: str, access_key: str, verify_ssl: bool) -> str:
    """MD5 challenge login, returns sessionName."""
    url = _webservice_url(base_url)
    verify = _verify_option(verify_ssl)

    r = requests.get(
        url,
        params={"operation": "getchallenge", "username": username},
        verify=verify,
        timeout=15,
    )
    r.raise_for_status()
    data = r.json()
    if not data.get("success"):
        msg = (data.get("error") or {}).get("message", "challenge failed")
        raise RuntimeError(f"Vtiger challenge failed: {msg}")
    token = data["result"]["token"]

    access_hash = hashlib.md5((token + access_key).encode("utf-8")).hexdigest()
    r = requests.post(
        url,
        data={"operation": "login", "username": username, "accessKey": access_hash},
        verify=verify,
        timeout=15,
    )
    r.raise_for_status()
    data = r.json()
    if not data.get("success"):
        msg = (data.get("error") or {}).get("message", "login failed")
        raise RuntimeError(f"Vtiger login failed: {msg}")
    return data["result"]["sessionName"]


def _get_session(cfg, force_refresh: bool = False) -> str:
    base_url = (cfg.VTIGER_BASE_URL or "").strip()
    username = (cfg.VTIGER_USERNAME or "").strip()
    access_key = (cfg.VTIGER_ACCESS_KEY or "").strip()
    verify_ssl = bool(cfg.VTIGER_VERIFY_SSL)

    if not (base_url and username and access_key):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Vtiger CRM is not configured. Set base URL, username, and access key.",
        )

    with _session_lock:
        now = time.time()
        cached = _session_state.get("session_name")
        same_url = _session_state.get("base_url") == base_url
        fresh = (now - _session_state.get("fetched_at", 0)) < _SESSION_TTL_SECONDS
        if cached and same_url and fresh and not force_refresh:
            return cached

        session_name = _login(base_url, username, access_key, verify_ssl)
        _session_state.update(
            {
                "session_name": session_name,
                "fetched_at": now,
                "base_url": base_url,
            }
        )
        return session_name


def _is_session_error(payload: dict) -> bool:
    if payload.get("success"):
        return False
    code = ((payload.get("error") or {}).get("code") or "").upper()
    return code in {
        "INVALID_SESSIONID",
        "AUTHENTICATION_REQUIRED",
        "SESSION_EXPIRED",
    }


def _query(cfg, query_sql: str) -> list:
    """Run a Vtiger query, transparently re-logging in on session expiry."""
    base_url = cfg.VTIGER_BASE_URL.rstrip("/")
    verify = _verify_option(bool(cfg.VTIGER_VERIFY_SSL))

    for attempt in (1, 2):
        session_name = _get_session(cfg, force_refresh=(attempt == 2))
        url = (
            f"{_webservice_url(base_url)}"
            f"?operation=query&sessionName={session_name}"
            f"&query={urllib.parse.quote(query_sql)}"
        )
        r = requests.get(url, verify=verify, timeout=20)
        r.raise_for_status()
        payload = r.json()

        if payload.get("success"):
            result = payload.get("result") or []
            return result if isinstance(result, list) else [result]

        if _is_session_error(payload) and attempt == 1:
            continue

        msg = (payload.get("error") or {}).get("message", "Vtiger query failed")
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=msg)

    return []


def _retrieve(cfg, lead_id: str) -> dict:
    base_url = cfg.VTIGER_BASE_URL.rstrip("/")
    verify = _verify_option(bool(cfg.VTIGER_VERIFY_SSL))

    for attempt in (1, 2):
        session_name = _get_session(cfg, force_refresh=(attempt == 2))
        r = requests.get(
            _webservice_url(base_url),
            params={
                "operation": "retrieve",
                "sessionName": session_name,
                "id": lead_id,
            },
            verify=verify,
            timeout=20,
        )
        r.raise_for_status()
        payload = r.json()

        if payload.get("success"):
            return payload.get("result") or {}

        if _is_session_error(payload) and attempt == 1:
            continue

        msg = (payload.get("error") or {}).get("message", "Vtiger retrieve failed")
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=msg)

    return {}


def _escape_sql(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _require_enabled(request: Request):
    if not request.app.state.config.ENABLE_VTIGER_CRM:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Vtiger CRM integration is disabled.",
        )


############################
# Config
############################


@router.get("/config")
async def get_vtiger_config(request: Request, user=Depends(get_admin_user)):
    return _config_snapshot(request)


@router.post("/config/update")
async def update_vtiger_config(
    request: Request,
    form_data: VtigerConfigForm,
    user=Depends(get_admin_user),
):
    cfg = request.app.state.config

    if form_data.ENABLE_VTIGER_CRM is not None:
        cfg.ENABLE_VTIGER_CRM = form_data.ENABLE_VTIGER_CRM
    if form_data.VTIGER_BASE_URL is not None:
        cfg.VTIGER_BASE_URL = form_data.VTIGER_BASE_URL.strip()
    if form_data.VTIGER_USERNAME is not None:
        cfg.VTIGER_USERNAME = form_data.VTIGER_USERNAME.strip()
    if form_data.VTIGER_ACCESS_KEY is not None:
        cfg.VTIGER_ACCESS_KEY = form_data.VTIGER_ACCESS_KEY.strip()
    if form_data.VTIGER_VERIFY_SSL is not None:
        cfg.VTIGER_VERIFY_SSL = form_data.VTIGER_VERIFY_SSL

    # Invalidate cached session: credentials/URL may have changed.
    with _session_lock:
        _session_state.update(
            {"session_name": None, "fetched_at": 0.0, "base_url": None}
        )

    return _config_snapshot(request)


@router.post("/config/test")
async def test_vtiger_config(request: Request, user=Depends(get_admin_user)):
    """Force a fresh login to verify credentials work."""
    try:
        _get_session(request.app.state.config, force_refresh=True)
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        log.exception("Vtiger config test failed")
        return {"ok": False, "error": str(e)}


############################
# Leads
############################


def _lead_summary(row: dict) -> dict:
    """Project a Vtiger lead row to the fields the picker actually shows."""
    return {
        "id": row.get("id", ""),
        "firstname": row.get("firstname", "") or "",
        "lastname": row.get("lastname", "") or "",
        "company": row.get("company", "") or "",
        "designation": row.get("designation", "") or "",
        "email": row.get("email", "") or "",
        "phone": row.get("phone", "") or row.get("mobile", "") or "",
        "industry": row.get("industry", "") or "",
        "city": row.get("city", "") or "",
        "country": row.get("country", "") or "",
    }


@router.get("/leads/search")
async def search_leads(
    request: Request,
    q: str = "",
    limit: int = 20,
    offset: int = 0,
    user=Depends(get_verified_user),
):
    _require_enabled(request)

    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    cfg = request.app.state.config

    select_fields = (
        "id, firstname, lastname, company, designation, email, phone, "
        "mobile, industry, city, country"
    )
    where = ""
    q_stripped = (q or "").strip()
    if q_stripped:
        like = f"%{_escape_sql(q_stripped)}%"
        # Vtiger SQL-like syntax supports LIKE with %.
        conds = [
            f"firstname LIKE '{like}'",
            f"lastname LIKE '{like}'",
            f"company LIKE '{like}'",
            f"email LIKE '{like}'",
        ]
        where = " WHERE " + " OR ".join(conds)

    query_sql = (
        f"SELECT {select_fields} FROM Leads{where} "
        f"LIMIT {offset}, {limit};"
    )
    rows = _query(cfg, query_sql)
    leads = [_lead_summary(r) for r in rows if isinstance(r, dict)]
    return {
        "leads": leads,
        "limit": limit,
        "offset": offset,
        "has_more": len(leads) >= limit,
    }


@router.get("/leads/{lead_id}")
async def get_lead(
    request: Request,
    lead_id: str,
    user=Depends(get_verified_user),
):
    _require_enabled(request)
    cfg = request.app.state.config
    lead = _retrieve(cfg, lead_id)
    if not lead:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found")
    # Return the full lead — the picker uses description/website/etc. to build
    # the scenario background, which the summary view doesn't carry.
    return lead
