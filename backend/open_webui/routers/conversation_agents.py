import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status

from open_webui.models.conversation_agents import (
    ConversationAgents,
    ConversationAgentModel,
    ConversationAgentForm,
    ConversationAgentUpdateForm,
)

from open_webui.constants import ERROR_MESSAGES
from open_webui.env import SRC_LOG_LEVELS

from open_webui.utils.auth import get_admin_user


log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MODELS"])

router = APIRouter()


############################
# List Agents
############################


@router.get("/", response_model=list[ConversationAgentModel])
async def get_agents(request: Request, user=Depends(get_admin_user)):
    return ConversationAgents.get_agents()


############################
# Create Agent
############################


@router.post("/create", response_model=Optional[ConversationAgentModel])
async def create_new_agent(
    request: Request,
    form_data: ConversationAgentForm,
    user=Depends(get_admin_user),
):
    try:
        return ConversationAgents.insert_new_agent(form_data, user.id)
    except Exception as e:
        log.exception(e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(),
        )

############################
# Get Agent
############################


@router.get("/{id}", response_model=Optional[ConversationAgentModel])
async def get_agent_by_id(
    request: Request, id: str, user=Depends(get_admin_user)
):
    agent = ConversationAgents.get_agent_by_id(id)
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    return agent


############################
# Update Agent
############################


@router.post("/{id}/update", response_model=Optional[ConversationAgentModel])
async def update_agent_by_id(
    request: Request,
    id: str,
    form_data: ConversationAgentUpdateForm,
    user=Depends(get_admin_user),
):
    agent = ConversationAgents.get_agent_by_id(id)
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    try:
        return ConversationAgents.update_agent_by_id(id, form_data)
    except Exception as e:
        log.exception(e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(),
        )


############################
# Delete Agent
############################


@router.delete("/{id}/delete", response_model=bool)
async def delete_agent_by_id(
    request: Request, id: str, user=Depends(get_admin_user)
):
    agent = ConversationAgents.get_agent_by_id(id)
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    try:
        ConversationAgents.delete_agent_by_id(id)
        return True
    except Exception as e:
        log.exception(e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(),
        )
