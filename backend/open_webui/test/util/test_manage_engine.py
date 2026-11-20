import datetime as dt
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

from open_webui.utils import manage_engine as me


def ticket(id, status='Open', created='2026-10-01', completed=None, technician='Wang'):
    row = {'id': str(id), 'display_id': str(id), 'subject': 'Network support',
           'technician': {'name': technician}, 'status': {'name': status},
           'created_time': {'value': me._to_ms(me.parse_date(created))}}
    if completed:
        row['completed_time'] = {'value': me._to_ms(me.parse_date(completed))}
    return row


class ManageEngineTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.token = me._timezone.set(ZoneInfo('Asia/Shanghai'))
        self.client = me.ManageEngineClient({'endpoint': 'https://sdp.example.com', 'api_key': 'test'})
        self.create = patch.object(me.ManageEngineClient, 'create', AsyncMock(return_value=self.client))
        self.create.start()
        self.clock = patch.object(me, 'now_local', return_value=dt.datetime(2026, 11, 20, 12, tzinfo=ZoneInfo('Asia/Shanghai')))
        self.clock.start()

    def tearDown(self):
        self.create.stop()
        self.clock.stop()
        me._timezone.reset(self.token)

    def rows(self, rows):
        self.client.list_requests_page = AsyncMock(return_value={'requests': rows, 'list_info': {'has_more_rows': False}})

    def test_week_and_inclusive_end(self):
        start, end = me.resolve_period('this_week')
        self.assertEqual(str(start.date()), '2026-11-16')
        self.assertEqual(str(end.date()), '2026-11-23')
        self.assertEqual(me.parse_date('2026-11-22', True), end)

    async def test_outstanding_includes_older_backlog(self):
        self.rows([ticket(1), ticket(2, 'Closed'), ticket(3, technician='Lee'), ticket(4, created='2026-11-24')])
        result = await me.search_tickets(technician='wang', period='this_week')
        self.assertEqual([t['id'] for t in result['tickets']], ['1'])
        self.assertEqual(result['filters']['date_field'], 'outstanding')

    async def test_completed_uses_completion_not_creation(self):
        self.rows([ticket(1, 'Closed', completed='2026-11-18'), ticket(2, 'Closed', completed='2026-11-01'), ticket(3, 'Cancelled', completed='2026-11-18')])
        result = await me.search_tickets(state='closed', period='this_week')
        self.assertEqual([t['id'] for t in result['tickets']], ['1'])
        self.assertIn('resolved', result['tickets'][0])

    async def test_explicit_created_range(self):
        self.rows([ticket(1, created='2026-11-20'), ticket(2, created='2026-11-21')])
        result = await me.search_tickets(date_field='created', date_from='2026-11-20', date_to='2026-11-20')
        self.assertEqual(result['total_matched'], 1)

    async def test_invalid_range(self):
        with self.assertRaises(me.ManageEngineError):
            await me.search_tickets(date_from='2026-11-22', date_to='2026-11-20')

    async def test_pagination_and_counts_before_limit(self):
        self.client.list_requests_page = AsyncMock(side_effect=[
            {'requests': [ticket(1)], 'list_info': {'has_more_rows': True}},
            {'requests': [ticket(2)], 'list_info': {'has_more_rows': False}},
        ])
        result = await me.search_tickets(limit=1)
        self.assertEqual(result['total_matched'], 2)
        self.assertEqual(len(result['tickets']), 1)
        self.assertTrue(result['complete'])
        self.assertEqual(self.client.list_requests_page.call_args.args[0], 2)

    async def test_truncation_is_explicit(self):
        self.client.list_requests_page = AsyncMock(return_value={'requests': [ticket(1)], 'list_info': {'has_more_rows': True}})
        with patch.object(me, 'MAX_PAGES', 1):
            result = await me.search_tickets()
        self.assertFalse(result['complete'])
        self.assertIn('warning', result)

    async def test_unsupported_filter_falls_back(self):
        self.client.list_requests_page = AsyncMock(side_effect=[me.ManageEngineError('unsupported', status=400), {'requests': [ticket(1), ticket(2, technician='Lee')]}])
        result = await me.search_tickets(technician='wang')
        self.assertEqual(result['total_matched'], 1)
        self.assertEqual(result['search_mode'], 'client-side')

    async def test_auth_failure_never_falls_back(self):
        self.client.list_requests_page = AsyncMock(side_effect=me.ManageEngineError('unauthorized', status=401))
        with self.assertRaises(me.ManageEngineError):
            await me.search_tickets(technician='wang')
        self.client.list_requests_page.assert_awaited_once()

    async def test_invalid_ticket_id(self):
        self.client.get_request = AsyncMock()
        with self.assertRaises(me.ManageEngineError):
            await me.get_ticket('../users')
        self.client.get_request.assert_not_awaited()

    async def test_overdue_excludes_completed_and_future_due(self):
        rows = [ticket(1), ticket(2, 'Closed'), ticket(3)]
        for row, day in zip(rows, ['2026-11-19', '2026-11-19', '2026-11-21']):
            row['due_by_time'] = {'value': me._to_ms(me.parse_date(day))}
        self.rows(rows)
        result = await me.search_tickets(overdue=True)
        self.assertEqual([t['id'] for t in result['tickets']], ['1'])

    async def test_http_wire_format_and_application_failure(self):
        response = MagicMock()
        response.status = 200
        response.text = AsyncMock(return_value='{"response_status":{"status_code":4000,"status":"failed"}}')
        session = MagicMock()
        session.get.return_value.__aenter__ = AsyncMock(return_value=response)
        factory = MagicMock()
        factory.return_value.__aenter__ = AsyncMock(return_value=session)
        with patch.object(me.aiohttp, 'ClientSession', factory):
            with self.assertRaises(me.ManageEngineError):
                await self.client.list_requests_page(1, [], 'created_time')
        kwargs = session.get.call_args.kwargs
        self.assertEqual(kwargs['headers']['authtoken'], 'test')
        self.assertEqual(kwargs['headers']['Accept'], me.ACCEPT)
        self.assertIn('input_data', kwargs['params'])

    async def test_invalid_endpoint_payload_is_not_empty_success(self):
        self.client._get = AsyncMock(return_value={})
        with self.assertRaises(me.ManageEngineError):
            await self.client.test_connection()

    async def test_keyword_matches_display_id(self):
        self.rows([ticket(123), ticket(456)])
        result = await me.search_tickets(keyword='123')
        self.assertEqual(result['total_matched'], 1)


if __name__ == '__main__':
    unittest.main()
