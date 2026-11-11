import time
import uuid
from typing import Optional

from open_webui.internal.db import Base, get_db
from open_webui.models.users import UserResponse

from pydantic import BaseModel, ConfigDict
from sqlalchemy import BigInteger, Column, Text, JSON


####################
# ConversationAgent DB Schema
####################


class ConversationAgent(Base):
    __tablename__ = "conversation_agent"

    id = Column(Text, primary_key=True, unique=True)
    user_id = Column(Text)

    name = Column(Text)
    description = Column(Text, nullable=True)
    system_prompt = Column(Text, nullable=True)
    model_id = Column(Text, nullable=True)

    voice_config = Column(JSON, nullable=True)
    meta = Column(JSON, nullable=True)
    access_control = Column(JSON, nullable=True)

    created_at = Column(BigInteger)
    updated_at = Column(BigInteger)


class ConversationAgentModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str

    name: str
    description: Optional[str] = None
    system_prompt: Optional[str] = None
    model_id: Optional[str] = None

    voice_config: Optional[dict] = None
    meta: Optional[dict] = None
    access_control: Optional[dict] = None

    created_at: int
    updated_at: int


####################
# Forms
####################


class ConversationAgentForm(BaseModel):
    name: str
    description: Optional[str] = None
    system_prompt: Optional[str] = None
    model_id: Optional[str] = None
    voice_config: Optional[dict] = None
    meta: Optional[dict] = None
    access_control: Optional[dict] = None


class ConversationAgentUpdateForm(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    system_prompt: Optional[str] = None
    model_id: Optional[str] = None
    voice_config: Optional[dict] = None
    meta: Optional[dict] = None
    access_control: Optional[dict] = None


class ConversationAgentUserResponse(ConversationAgentModel):
    user: Optional[UserResponse] = None


class ConversationAgentTable:
    def insert_new_agent(
        self,
        form_data: ConversationAgentForm,
        user_id: str,
    ) -> Optional[ConversationAgentModel]:
        with get_db() as db:
            agent = ConversationAgentModel(
                **{
                    "id": str(uuid.uuid4()),
                    "user_id": user_id,
                    **form_data.model_dump(),
                    "created_at": int(time.time_ns()),
                    "updated_at": int(time.time_ns()),
                }
            )
            new_agent = ConversationAgent(**agent.model_dump())
            db.add(new_agent)
            db.commit()
            return agent

    def get_agents(
        self, skip: Optional[int] = None, limit: Optional[int] = None
    ) -> list[ConversationAgentModel]:
        with get_db() as db:
            query = db.query(ConversationAgent).order_by(
                ConversationAgent.updated_at.desc()
            )
            if skip is not None:
                query = query.offset(skip)
            if limit is not None:
                query = query.limit(limit)
            return [ConversationAgentModel.model_validate(a) for a in query.all()]

    def get_agents_by_user_id(
        self,
        user_id: str,
        skip: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> list[ConversationAgentModel]:
        with get_db() as db:
            query = db.query(ConversationAgent).filter(
                ConversationAgent.user_id == user_id
            )
            query = query.order_by(ConversationAgent.updated_at.desc())
            if skip is not None:
                query = query.offset(skip)
            if limit is not None:
                query = query.limit(limit)
            return [ConversationAgentModel.model_validate(a) for a in query.all()]

    def get_agent_by_id(self, id: str) -> Optional[ConversationAgentModel]:
        with get_db() as db:
            agent = (
                db.query(ConversationAgent)
                .filter(ConversationAgent.id == id)
                .first()
            )
            return ConversationAgentModel.model_validate(agent) if agent else None

    def update_agent_by_id(
        self, id: str, form_data: ConversationAgentUpdateForm
    ) -> Optional[ConversationAgentModel]:
        with get_db() as db:
            agent = (
                db.query(ConversationAgent)
                .filter(ConversationAgent.id == id)
                .first()
            )
            if not agent:
                return None

            data = form_data.model_dump(exclude_unset=True)
            for field in (
                "name",
                "description",
                "system_prompt",
                "model_id",
                "voice_config",
                "meta",
                "access_control",
            ):
                if field in data:
                    setattr(agent, field, data[field])

            agent.updated_at = int(time.time_ns())
            db.commit()
            return ConversationAgentModel.model_validate(agent)

    def delete_agent_by_id(self, id: str) -> bool:
        with get_db() as db:
            db.query(ConversationAgent).filter(
                ConversationAgent.id == id
            ).delete()
            db.commit()
            return True


ConversationAgents = ConversationAgentTable()
