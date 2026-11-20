"""Durable, user/chat-scoped ticket drafts with atomic single-use submission."""
import time
from uuid import uuid4

from sqlalchemy import BigInteger, Column, JSON, String, Text, UniqueConstraint, select, update
from sqlalchemy.exc import IntegrityError

from open_webui.internal.db import Base, get_async_db
from open_webui.utils.manage_engine import ManageEngineError


class ManageEngineDraft(Base):
    __tablename__ = 'manage_engine_draft'
    id = Column(String(32), primary_key=True)
    user_id = Column(Text, nullable=False)
    chat_id = Column(Text, nullable=False)
    source_id = Column(Text, nullable=False)
    endpoint = Column(Text, nullable=False)
    payload = Column(JSON, nullable=False)
    status = Column(String(20), nullable=False)
    result = Column(JSON, nullable=True)
    expires_at = Column(BigInteger, nullable=False)
    __table_args__ = (UniqueConstraint('user_id', 'chat_id', 'source_id', name='uq_manage_engine_draft_source'),)


def snapshot(row):
    return {column.name: getattr(row, column.name) for column in ManageEngineDraft.__table__.columns}


async def save_draft(context, endpoint, payload, *, status='pending', result=None):
    async with get_async_db() as db:
        where = (ManageEngineDraft.user_id == context['user_id'],
                 ManageEngineDraft.chat_id == context['chat_id'],
                 ManageEngineDraft.source_id == context['source_id'])
        row = (await db.execute(select(ManageEngineDraft).where(*where))).scalar_one_or_none()
        if row is None:
            row = ManageEngineDraft(id=uuid4().hex, **context, endpoint=endpoint,
                                    payload=payload, status=status, result=result, expires_at=int(time.time()) + 1800)
            # A new revision replaces the previous incomplete form in this chat.
            await db.execute(update(ManageEngineDraft).where(
                ManageEngineDraft.user_id == context['user_id'],
                ManageEngineDraft.chat_id == context['chat_id'],
                ManageEngineDraft.status == 'collecting',
            ).values(status='superseded'))
            db.add(row)
            try:
                await db.commit()
                await db.refresh(row)
            except IntegrityError:
                await db.rollback()
                row = (await db.execute(select(ManageEngineDraft).where(*where))).scalar_one()
        return snapshot(row)


async def get_draft(draft_id, context):
    async with get_async_db() as db:
        row = (await db.execute(select(ManageEngineDraft).where(
            ManageEngineDraft.id == draft_id, ManageEngineDraft.user_id == context['user_id'],
            ManageEngineDraft.chat_id == context['chat_id'],
        ))).scalar_one_or_none()
        if row is None:
            raise ManageEngineError('This ticket draft is not available in your current chat.')
        return snapshot(row)


async def claim_draft(draft_id, context, status='submitting'):
    async with get_async_db() as db:
        result = await db.execute(update(ManageEngineDraft).where(
            ManageEngineDraft.id == draft_id, ManageEngineDraft.user_id == context['user_id'],
            ManageEngineDraft.chat_id == context['chat_id'], ManageEngineDraft.status == 'pending',
            ManageEngineDraft.expires_at > int(time.time()),
        ).values(status=status))
        await db.commit()
        return result.rowcount == 1


async def finish_draft(draft_id, status, result):
    async with get_async_db() as db:
        await db.execute(update(ManageEngineDraft).where(
            ManageEngineDraft.id == draft_id, ManageEngineDraft.status == 'submitting',
        ).values(status=status, result=result))
        await db.commit()


async def get_collecting_draft(context, endpoint):
    async with get_async_db() as db:
        row = (await db.execute(select(ManageEngineDraft).where(
            ManageEngineDraft.user_id == context['user_id'],
            ManageEngineDraft.chat_id == context['chat_id'],
            ManageEngineDraft.endpoint == endpoint,
            ManageEngineDraft.status == 'collecting',
            ManageEngineDraft.expires_at > int(time.time()),
        ).order_by(ManageEngineDraft.expires_at.desc(), ManageEngineDraft.id.desc()).limit(1))).scalar_one_or_none()
        return snapshot(row) if row else None
