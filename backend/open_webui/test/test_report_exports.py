"""Report task routing without database/provider dependencies."""
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient


def require_user():
    raise HTTPException(status_code=401, detail='Authentication required')


class ReportExportRouteTests(unittest.TestCase):
    def setUp(self):
        auth = types.ModuleType('open_webui.utils.auth')
        auth.get_verified_user = require_user
        spec = importlib.util.spec_from_file_location('report_export_route_test', Path(__file__).parents[1] / 'routers' / 'reports.py')
        self.route = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'open_webui.utils.auth': auth}):
            spec.loader.exec_module(self.route)
        self.app = FastAPI()
        self.app.include_router(self.route.router, prefix='/api/v1/reports')
        self.app.state.MODELS = {'internal': {'id': 'internal'}}
        self.user = types.SimpleNamespace(id='user-1', role='user')
        self.payload = {'model': 'internal', 'messages': [{'role': 'system', 'content': 'Return JSON only'}, {'role': 'user', 'content': 'Source evidence'}], 'stream': False}
        self.access = AsyncMock()
        self.completion = AsyncMock(return_value={'choices': [{'message': {'content': '{}'}}]})
        self.inlet = AsyncMock(side_effect=lambda request, payload, user, models: payload)
        models = types.ModuleType('open_webui.utils.models')
        models.check_model_access = self.access
        models.get_all_models = AsyncMock()
        chat = types.ModuleType('open_webui.utils.chat')
        chat.generate_chat_completion = self.completion
        pipelines = types.ModuleType('open_webui.routers.pipelines')
        pipelines.process_pipeline_inlet_filter = self.inlet
        self.config = types.ModuleType('open_webui.config')
        self.config.BYPASS_ADMIN_ACCESS_CONTROL = True
        self.env = types.ModuleType('open_webui.env')
        self.env.BYPASS_MODEL_ACCESS_CONTROL = False
        self.stubs = patch.dict(sys.modules, {'open_webui.config': self.config, 'open_webui.env': self.env, 'open_webui.utils.models': models, 'open_webui.utils.chat': chat, 'open_webui.routers.pipelines': pipelines})
        self.stubs.start()
        self.addCleanup(self.stubs.stop)
        self.client = TestClient(self.app)

    def authenticate(self):
        self.app.dependency_overrides[require_user] = lambda: self.user

    def test_authentication_is_required(self):
        response = self.client.post('/api/v1/reports/chat/completions', json=self.payload)
        self.assertEqual(response.status_code, 401)
        self.completion.assert_not_called()

    def test_export_contract_does_not_inherit_model_document_prompt(self):
        self.authenticate()
        response = self.client.post('/api/v1/reports/chat/completions', json=self.payload)
        self.assertEqual(response.status_code, 200)
        self.access.assert_awaited_once_with(self.user, {'id': 'internal'})
        self.inlet.assert_awaited_once()
        args, kwargs = self.completion.call_args
        self.assertEqual(args[1]['messages'], self.payload['messages'])
        self.assertEqual(kwargs, {'bypass_system_prompt': True})
        self.assertNotIn('bypass_filter', kwargs)

    def test_denied_models_cannot_reach_the_provider(self):
        self.authenticate()
        self.access.side_effect = HTTPException(status_code=403, detail='Access denied')
        response = self.client.post('/api/v1/reports/chat/completions', json=self.payload)
        self.assertEqual(response.status_code, 403)
        self.inlet.assert_not_called()
        self.completion.assert_not_called()

    def test_admin_can_use_provider_model_without_workspace_registration(self):
        self.authenticate()
        self.user.role = 'admin'
        self.access.side_effect = Exception('Model not found')
        response = self.client.post('/api/v1/reports/chat/completions', json=self.payload)
        self.assertEqual(response.status_code, 200)
        self.access.assert_not_called()
        self.completion.assert_awaited_once()
        self.assertNotIn('bypass_filter', self.completion.call_args.kwargs)

    def test_admin_still_requires_access_when_admin_bypass_is_disabled(self):
        self.authenticate()
        self.user.role = 'admin'
        self.config.BYPASS_ADMIN_ACCESS_CONTROL = False
        self.access.side_effect = Exception('Model not found')
        response = self.client.post('/api/v1/reports/chat/completions', json=self.payload)
        self.assertEqual(response.status_code, 404)
        self.access.assert_awaited_once()
        self.completion.assert_not_called()

    def test_existing_global_access_configuration_is_respected(self):
        self.authenticate()
        self.env.BYPASS_MODEL_ACCESS_CONTROL = True
        self.access.side_effect = Exception('Model not found')
        response = self.client.post('/api/v1/reports/chat/completions', json=self.payload)
        self.assertEqual(response.status_code, 200)
        self.access.assert_not_called()

    def test_unregistered_model_for_normal_user_returns_404_not_500(self):
        self.authenticate()
        self.access.side_effect = Exception('Model not found')
        response = self.client.post('/api/v1/reports/chat/completions', json=self.payload)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()['detail'], 'Report model not found or access denied')
        self.inlet.assert_not_called()
        self.completion.assert_not_called()

    def test_model_missing_from_provider_cache_returns_404(self):
        self.authenticate()
        response = self.client.post('/api/v1/reports/chat/completions', json={**self.payload, 'model': 'missing'})
        self.assertEqual(response.status_code, 404)
        self.completion.assert_not_called()

    def test_report_tasks_cannot_enable_streaming(self):
        self.authenticate()
        response = self.client.post('/api/v1/reports/chat/completions', json={**self.payload, 'stream': True})
        self.assertEqual(response.status_code, 422)
        self.completion.assert_not_called()


if __name__ == '__main__':
    unittest.main()
