"""Review-first creation workflow. The model cannot approve or submit a draft."""
import html
import re
import time
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from open_webui.utils import manage_engine as me


class TicketDraftFields(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    subject: Optional[str] = Field(default=None, max_length=250)
    description: Optional[str] = Field(default=None, max_length=20000)
    requester: Optional[str] = Field(default=None, max_length=250)
    technician: Optional[str] = Field(default=None, max_length=250)
    priority: Optional[str] = Field(default=None, max_length=100)
    group: Optional[str] = Field(default=None, max_length=250)
    site: Optional[str] = Field(default=None, max_length=250)
    template: Optional[str] = Field(default=None, max_length=250)


REQUIRED_CREATION_FIELDS = ('subject', 'description')


def fields_from_payload(payload):
    values = {}
    for key in TicketDraftFields.model_fields:
        value = payload.get(key)
        if isinstance(value, dict):
            value = (value.get('email_id') or value.get('id') or value.get('name')) if key in ('requester', 'technician') else value.get('name')
        if value:
            values[key] = html.unescape(re.sub(r'<br\s*/?>', '\n', value)) if key == 'description' else value
    return values


async def collect_creation_fields(fields, missing, context, settings, *, payload=None):
    values = fields.model_dump(exclude_none=True)
    unsupported = [field for field in missing if field not in TicketDraftFields.model_fields]
    clarification = (
        'Required fields: ' + ', '.join(sorted(set(REQUIRED_CREATION_FIELDS) | set(missing))) + '. '
        'Please provide or correct the following fields before ticket creation: ' + ', '.join(missing) + '. '
        'The details you already provided have been saved. No ticket has been created; confirmation is not available yet.'
    )
    if unsupported:
        clarification += (' These fields are not supported by chat creation: ' + ', '.join(unsupported) +
                          '. Please choose another template or complete this request in ServiceDesk.')
    result = {'clarification': clarification, 'missing_fields': missing,
              'pending_creation': values, 'required_fields': sorted(set(REQUIRED_CREATION_FIELDS) | set(missing))}
    from open_webui.models.manage_engine import save_draft
    await save_draft(context, settings['endpoint'].rstrip('/'), payload or values,
                     status='collecting', result=result)
    return result


def require_context(context):
    if not context or not all(context.get(k) for k in ('user_id', 'chat_id', 'source_id')):
        raise me.ManageEngineError('Create tickets from a chat with an active signed-in user. Start a new chat and try again.')


def draft_preview(draft):
    if draft['status'] == 'completed':
        return draft['result']
    if draft['status'] != 'pending' or draft['expires_at'] <= time.time():
        raise me.ManageEngineError('This draft is no longer pending or has expired. Send a new message to prepare another draft.')
    return {
        'creation_draft': draft['payload'], 'draft_id': draft['id'],
        'confirmation_required': True,
        'confirmation_command': f"Confirm create {draft['id']}",
        'cancel_command': f"Cancel create {draft['id']}",
        'message': 'Review this ticket draft. No ticket has been created. Reply with the exact confirmation command to submit, or describe changes to prepare a revised draft. This draft expires in 30 minutes.',
    }


async def prepare_creation(fields, context, settings, generate_plan, messages, required_fields=None):
    require_context(context)
    fields = fields or TicketDraftFields()
    required = list(dict.fromkeys([*REQUIRED_CREATION_FIELDS, *(required_fields or [])]))
    missing = [key for key in required if key not in TicketDraftFields.model_fields or not getattr(fields, key, None)]
    if missing:
        return await collect_creation_fields(fields, missing, context, settings)
    if '\n' in fields.subject or '\r' in fields.subject:
        return {'clarification': 'Please provide a single-line ticket subject (up to 250 characters).'}
    payload = {'subject': fields.subject}
    if fields.description:
        payload['description'] = html.escape(fields.description).replace('\n', '<br />')
    requester = fields.requester
    if requester:
        if '@' in requester:
            # A full email is an API-supported identifier and does not require directory access.
            if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', requester):
                return {'clarification': 'Please provide a valid full email address for the requester.'}
            payload['requester'] = {'email_id': requester}
        else:
            from open_webui.utils.manage_engine_chat import resolve_person
            client = me.ManageEngineClient(settings)
            try:
                users = await client.list_requesters(login_name=requester)
                # The API may ignore search_fields; only trust verified matching rows.
                exact_logins = [u for u in users if u['login_name'].casefold() == requester.casefold()]
                if exact_logins:
                    users = exact_logins
                else:
                    users = await client.list_requesters()
            except me.ManageEngineError as exc:
                if exc.status == 403:
                    return {'clarification': str(exc) + ' No ticket has been created.',
                            'pending_creation': fields.model_dump(exclude_none=True)}
                raise
            resolved, candidates = await resolve_person(requester, users, generate_plan, messages, 'requester')
            if resolved is None:
                options = '\n'.join(
                    f"{i}. {u['name']} ({u.get('login_name') or u['id']}; {u.get('email') or 'no email'}; {u.get('domain') or 'no domain'}) [ID: {u['id']}]"
                    for i, u in enumerate(candidates, 1)
                )
                return {
                    'clarification': 'Please confirm the requester for the new ticket:\n' +
                    (options or 'Please provide their full name or full email address.') + '\nNo ticket has been created.',
                    'pending_creation': fields.model_dump(exclude_none=True),
                }
            payload['requester'] = {'id': resolved['id'], 'name': resolved['name']}
    for key in ('priority', 'group', 'site', 'template'):
        value = getattr(fields, key)
        if value:
            payload[key] = {'name': value}
    if fields.technician:
        from open_webui.utils.manage_engine_chat import resolve_technician
        client = me.ManageEngineClient(settings)
        technicians = await client.list_technicians()
        technician, candidates = await resolve_technician(fields.technician, technicians, generate_plan, messages)
        if technician is None:
            options = '\n'.join(f"{i}. {t['name']} ({t.get('login_name') or t.get('email') or t['id']}) [ID: {t['id']}]"
                                for i, t in enumerate(candidates, 1))
            return {'clarification': 'Please confirm the technician for the new ticket:\n' +
                    (options or 'Please provide a full name or email address.') + '\nNo ticket has been created.',
                    'pending_creation': fields.model_dump(exclude_none=True)}
        payload['technician'] = {'id': technician['id'], 'name': technician['name']}
    from open_webui.models.manage_engine import save_draft
    draft = await save_draft(context, settings['endpoint'].rstrip('/'), payload)
    return draft_preview(draft)


async def handle_creation_command(messages, context, settings):
    """Only an explicit latest-user command can submit/cancel; never an LLM decision."""
    latest = next((m.get('content') for m in reversed(messages) if m.get('role') == 'user'), '')
    if isinstance(latest, list):
        latest = '\n'.join(part.get('text', '') for part in latest if part.get('type') == 'text')
    match = re.fullmatch(r'(confirm|cancel) create ([a-f0-9]{32})', str(latest).strip(), re.IGNORECASE)
    if not match:
        return None
    require_context(context)
    from open_webui.models.manage_engine import get_draft, claim_draft, finish_draft
    action, draft_id = match.group(1).lower(), match.group(2).lower()
    draft = await get_draft(draft_id, context)
    if draft['status'] == 'completed':
        return draft['result']
    if draft['status'] != 'pending':
        raise me.ManageEngineError('This draft was already submitted or cancelled. Check ServiceDesk before preparing another ticket; it will not be submitted again.')
    if draft['expires_at'] <= time.time():
        raise me.ManageEngineError('This ticket draft has expired. Send a new message to prepare a fresh draft.')
    if draft['endpoint'] != settings['endpoint'].rstrip('/'):
        raise me.ManageEngineError('The ServiceDesk endpoint has changed. Prepare and review a new draft before submitting.')
    missing = [key for key in REQUIRED_CREATION_FIELDS if not str(draft['payload'].get(key) or '').strip()]
    if action == 'confirm' and missing:
        # Older previews must pass the same validation as newly generated drafts.
        return await collect_creation_fields(
            TicketDraftFields.model_validate(fields_from_payload(draft['payload'])), missing,
            context, settings, payload=draft['payload'],
        )
    requester = draft['payload'].get('requester') or {}
    if action == 'confirm' and requester and not (requester.get('id') or requester.get('email_id')):
        raise me.ManageEngineError('This older draft contains an unverified requester name. Send a new message with the requester account or full email address to prepare a new draft.')
    claimed = await claim_draft(draft_id, context, 'cancelled' if action == 'cancel' else 'submitting')
    if not claimed:
        raise me.ManageEngineError('This draft is already being processed or has expired. It will not be submitted again.')
    if action == 'cancel':
        return {'creation_cancelled': True, 'message': 'Ticket creation cancelled. No ticket was created.'}
    try:
        # The saved, reviewed payload is authoritative; no second model extraction occurs.
        result = await me.ManageEngineClient(settings).create_request(draft['payload'])
    except me.ManageEngineValidationError as exc:
        await finish_draft(draft_id, 'validation_failed', {'fields': exc.fields})
        values = fields_from_payload(draft['payload'])
        invalid_fields = list(dict.fromkeys(field.split('.')[0] for field in exc.fields))
        for key in invalid_fields:
            values.pop(key, None)
        return await collect_creation_fields(
            TicketDraftFields.model_validate(values), invalid_fields,
            context, settings, payload=draft['payload'],
        )
    except Exception:
        await finish_draft(draft_id, 'failed', {'message': 'Creation was not confirmed. Check ServiceDesk before trying again.'})
        raise
    await finish_draft(draft_id, 'completed', result)
    return result
