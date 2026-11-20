import asyncio
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from contextlib import asynccontextmanager
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base
from sqlalchemy import update

from open_webui.utils import manage_engine as me
from open_webui.utils.manage_engine_chat import retrieve_tickets
from open_webui.utils.manage_engine_create import TicketDraftFields, prepare_creation, handle_creation_command


class CreationWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.engine = create_async_engine(f'sqlite+aiosqlite:///{self.directory.name}/drafts.db')
        sessions = async_sessionmaker(self.engine)
        base = declarative_base()

        @asynccontextmanager
        async def get_db():
            async with sessions() as session:
                yield session

        self.db = get_db
        internal = ModuleType('open_webui.internal.db')
        internal.Base = base
        internal.get_async_db = get_db
        path = Path(__file__).resolve().parents[2] / 'models' / 'manage_engine.py'
        spec = importlib.util.spec_from_file_location('test_manage_engine_store', path)
        self.store = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'open_webui.internal.db': internal}):
            spec.loader.exec_module(self.store)
        self.store_patch = patch.dict(sys.modules, {'open_webui.models.manage_engine': self.store})
        self.store_patch.start()
        async with self.engine.begin() as connection:
            await connection.run_sync(base.metadata.create_all)
        self.context = {'user_id': 'alice', 'chat_id': 'chat-a', 'source_id': 'message-a'}
        self.settings = {'endpoint': 'https://sdp.example.com', 'api_key': 'test', 'admin_only': False}
        self.settings_patch = patch.object(me, 'load_settings', AsyncMock(return_value=self.settings))
        self.settings_patch.start()
        self.plan = AsyncMock(return_value=json.dumps({'action': 'create', 'creation': {
            'subject': 'VPN is unavailable', 'description': 'Cannot connect <today>',
            'requester': 'alice@example.com', 'priority': 'High',
        }}))
        self.messages = [{'role': 'user', 'content': 'Create a ticket for my VPN connection failure'}]
        self.post = AsyncMock(return_value={'created': True, 'ticket': {'id': '42', 'url': 'https://sdp.example.com/WorkOrder.do?woID=42'}})
        self.post_patch = patch.object(me.ManageEngineClient, 'create_request', self.post)
        self.post_patch.start()

    async def asyncTearDown(self):
        self.post_patch.stop()
        self.settings_patch.stop()
        self.store_patch.stop()
        await self.engine.dispose()
        self.directory.cleanup()

    async def draft(self):
        return await retrieve_tickets(self.messages, self.plan, 'admin', self.context)

    async def command(self, draft, action='Confirm', context=None):
        return await handle_creation_command(
            [{'role': 'user', 'content': f"{action} create {draft['draft_id']}"}],
            context or self.context, self.settings,
        )

    async def test_creation_stops_at_review_with_no_post(self):
        draft = await self.draft()
        self.post.assert_not_awaited()
        self.assertTrue(draft['confirmation_required'])
        self.assertEqual(draft['creation_draft']['subject'], 'VPN is unavailable')
        self.assertEqual(draft['creation_draft']['description'], 'Cannot connect &lt;today&gt;')
        self.assertEqual(draft['creation_draft']['requester'], {'email_id': 'alice@example.com'})
        self.assertIn(draft['draft_id'], draft['confirmation_command'])

    async def test_confirm_uses_saved_payload_and_replay_returns_existing_result(self):
        draft = await self.draft()
        self.plan.reset_mock()
        confirmation = [{'role': 'user', 'content': draft['confirmation_command']}]
        result = await retrieve_tickets(confirmation, self.plan, 'admin', self.context)
        again = await self.command(draft)
        self.assertEqual(result, again)
        self.assertEqual(result['ticket']['id'], '42')
        self.post.assert_awaited_once_with(draft['creation_draft'])
        self.plan.assert_not_awaited()

    async def test_concurrent_confirmations_submit_only_once(self):
        draft = await self.draft()
        results = await asyncio.gather(self.command(draft), self.command(draft), return_exceptions=True)
        self.post.assert_awaited_once()
        self.assertTrue(any(isinstance(r, dict) and r.get('created') for r in results))

    async def test_cancel_consumes_draft_without_post(self):
        draft = await self.draft()
        self.assertTrue((await self.command(draft, 'Cancel'))['creation_cancelled'])
        with self.assertRaises(me.ManageEngineError):
            await self.command(draft)
        self.post.assert_not_awaited()

    async def test_other_user_or_chat_cannot_confirm(self):
        draft = await self.draft()
        for context in ({**self.context, 'user_id': 'bob'}, {**self.context, 'chat_id': 'chat-b'}):
            with self.assertRaisesRegex(me.ManageEngineError, 'not available'):
                await self.command(draft, context=context)
        self.post.assert_not_awaited()

    async def test_expiration_and_endpoint_change_require_new_review(self):
        draft = await self.draft()
        self.settings['endpoint'] = 'https://different.example.com'
        with self.assertRaisesRegex(me.ManageEngineError, 'endpoint has changed'):
            await self.command(draft)
        self.settings['endpoint'] = 'https://sdp.example.com'
        async with self.db() as db:
            await db.execute(update(self.store.ManageEngineDraft).values(expires_at=int(time.time()) - 1))
            await db.commit()
        with self.assertRaisesRegex(me.ManageEngineError, 'expired'):
            await self.command(draft)
        self.post.assert_not_awaited()

    async def test_uncertain_failure_is_never_resubmitted(self):
        draft = await self.draft()
        self.post.side_effect = me.ManageEngineError('Uncertain result; check ServiceDesk')
        with self.assertRaises(me.ManageEngineError):
            await self.command(draft)
        with self.assertRaisesRegex(me.ManageEngineError, 'will not be submitted again'):
            await self.command(draft)
        self.post.assert_awaited_once()

    async def test_assistant_text_or_generic_yes_cannot_submit(self):
        draft = await self.draft()
        messages = [{'role': 'assistant', 'content': draft['confirmation_command']},
                    {'role': 'user', 'content': 'yes'}]
        self.assertIsNone(await handle_creation_command(messages, self.context, self.settings))
        self.post.assert_not_awaited()

    async def test_missing_subject_asks_for_details(self):
        result = await prepare_creation(TicketDraftFields(), self.context, self.settings, self.plan, self.messages)
        self.assertIn('subject', result['clarification'])
        self.post.assert_not_awaited()

    async def test_regeneration_of_same_message_reuses_reviewed_draft(self):
        first = await self.draft()
        self.plan.return_value = '{"action":"create","creation":{"subject":"Unexpected replacement","description":"VPN disconnects after login"}}'
        second = await self.draft()
        self.assertEqual(first['draft_id'], second['draft_id'])
        self.assertEqual(first['creation_draft'], second['creation_draft'])
        self.post.assert_not_awaited()

    async def test_edits_in_new_message_prepare_new_review(self):
        first = await self.draft()
        self.context['source_id'] = 'message-b'
        self.plan.return_value = '{"action":"create","creation":{"subject":"VPN still unavailable","priority":"Low","description":"VPN disconnects after login"}}'
        second = await self.draft()
        self.assertNotEqual(first['draft_id'], second['draft_id'])
        self.assertEqual(second['creation_draft']['priority']['name'], 'Low')
        self.post.assert_not_awaited()

    async def test_creation_resolves_technician_before_review(self):
        self.plan.return_value = '{"action":"create","creation":{"subject":"VPN failure","technician":"wangj","description":"VPN disconnects after login"}}'
        with patch.object(me.ManageEngineClient, 'list_technicians', AsyncMock(return_value=[
            {'id': '7', 'name': 'J Wang', 'login_name': 'wangj', 'email': 'wangj@example.com'}
        ])):
            draft = await self.draft()
        self.assertEqual(draft['creation_draft']['technician'], {'id': '7', 'name': 'J Wang'})
        self.post.assert_not_awaited()

    async def test_ambiguous_assignment_asks_before_creating_draft(self):
        self.plan.side_effect = [
            '{"action":"create","creation":{"subject":"VPN failure","technician":"wang","description":"VPN disconnects after login"}}',
            '{"confidence":"uncertain","candidate_ids":["7","8"]}',
        ]
        with patch.object(me.ManageEngineClient, 'list_technicians', AsyncMock(return_value=[
            {'id': '7', 'name': 'J Wang', 'login_name': 'wangj', 'email': ''},
            {'id': '8', 'name': 'K Wang', 'login_name': 'wangk', 'email': ''},
        ])), patch.object(self.store, 'save_draft', AsyncMock()) as save:
            result = await self.draft()
        self.assertIn('1. J Wang', result['clarification'])
        self.assertIn('2. K Wang', result['clarification'])
        save.assert_not_awaited()
        self.post.assert_not_awaited()

    async def test_requester_login_is_resolved_and_submitted_by_id(self):
        self.plan.return_value = '{"action":"create","creation":{"subject":"VPN failure","requester":"wangj","description":"VPN disconnects after login"}}'
        with patch.object(me.ManageEngineClient, 'list_requesters', AsyncMock(return_value=[
            {'id': '71', 'name': 'J Wang', 'login_name': 'wangj', 'email': 'wangj@example.com', 'domain': 'CORP'}
        ])) as directory:
            draft = await self.draft()
        directory.assert_awaited_once_with(login_name='wangj')
        self.assertEqual(draft['creation_draft']['requester'], {'id': '71', 'name': 'J Wang'})
        self.post.assert_not_awaited()
        await self.command(draft)
        self.assertEqual(self.post.call_args.args[0]['requester']['id'], '71')

    async def test_duplicate_requester_logins_require_a_choice(self):
        self.plan.return_value = '{"action":"create","creation":{"subject":"VPN failure","requester":"wangj","description":"VPN disconnects after login"}}'
        with patch.object(me.ManageEngineClient, 'list_requesters', AsyncMock(return_value=[
            {'id': '71', 'name': 'J Wang', 'login_name': 'wangj', 'email': 'j@example.com', 'domain': 'CORP'},
            {'id': '72', 'name': 'John Wang', 'login_name': 'wangj', 'email': 'john@example.com', 'domain': 'OTHER'},
        ])), patch.object(self.store, 'save_draft', AsyncMock()) as save:
            result = await self.draft()
        self.assertIn('CORP', result['clarification'])
        self.assertIn('OTHER', result['clarification'])
        self.assertIn('ID: 71', result['clarification'])
        self.assertIn('ID: 72', result['clarification'])
        self.assertEqual(result['pending_creation']['subject'], 'VPN failure')
        save.assert_not_awaited()
        self.post.assert_not_awaited()

    async def test_requester_directory_denied_asks_for_email_before_review(self):
        self.plan.return_value = '{"action":"create","creation":{"subject":"VPN failure","requester":"wangj","description":"VPN disconnects after login"}}'
        with patch.object(me.ManageEngineClient, 'list_requesters', AsyncMock(side_effect=me.ManageEngineError(
            'Requester directory access was denied (HTTP 403). Provide a full email address.', status=403
        ))), patch.object(self.store, 'save_draft', AsyncMock()) as save:
            result = await self.draft()
        self.assertIn('full email address', result['clarification'])
        self.assertIn('No ticket has been created', result['clarification'])
        save.assert_not_awaited()
        self.post.assert_not_awaited()

    async def test_explicit_requester_email_does_not_need_user_directory(self):
        with patch.object(me.ManageEngineClient, 'list_requesters', AsyncMock()) as directory:
            draft = await self.draft()
        self.assertEqual(draft['creation_draft']['requester'], {'email_id': 'alice@example.com'})
        directory.assert_not_awaited()

    async def test_old_unverified_requester_draft_is_not_submitted(self):
        row = await self.store.save_draft(self.context, self.settings['endpoint'],
                                          {'subject': 'Old draft', 'description': 'VPN disconnects', 'requester': {'name': 'wangj'}})
        with self.assertRaisesRegex(me.ManageEngineError, 'unverified requester'):
            await self.command({'draft_id': row['id']})
        self.post.assert_not_awaited()

    async def test_fuzzy_requester_match_uses_verified_user_id(self):
        self.plan.side_effect = [
            '{"action":"create","creation":{"subject":"VPN failure","requester":"wangjj","description":"VPN disconnects after login"}}',
            '{"confidence":"high","person_id":"71","candidate_ids":["71"]}',
        ]
        with patch.object(me.ManageEngineClient, 'list_requesters', AsyncMock(side_effect=[[], [
            {'id': '71', 'name': 'J Wang', 'login_name': 'wangj', 'email': 'j@example.com', 'domain': ''}
        ]])):
            draft = await self.draft()
        self.assertEqual(draft['creation_draft']['requester']['id'], '71')
        self.assertIn('requester identity', self.plan.call_args.args[0])
        self.post.assert_not_awaited()

    async def test_missing_description_stops_before_confirmation_and_saves_values(self):
        self.plan.return_value = '{"action":"create","creation":{"subject":"VPN failure","requester":"alice@example.com","priority":"High"}}'
        result = await self.draft()
        self.assertEqual(result['missing_fields'], ['description'])
        self.assertNotIn('confirmation_command', result)
        self.assertNotIn('draft_id', result)
        saved = await self.store.get_collecting_draft(self.context, self.settings['endpoint'])
        self.assertEqual(saved['result']['pending_creation']['requester'], 'alice@example.com')
        self.post.assert_not_awaited()

    async def test_followup_description_preserves_fields_and_requires_fresh_confirmation(self):
        self.plan.return_value = '{"action":"create","creation":{"subject":"VPN failure","requester":"alice@example.com","priority":"High"}}'
        await self.draft()
        self.context['source_id'] = 'description-reply'
        self.plan.return_value = '{"action":"create","resume_creation":true,"creation":{"description":"VPN disconnects after login"}}'
        draft = await self.draft()
        self.assertTrue(draft['confirmation_required'])
        self.assertEqual(draft['creation_draft']['subject'], 'VPN failure')
        self.assertEqual(draft['creation_draft']['requester'], {'email_id': 'alice@example.com'})
        self.assertEqual(draft['creation_draft']['priority'], {'name': 'High'})
        self.assertEqual(draft['creation_draft']['description'], 'VPN disconnects after login')
        self.assertIsNone(await self.store.get_collecting_draft(self.context, self.settings['endpoint']))
        self.post.assert_not_awaited()

    async def test_api_field_validation_becomes_collectable_form_not_generic_failure(self):
        draft = await self.draft()
        self.context['source_id'] = 'confirmation-message'
        self.post.side_effect = me.ManageEngineValidationError('Site is mandatory', ['site'], status=400)
        result = await self.command(draft)
        self.assertEqual(result['missing_fields'], ['site'])
        self.assertNotIn('confirmation_command', result)
        self.assertEqual(result['pending_creation']['subject'], 'VPN is unavailable')
        self.post.assert_awaited_once()
        saved = await self.store.get_draft(draft['draft_id'], self.context)
        self.assertEqual(saved['status'], 'validation_failed')
        self.context['source_id'] = 'site-reply'
        self.plan.return_value = '{"action":"create","resume_creation":true,"creation":{"site":"Sydney"}}'
        revised = await self.draft()
        self.assertTrue(revised['confirmation_required'])
        self.assertNotEqual(revised['draft_id'], draft['draft_id'])
        self.assertEqual(revised['creation_draft']['site'], {'name': 'Sydney'})
        self.assertEqual(revised['creation_draft']['priority'], {'name': 'High'})
        self.post.assert_awaited_once()  # Filling a missing field never submits automatically.

    async def test_old_preview_without_description_cannot_bypass_preflight(self):
        old = await self.store.save_draft(self.context, self.settings['endpoint'], {'subject': 'Legacy ticket'})
        self.context['source_id'] = 'confirm-old-draft'
        result = await self.command({'draft_id': old['id']})
        self.assertEqual(result['missing_fields'], ['description'])
        self.post.assert_not_awaited()

    async def test_unknown_required_custom_field_never_reaches_confirmation(self):
        draft = await self.draft()
        self.context['source_id'] = 'confirm-custom'
        self.post.side_effect = me.ManageEngineValidationError('Custom field required', ['udf_fields.udf_char1'], status=400)
        result = await self.command(draft)
        self.assertIn('not supported by chat creation', result['clarification'])
        self.context['source_id'] = 'reply-custom'
        self.plan.return_value = '{"action":"create","resume_creation":true,"creation":{"description":"More detail"}}'
        result = await self.draft()
        self.assertNotIn('confirmation_command', result)
        self.assertIn('udf_fields', result['missing_fields'])
        self.post.assert_awaited_once()


class CreationHTTPTests(unittest.IsolatedAsyncioTestCase):
    def session(self, status, data):
        response = MagicMock(status=status)
        response.json = AsyncMock(return_value=data)
        session = MagicMock()
        session.post.return_value.__aenter__ = AsyncMock(return_value=response)
        factory = MagicMock()
        factory.return_value.__aenter__ = AsyncMock(return_value=session)
        return session, factory

    async def test_post_wire_format_and_returned_link(self):
        session, factory = self.session(201, {'response_status': {'status_code': 2000},
                                             'request': {'id': '42', 'subject': 'VPN unavailable'}})
        client = me.ManageEngineClient({'endpoint': 'https://example.com', 'api_key': 'test'})
        with patch.object(me.aiohttp, 'ClientSession', factory):
            result = await client.create_request({'subject': 'VPN unavailable'})
        self.assertTrue(result['created'])
        self.assertIn('woID=42', result['ticket']['url'])
        session.post.assert_called_once()
        call = session.post.call_args
        self.assertEqual(call.args[0], 'https://example.com/api/v3/requests')
        self.assertEqual(json.loads(call.kwargs['data']['input_data']), {'request': {'subject': 'VPN unavailable'}})
        self.assertFalse(call.kwargs['allow_redirects'])

    async def test_http_and_application_failures_are_not_success(self):
        for status, data in [(403, {}), (200, {'response_status': {'status_code': 4000, 'status': 'failed'}}),
                             (200, {'request': {'subject': 'no id'}})]:
            session, factory = self.session(status, data)
            with patch.object(me.aiohttp, 'ClientSession', factory):
                with self.assertRaises(me.ManageEngineError):
                    await me.ManageEngineClient({'endpoint': 'https://example.com', 'api_key': 'test'}).create_request({'subject': 'VPN'})
            session.post.assert_called_once()

    async def test_timeout_never_retries_post(self):
        session, factory = self.session(200, {})
        session.post.return_value.__aenter__.side_effect = asyncio.TimeoutError
        with patch.object(me.aiohttp, 'ClientSession', factory):
            with self.assertRaisesRegex(me.ManageEngineError, 'not be retried'):
                await me.ManageEngineClient({'endpoint': 'https://example.com', 'api_key': 'test'}).create_request({'subject': 'VPN'})
        session.post.assert_called_once()

    async def test_logs_detailed_field_errors_without_credentials_or_request_body(self):
        session, factory = self.session(400, {
            'response_status': {
                'status_code': 4000, 'status': 'failed',
                'messages': [{'status_code': 4001, 'field': 'requester',
                              'message': 'Requester does not exist'},
                             {'field': 'site', 'message': 'Mandatory field missing'}],
                'api_key': 'secret-key',
                'detail': 'Rejected authtoken: secret-key',
                'request': {'subject': 'Private subject', 'description': 'Private description'},
            }
        })
        with patch.object(me.aiohttp, 'ClientSession', factory), self.assertLogs(me.log, level='ERROR') as logs:
            with self.assertRaisesRegex(me.ManageEngineError, 'requester, site'):
                await me.ManageEngineClient({'endpoint': 'https://example.com', 'api_key': 'secret-key'}).create_request(
                    {'subject': 'Private subject', 'description': 'Private description', 'requester': {'id': '71'}}
                )
        logged = '\n'.join(logs.output)
        for text in ('http_status=400', '4000', '4001', 'Requester does not exist', 'Mandatory field missing', '[redacted]'):
            self.assertIn(text, logged)
        for text in ('secret-key', 'Private subject', 'Private description'):
            self.assertNotIn(text, logged)
        session.post.assert_called_once()

    async def test_nested_errors_and_list_fields_are_logged_and_reported(self):
        session, factory = self.session(400, {'errors': {
            'validation': [{'fields': ['requester', 'template'], 'code': 'REQUIRED',
                            'message': 'Values are required for the selected template'}]
        }})
        with patch.object(me.aiohttp, 'ClientSession', factory), self.assertLogs(me.log, level='ERROR') as logs:
            with self.assertRaisesRegex(me.ManageEngineError, 'requester, template'):
                await me.ManageEngineClient({'endpoint': 'https://example.com', 'api_key': 'test'}).create_request({'subject': 'VPN'})
        self.assertIn('Values are required for the selected template', logs.output[0])
        self.assertIn('REQUIRED', logs.output[0])

    async def test_permission_errors_also_log_upstream_details(self):
        session, factory = self.session(403, {'response_status': {
            'status_code': 4000, 'messages': [{'message': 'User lacks AddRequest permission'}]
        }})
        with patch.object(me.aiohttp, 'ClientSession', factory), self.assertLogs(me.log, level='ERROR') as logs:
            with self.assertRaisesRegex(me.ManageEngineError, 'denied'):
                await me.ManageEngineClient({'endpoint': 'https://example.com', 'api_key': 'test'}).create_request({'subject': 'VPN'})
        self.assertIn('User lacks AddRequest permission', logs.output[0])

    async def test_mandatory_description_error_preserves_structured_fields(self):
        session, factory = self.session(400, {'response_status': {
            'status_code': 4000, 'status': 'failed', 'messages': [
                {'status_code': 4012, 'type': 'failed', 'message': 'Please fill the mandatory fields',
                 'fields': ['description']}
            ]
        }})
        with patch.object(me.aiohttp, 'ClientSession', factory), self.assertLogs(me.log, level='ERROR'):
            with self.assertRaises(me.ManageEngineValidationError) as error:
                await me.ManageEngineClient({'endpoint': 'https://example.com', 'api_key': 'test'}).create_request({'subject': 'VPN'})
        self.assertEqual(error.exception.fields, ['description'])
        self.assertEqual(error.exception.status, 400)


class RequesterDirectoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_login_search_uses_user_api_and_normalizes_identity(self):
        client = me.ManageEngineClient({'endpoint': 'https://example.com', 'api_key': 'test'})
        client._get = AsyncMock(return_value={'users': [
            {'id': '71', 'name': 'J Wang', 'login_name': 'wangj', 'email_id': 'j@example.com',
             'domain': {'name': 'CORP'}, 'phone': 'private'}
        ], 'list_info': {'has_more_rows': False}})
        users = await client.list_requesters('wangj')
        call = client._get.call_args
        self.assertEqual(call.args[0], '/api/v3/users')
        self.assertEqual(json.loads(call.args[1]['input_data'])['list_info']['search_fields'], {'login_name': 'wangj'})
        self.assertEqual(users[0]['email'], 'j@example.com')
        self.assertEqual(users[0]['domain'], 'CORP')
        self.assertNotIn('phone', users[0])

    async def test_requester_permission_error_has_actionable_english_message(self):
        client = me.ManageEngineClient({'endpoint': 'https://example.com', 'api_key': 'test'})
        client._get = AsyncMock(side_effect=me.ManageEngineError('HTTP 403', status=403))
        with self.assertRaisesRegex(me.ManageEngineError, 'full email address'):
            await client.list_requesters('wangj')
        client._get.assert_awaited_once()
