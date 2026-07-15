import time
from datetime import date, timedelta
from typing import Optional

from pydantic import BaseModel, ConfigDict
from sqlalchemy import BigInteger, Column, String, Text

from open_webui.internal.db import Base, get_db


class BigEvent(Base):
    __tablename__ = "big_event"

    id = Column(String, primary_key=True, unique=True)
    title = Column(Text, nullable=False)
    start = Column(String, nullable=False, index=True)
    end = Column(String, nullable=True)
    description = Column(Text, nullable=True)
    location = Column(Text, nullable=True)
    organiser = Column(Text, nullable=True)
    target_audience = Column(Text, nullable=True)
    cost = Column(Text, nullable=True)
    participation = Column(Text, nullable=True)
    registration_url = Column(Text, nullable=False)
    url = Column(Text, nullable=False)
    category = Column(String, nullable=False, default="other")
    source_type = Column(String, nullable=False, default="discovered", index=True)
    status = Column(String, nullable=False, default="candidate")
    last_verified = Column(String, nullable=True)
    first_seen_at = Column(BigInteger, nullable=False)
    last_seen_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)


class BigEventDiscoveryState(Base):
    __tablename__ = "big_event_discovery_state"

    id = Column(String, primary_key=True)
    last_success = Column(BigInteger, nullable=True)
    last_attempt = Column(BigInteger, nullable=True)
    error = Column(Text, nullable=True)
    engine = Column(String, nullable=True)


class BigEventModel(BaseModel):
    id: str
    title: str
    start: str
    end: Optional[str] = None
    description: Optional[str] = None
    location: Optional[str] = None
    organiser: Optional[str] = None
    target_audience: Optional[str] = None
    cost: Optional[str] = None
    participation: Optional[str] = None
    registration_url: str
    url: str
    category: str
    source_type: str
    status: str
    last_verified: Optional[str] = None
    first_seen_at: int
    last_seen_at: int
    updated_at: int

    model_config = ConfigDict(from_attributes=True)


def _to_api_event(event: BigEventModel) -> dict:
    return {
        "id": event.id,
        "title": event.title,
        "start": event.start,
        "end": event.end,
        "description": event.description,
        "location": event.location,
        "organiser": event.organiser,
        "targetAudience": event.target_audience,
        "cost": event.cost,
        "participation": event.participation,
        "registrationUrl": event.registration_url,
        "url": event.url,
        "category": event.category,
        "sourceId": event.source_type,
        "lastVerified": event.last_verified,
        "discoveryStatus": event.status,
        "sourceType": event.source_type,
    }


class BigEventsTable:
    STATE_ID = "sydney-executive-discovery"

    def upsert_discovered(self, events: list[dict]) -> int:
        now = int(time.time())
        with get_db() as db:
            for data in events:
                event = db.get(BigEvent, data["id"])
                values = {
                    "title": data["title"],
                    "start": data["start"],
                    "end": data.get("end"),
                    "description": data.get("description"),
                    "location": data.get("location"),
                    "organiser": data.get("organiser"),
                    "target_audience": data.get("targetAudience"),
                    "cost": data.get("cost"),
                    "participation": data.get("participation"),
                    "registration_url": data["registrationUrl"],
                    "url": data.get("url") or data["registrationUrl"],
                    "category": data.get("category", "other"),
                    "source_type": data.get("sourceType", "discovered"),
                    "status": data.get("discoveryStatus", "candidate"),
                    "last_verified": data.get("lastVerified"),
                    "last_seen_at": now,
                    "updated_at": now,
                }
                if event:
                    for key, value in values.items():
                        setattr(event, key, value)
                else:
                    db.add(BigEvent(id=data["id"], first_seen_at=now, **values))
            db.commit()
        return len(events)

    def get_upcoming(
        self, limit: int = 250, source_type: Optional[str] = None
    ) -> list[dict]:
        today = date.today().isoformat()
        with get_db() as db:
            query = db.query(BigEvent).filter(BigEvent.start >= today)
            if source_type:
                query = query.filter(BigEvent.source_type == source_type)
            events = query.order_by(BigEvent.start.asc()).limit(limit).all()
            return [
                _to_api_event(BigEventModel.model_validate(event)) for event in events
            ]

    def delete_expired_discovered(
        self, source_types: tuple[str, ...], retention_days: int = 30
    ) -> int:
        cutoff = (date.today() - timedelta(days=retention_days)).isoformat()
        with get_db() as db:
            deleted = (
                db.query(BigEvent)
                .filter(
                    BigEvent.source_type.in_(source_types),
                    BigEvent.start < cutoff,
                )
                .delete(synchronize_session=False)
            )
            db.commit()
            return deleted

    def delete_legacy_sources(self) -> int:
        """Remove the former hard-coded and web-search event rows after a successful crawl."""
        with get_db() as db:
            deleted = (
                db.query(BigEvent)
                .filter(BigEvent.source_type.in_(("seed", "discovered")))
                .delete(synchronize_session=False)
            )
            db.commit()
            return deleted

    def delete_stale_managed_sources(
        self, source_types: tuple[str, ...], stale_days: int = 7
    ) -> int:
        cutoff = int(time.time()) - stale_days * 86400
        with get_db() as db:
            deleted = (
                db.query(BigEvent)
                .filter(
                    BigEvent.source_type.in_(source_types),
                    BigEvent.last_seen_at < cutoff,
                )
                .delete(synchronize_session=False)
            )
            db.commit()
            return deleted

    def get_state(self) -> dict:
        with get_db() as db:
            state = db.get(BigEventDiscoveryState, self.STATE_ID)
            if not state:
                return {
                    "lastSuccess": None,
                    "lastAttempt": None,
                    "error": None,
                    "engine": None,
                }
            return {
                "lastSuccess": state.last_success,
                "lastAttempt": state.last_attempt,
                "error": state.error,
                "engine": state.engine,
            }

    def update_state(
        self,
        *,
        last_success: Optional[int],
        last_attempt: int,
        error: Optional[str],
        engine: str,
    ) -> None:
        with get_db() as db:
            state = db.get(BigEventDiscoveryState, self.STATE_ID)
            if not state:
                state = BigEventDiscoveryState(id=self.STATE_ID)
                db.add(state)
            state.last_success = last_success
            state.last_attempt = last_attempt
            state.error = error
            state.engine = engine
            db.commit()


BigEvents = BigEventsTable()
