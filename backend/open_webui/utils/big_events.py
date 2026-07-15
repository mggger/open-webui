import asyncio
import logging
import os

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Request

log = logging.getLogger(__name__)
log.setLevel(logging.INFO)

DEFAULT_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
BIG_EVENTS_CACHE_DIR = Path(os.getenv("DATA_DIR", DEFAULT_DATA_DIR)).resolve() / "cache"
BIG_EVENTS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
LOCK_FILE = BIG_EVENTS_CACHE_DIR / "big-events-discovery.lock"
DISCOVERY_INTERVAL = max(
    3600, int(os.getenv("BIG_EVENTS_DISCOVERY_INTERVAL_SECONDS", "86400"))
)
DISCOVERY_ENABLED = os.getenv("ENABLE_BIG_EVENTS_DISCOVERY", "True").lower() == "true"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _epoch_now() -> int:
    return int(_utc_now().timestamp())


DISCOVERY_ENGINE = "multi-source+llm"


def get_big_events_payload(source_type: str | None = None) -> dict:
    from open_webui.models.big_events import BigEvents

    return {
        "events": BigEvents.get_upcoming(source_type=source_type),
        **BigEvents.get_state(),
    }


def _acquire_lock() -> int | None:
    try:
        if (
            LOCK_FILE.exists()
            and _utc_now().timestamp() - LOCK_FILE.stat().st_mtime > 7200
        ):
            LOCK_FILE.unlink(missing_ok=True)
        return os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return None


async def refresh_big_events(request: Request, user=None) -> dict:
    from open_webui.utils.executive_event_sources import (
        MANAGED_SOURCE_TYPES,
        crawl_event_sources,
    )

    lock = _acquire_lock()
    if lock is None:
        return get_big_events_payload()

    try:
        from open_webui.models.big_events import BigEvents

        if user is None:
            from open_webui.models.users import Users

            admin_users = Users.get_users(filter={"roles": ["admin"]}, limit=1)
            user = next(iter(admin_users["users"]), None)

        crawl_result = await crawl_event_sources()
        if not crawl_result.events:
            details = "; ".join(
                f"{source}: {error}" for source, error in crawl_result.errors.items()
            )
            raise RuntimeError(f"No event source returned candidates. {details}".strip())

        from open_webui.utils.big_event_llm import classify_events

        events = await classify_events(request, user, crawl_result.events)

        now = _epoch_now()
        BigEvents.upsert_discovered(events)
        BigEvents.delete_legacy_sources()
        BigEvents.delete_stale_managed_sources(MANAGED_SOURCE_TYPES)
        BigEvents.delete_expired_discovered(MANAGED_SOURCE_TYPES)
        partial_error = (
            "; ".join(
                f"{source}: {error}" for source, error in crawl_result.errors.items()
            )[:2000]
            or None
        )
        BigEvents.update_state(
            last_success=now,
            last_attempt=now,
            error=partial_error,
            engine=DISCOVERY_ENGINE,
        )
        payload = get_big_events_payload()
        payload["discovery"] = {
            "candidateCount": len(crawl_result.events),
            "acceptedCount": len(events),
            "crawledBySource": crawl_result.counts,
            "acceptedBySource": dict(
                Counter(event.get("sourceType", "unknown") for event in events)
            ),
            "sourceErrors": crawl_result.errors,
        }
        return payload
    except Exception as error:
        from open_webui.models.big_events import BigEvents

        previous_state = BigEvents.get_state()
        BigEvents.update_state(
            last_success=(
                previous_state.get("lastSuccess")
                if previous_state.get("engine") == DISCOVERY_ENGINE
                else None
            ),
            last_attempt=_epoch_now(),
            error=str(error)[:2000],
            engine=DISCOVERY_ENGINE,
        )
        raise
    finally:
        os.close(lock)
        LOCK_FILE.unlink(missing_ok=True)


def discovery_is_stale(state: dict) -> bool:
    if state.get("engine") != DISCOVERY_ENGINE:
        return True
    try:
        return _epoch_now() - int(state["lastSuccess"]) > DISCOVERY_INTERVAL
    except (KeyError, TypeError, ValueError):
        return True


def seconds_until_next_discovery(state: dict) -> int:
    try:
        due_at = int(state["lastSuccess"]) + DISCOVERY_INTERVAL
        return min(DISCOVERY_INTERVAL, max(1, due_at - _epoch_now()))
    except (KeyError, TypeError, ValueError):
        return 1


async def big_events_discovery_loop(app) -> None:
    while True:
        refresh_attempted = False
        try:
            from open_webui.models.big_events import BigEvents

            if discovery_is_stale(BigEvents.get_state()):
                refresh_attempted = True
                request = Request(
                    {
                        "type": "http",
                        "method": "GET",
                        "path": "/internal/big-events",
                        "headers": [],
                        "query_string": b"",
                        "server": ("127.0.0.1", 80),
                        "client": ("127.0.0.1", 0),
                        "scheme": "http",
                        "app": app,
                    }
                )
                await refresh_big_events(request)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception(
                "Scheduled big-event discovery failed; retaining database events"
            )
        try:
            state = BigEvents.get_state()
            delay = seconds_until_next_discovery(state)
            if refresh_attempted and discovery_is_stale(state):
                delay = max(3600, delay)
        except Exception:
            delay = 3600
        await asyncio.sleep(delay)
