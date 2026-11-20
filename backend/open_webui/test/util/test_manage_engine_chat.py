import json
import unittest
import sys
from types import ModuleType, SimpleNamespace
from starlette.requests import Request
from unittest.mock import AsyncMock, patch

from open_webui.utils import manage_engine as me
from open_webui.utils.manage_engine_chat import retrieve_tickets, chat_ticket_context


class ManageEngineChatTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.settings = {'enable': True, 'endpoint': 'https://sdp.example.com',
                         'api_key': 'test', 'admin_only': False}
        self.loader = patch.object(me, 'load_settings', AsyncMock(return_value=self.settings))
        self.loader.start()
        self.directory = patch.object(me.ManageEngineClient, 'list_technicians', AsyncMock(return_value=[
            {'id': '7', 'name': 'wang', 'login_name': 'wangj', 'email': 'wangj@example.com'}
        ]))
        self.directory.start()
        self.messages = [{'role': 'user', 'content': 'wang剩下的tickets'}]
        self.plan = AsyncMock(return_value=json.dumps({
            'action': 'search', 'technician': 'wang', 'state': 'open',
        }))

    def tearDown(self):
        self.loader.stop()
        self.directory.stop()

    async def test_extract_then_query_real_client_path(self):
        client = me.ManageEngineClient(self.settings)
        client.list_requests_page = AsyncMock(return_value={
            'requests': [{'id': '42', 'subject': 'Investigate alert',
                          'status': {'name': 'Open'}, 'technician': {'id': '7', 'name': 'Wang'}}],
            'list_info': {'has_more_rows': False},
        })
        with patch.object(me.ManageEngineClient, 'create', AsyncMock(return_value=client)):
            result = await retrieve_tickets(self.messages, self.plan, 'user')
        self.plan.assert_awaited_once()
        client.list_requests_page.assert_awaited_once()
        self.assertEqual(result['tickets'][0]['id'], '42')
        self.assertEqual(result['filters']['technician'], 'wang')
        self.assertEqual(result['filters']['state'], 'open')

    async def test_missing_config_fails_before_model(self):
        self.settings['api_key'] = ''
        with self.assertRaisesRegex(me.ManageEngineError, 'API'):
            await retrieve_tickets(self.messages, self.plan, 'admin')
        self.plan.assert_not_awaited()

    async def test_obsolete_admin_switch_does_not_block_chat_query(self):
        self.settings['enable'] = False
        with patch.object(me, 'search_tickets', AsyncMock(return_value={'tickets': []})) as search:
            await retrieve_tickets(self.messages, self.plan, 'admin')
        self.plan.assert_awaited_once()
        search.assert_awaited_once()

    async def test_admin_restriction_checked_before_model(self):
        self.settings['admin_only'] = True
        with self.assertRaisesRegex(me.ManageEngineError, 'administrators'):
            await retrieve_tickets(self.messages, self.plan, 'user')
        self.plan.assert_not_awaited()

    async def test_invalid_plan_does_not_query_all_tickets(self):
        self.plan.return_value = 'I think Wang has three tickets.'
        with patch.object(me, 'search_tickets', AsyncMock()) as search:
            with self.assertRaises(me.ManageEngineError):
                await retrieve_tickets(self.messages, self.plan, 'user')
            search.assert_not_awaited()

    async def test_clarification_is_not_a_search(self):
        self.plan.return_value = '{"action":"clarify","clarification":"哪位负责人？"}'
        with patch.object(me, 'search_tickets', AsyncMock()) as search:
            result = await retrieve_tickets(self.messages, self.plan, 'user')
            search.assert_not_awaited()
        self.assertEqual(result['clarification'], '哪位负责人？')

    async def test_details_are_fetched(self):
        self.plan.return_value = '```json\n{"action":"detail","ticket_id":"42"}\n```'
        with patch.object(me, 'get_ticket', AsyncMock(return_value={'id': '42'})) as detail:
            result = await retrieve_tickets(self.messages, self.plan, 'user')
            detail.assert_awaited_once_with('42')
        self.assertEqual(result['id'], '42')

    async def test_query_failure_propagates_instead_of_empty_results(self):
        with patch.object(me, 'search_tickets', AsyncMock(side_effect=me.ManageEngineError('HTTP 401'))):
            with self.assertRaisesRegex(me.ManageEngineError, '401'):
                await retrieve_tickets(self.messages, self.plan, 'user')

    async def test_unknown_arguments_cannot_reach_api(self):
        self.plan.return_value = '{"action":"search","api_key":"injected"}'
        with patch.object(me, 'search_tickets', AsyncMock()) as search:
            with self.assertRaises(me.ManageEngineError):
                await retrieve_tickets(self.messages, self.plan, 'user')
            search.assert_not_awaited()

    async def test_planner_isolated_and_compatible_with_provider(self):
        parent_metadata = {'task': 'chat', 'chat_id': 'original-chat', 'tools': {'ask_user': {}}}
        request = Request({'type': 'http', 'state': {'metadata': parent_metadata}})
        completion = AsyncMock(return_value={'choices': [{'message': {'content': self.plan.return_value}}]})
        chat_module = ModuleType('open_webui.utils.chat')
        chat_module.generate_chat_completion = completion
        misc_module = ModuleType('open_webui.utils.misc')
        misc_module.get_content_from_message = lambda message: message.get('content')
        emitter = AsyncMock()
        with patch.dict(sys.modules, {'open_webui.utils.chat': chat_module, 'open_webui.utils.misc': misc_module}):
            with patch.object(me, 'search_tickets', AsyncMock(return_value={'tickets': [], 'total_matched': 0})):
                result = await chat_ticket_context(request, {'model': 'configured-model', 'messages': self.messages}, SimpleNamespace(role='admin'), emitter)
        call = completion.call_args
        self.assertNotIn('tools', call.kwargs['form_data'])  # Provider rejects tools: [].
        self.assertEqual(call.kwargs['form_data']['model'], 'configured-model')
        self.assertEqual(call.kwargs['form_data']['response_format'], {'type': 'json_object'})
        self.assertTrue(call.kwargs['bypass_system_prompt'])
        self.assertNotIn('bypass_filter', call.kwargs)  # Model ACL remains enforced.
        self.assertIsNot(call.args[0], request)
        self.assertEqual(call.args[0].state.metadata, {'task': 'manage_engine_query'})
        self.assertIs(request.state.metadata, parent_metadata)
        self.assertEqual(result['total_matched'], 0)
        self.assertTrue(emitter.call_args.args[0]['data']['done'])


    async def test_login_alias_queries_exact_technician_id(self):
        self.plan.return_value = '{"action":"search","technician":"wangj"}'
        with patch.object(me, 'search_tickets', AsyncMock(return_value={'tickets': []})) as search:
            result = await retrieve_tickets(self.messages, self.plan, 'admin')
        self.assertEqual(search.call_args.kwargs['technician_id'], '7')
        self.assertEqual(result['matched_technician']['name'], 'wang')
        self.plan.assert_awaited_once()

    async def test_fuzzy_match_uses_configured_model_then_verified_id(self):
        self.plan.side_effect = [
            '{"action":"search","technician":"wangjj"}',
            '{"confidence":"high","technician_id":"7","candidate_ids":["7"]}',
        ]
        with patch.object(me, 'search_tickets', AsyncMock(return_value={'tickets': []})) as search:
            await retrieve_tickets(self.messages, self.plan, 'admin')
        self.assertEqual(self.plan.await_count, 2)
        self.assertEqual(search.call_args.kwargs['technician_id'], '7')

    async def test_ambiguous_identity_lists_options_without_querying_tickets(self):
        self.plan.side_effect = [
            '{"action":"search","technician":"wan","period":"this_week","state":"closed"}',
            '{"confidence":"uncertain","candidate_ids":["7"]}',
        ]
        with patch.object(me, 'search_tickets', AsyncMock()) as search:
            result = await retrieve_tickets(self.messages, self.plan, 'admin')
        search.assert_not_awaited()
        self.assertIn('1. wang (wangj) [ID: 7]', result['clarification'])
        self.assertEqual(result['pending_query']['period'], 'this_week')
        self.assertEqual(result['pending_query']['state'], 'closed')
        # The next extraction receives the original query and displayed choices.
        history = [*self.messages, {'role': 'assistant', 'content': result['clarification']},
                   {'role': 'user', 'content': '1'}]
        self.plan.side_effect = None
        self.plan.return_value = '{"action":"search","technician":"wangj","period":"this_week","state":"closed"}'
        with patch.object(me, 'search_tickets', AsyncMock(return_value={'tickets': []})) as search:
            await retrieve_tickets(history, self.plan, 'admin')
        self.assertEqual(self.plan.call_args.args[1], history)
        self.assertEqual(search.call_args.kwargs['technician_id'], '7')
        self.assertEqual(search.call_args.kwargs['period'], 'this_week')
        self.assertEqual(search.call_args.kwargs['state'], 'closed')

    async def test_invented_technician_id_cannot_reach_ticket_api(self):
        self.plan.side_effect = ['{"action":"search","technician":"wan"}',
                                 '{"confidence":"high","technician_id":"999"}']
        with patch.object(me, 'search_tickets', AsyncMock()) as search:
            with self.assertRaisesRegex(me.ManageEngineError, 'unknown technician'):
                await retrieve_tickets(self.messages, self.plan, 'admin')
        search.assert_not_awaited()

    async def test_explicit_directory_request_lists_technicians(self):
        self.plan.return_value = '{"action":"technicians"}'
        with patch.object(me, 'search_tickets', AsyncMock()) as search:
            result = await retrieve_tickets(self.messages, self.plan, 'admin')
        search.assert_not_awaited()
        self.assertEqual(result['technicians'][0]['login_name'], 'wangj')
        self.assertEqual(result['total_technicians'], 1)


class TechnicianDirectoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_directory_pagination_and_minimal_identity_fields(self):
        client = me.ManageEngineClient({'endpoint': 'https://example.com', 'api_key': 'test'})
        client._get = AsyncMock(side_effect=[
            {'technicians': [{'id': '1', 'name': 'Wang', 'phone': 'private'}], 'list_info': {'has_more_rows': True}},
            {'technicians': [{'id': '2', 'name': 'J Wang', 'login_name': 'wangj'}], 'list_info': {'has_more_rows': False}},
        ])
        rows = await client.list_technicians()
        self.assertEqual(len(rows), 2)
        self.assertNotIn('phone', rows[0])
        second = client._get.call_args_list[1]
        self.assertEqual(second.args[0], '/api/v3/technicians')
        self.assertEqual(json.loads(second.args[1]['input_data'])['list_info']['start_index'], 2)

    async def test_directory_permission_error_is_not_an_empty_result(self):
        client = me.ManageEngineClient({'endpoint': 'https://example.com', 'api_key': 'test'})
        client._get = AsyncMock(side_effect=me.ManageEngineError('SDP API returned HTTP 403', status=403))
        with self.assertRaisesRegex(me.ManageEngineError, '403'):
            await client.list_technicians()


class ManageEngineRoutingTests(unittest.IsolatedAsyncioTestCase):
    """Exercise the actual middleware function with isolated application dependencies."""

    def setUp(self):
        import ast
        from pathlib import Path
        from fastapi import HTTPException
        source = Path(__file__).resolve().parents[2] / 'utils' / 'middleware.py'
        tree = ast.parse(source.read_text())
        function = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'process_chat_payload')
        # Avoid booting databases, Redis, and model registries just to test routing.
        function.decorator_list = []
        for arg in function.args.args:
            arg.annotation = None
        function.returns = None
        self.knowledge = AsyncMock(side_effect=AssertionError('knowledge must not run'))
        self.emitter = AsyncMock()
        self.namespace = {
            'apply_params_to_form_data': lambda data, model: data,
            'log': SimpleNamespace(debug=lambda *a: None, exception=lambda *a: None),
            'is_saved_chat_id': lambda value: False,
            'process_messages_with_output': lambda messages, **kw: messages,
            'sanitize_tool_pairs': lambda messages: messages,
            'get_reasoning_format': lambda model: 'tags',
            'get_event_emitter': AsyncMock(return_value=self.emitter),
            'normalize_messages_for_model': lambda data: data,
            'HTTPException': HTTPException,
            'ENABLE_FUNCTIONS': True,
            'get_filter_functions': self.knowledge,
            'chat_completion_files_handler': self.knowledge,
            'chat_file_search_handler': self.knowledge,
            'process_pipeline_inlet_filter': self.knowledge,
        }
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), 'exec'), self.namespace)
        self.process = self.namespace['process_chat_payload']
        self.metadata = {'session_id': 'session', 'params': {'function_calling': 'legacy'},
                         'features': {'manage_engine': True}, 'tools': {'ask_user': {}},
                         'files': [{'id': 'knowledge'}]}
        self.form = {
            'model': 'configured-model', 'stream': True,
            'messages': [{'role': 'system', 'content': 'Search knowledge first'},
                         {'role': 'user', 'content': 'wang剩下的tickets'}],
            'features': {'manage_engine': True, 'web_search': True},
            'files': [{'id': 'knowledge'}], 'file_search': {'enabled': True},
            'tools': [{'type': 'function', 'function': {'name': 'ask_user'}}],
            'tool_ids': ['knowledge'], 'tool_choice': 'required', 'skill_ids': ['knowledge'],
        }
        self.model = {'id': 'configured-model', 'info': {'meta': {'knowledge': [{'id': 'kb'}]}}}

    async def test_enabled_mode_skips_knowledge_and_preserves_original_question(self):
        from open_webui.utils import manage_engine_chat
        result = {'tickets': [{'id': '42', 'subject': 'API result'}], 'total_matched': 1}
        with patch.object(manage_engine_chat, 'chat_ticket_context', AsyncMock(return_value=result)) as retrieval:
            data, metadata, events, paused = await self.process(None, self.form, SimpleNamespace(role='admin'), self.metadata, self.model)
        retrieval.assert_awaited_once()
        self.knowledge.assert_not_awaited()
        self.assertFalse(paused)
        self.assertEqual(data['messages'][1], {'role': 'user', 'content': 'wang剩下的tickets'})
        self.assertIn('API result', data['messages'][0]['content'])
        self.assertNotIn('Search knowledge first', data['messages'][0]['content'])
        for key in ('tools', 'tool_ids', 'tool_choice', 'files', 'file_search', 'features', 'skill_ids'):
            self.assertNotIn(key, data)
        self.assertEqual(metadata['tools'], {})
        self.assertFalse(metadata['include_file_context'])
        self.assertTrue(metadata['manage_engine_retrieved'])

    async def test_ingress_metadata_retains_mode_if_features_missing(self):
        from open_webui.utils import manage_engine_chat
        self.form.pop('features')
        with patch.object(manage_engine_chat, 'chat_ticket_context', AsyncMock(return_value={'tickets': []})) as retrieval:
            await self.process(None, self.form, SimpleNamespace(role='admin'), self.metadata, self.model)
        retrieval.assert_awaited_once()
        self.knowledge.assert_not_awaited()

    async def test_api_failure_never_falls_back_to_knowledge(self):
        from fastapi import HTTPException
        from open_webui.utils import manage_engine_chat
        with patch.object(manage_engine_chat, 'chat_ticket_context', AsyncMock(side_effect=me.ManageEngineError('HTTP 401'))):
            with self.assertRaises(HTTPException) as error:
                await self.process(None, self.form, SimpleNamespace(role='admin'), self.metadata, self.model)
        self.assertEqual(error.exception.status_code, 400)
        self.assertIn('401', error.exception.detail)
        self.knowledge.assert_not_awaited()

    async def test_disabled_mode_continues_normal_processing(self):
        self.form['features']['manage_engine'] = False
        with self.assertRaisesRegex(AssertionError, 'knowledge must not run'):
            await self.process(None, self.form, SimpleNamespace(role='admin'), self.metadata, self.model)
        self.knowledge.assert_awaited_once()

    async def test_saved_chat_loads_latest_question_before_retrieval(self):
        from open_webui.utils import manage_engine_chat
        self.metadata.update({'chat_id': 'saved-chat', 'user_message_id': 'latest'})
        self.form['messages'] = [{'role': 'system', 'content': 'Search knowledge first'}]
        self.namespace.update({
            'is_saved_chat_id': lambda value: value == 'saved-chat',
            'Chats': SimpleNamespace(get_chat_by_id=AsyncMock(return_value=None)),
            'load_messages_from_db': AsyncMock(return_value=[{'role': 'user', 'content': 'wang本周已完成的tickets'}]),
            'get_system_message': lambda messages: next((m for m in messages if m['role'] == 'system'), None),
        })
        async def retrieve(request, data, user, emitter):
            self.assertEqual(data['messages'][-1]['content'], 'wang本周已完成的tickets')
            return {'tickets': [], 'filters': {'technician': 'wang', 'state': 'closed', 'period': 'this_week'}}
        with patch.object(manage_engine_chat, 'chat_ticket_context', side_effect=retrieve):
            data, _, _, _ = await self.process(None, self.form, SimpleNamespace(role='admin'), self.metadata, self.model)
        self.assertEqual(data['messages'][-1]['content'], 'wang本周已完成的tickets')
        self.knowledge.assert_not_awaited()
