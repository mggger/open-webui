"""ManageEngine ServiceDesk Plus (SDP) v3 client used by the ManageEngine chat tools.

Adapted from siem_report_agent's ``siem_report/datasources/ticketing.py``. Settings live
in the admin config store (Admin Settings > ManageEngine, see routers/manage_engine.py):

    manage_engine.endpoint     e.g. https://sdp.example.com:8080
    manage_engine.api_key      SDP technician authtoken (never returned to the client)
    manage_engine.verify_ssl   verify the server's TLS certificate
    manage_engine.timezone     IANA zone used for "today" / "this week" (default Australia/Sydney)
    manage_engine.admin_only   restrict the chat tool to admins

Filtering strategy: criteria are sent to SDP server-side where possible, and every filter
is also re-applied client-side on the returned rows. If SDP rejects the criteria (search
support varies between editions), the query is retried with no criteria and filtering
happens purely client-side over a bounded number of pages.
"""

import asyncio
import datetime as dt
import json
import logging
import re
from contextvars import ContextVar
from typing import Optional
from zoneinfo import ZoneInfo

import aiohttp


log = logging.getLogger(__name__)

ACCEPT = 'application/vnd.manageengine.sdp.v3+json'
PAGE_SIZE = 100
MAX_PAGES = 10  # client-side fallback scans at most this many pages (newest first)
MAX_RETURNED = 50
COMPLETED_STATUSES = {'resolved', 'closed', 'completed'}
CLOSED_STATUSES = COMPLETED_STATUSES | {'cancelled', 'canceled'}

PERIODS = (
    'today',
    'yesterday',
    'this_week',
    'last_week',
    'next_week',
    'this_month',
    'last_month',
    'last_7_days',
    'last_30_days',
)


class ManageEngineError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class ManageEngineValidationError(ManageEngineError):
    def __init__(self, message: str, fields: list[str], status: int | None = None):
        super().__init__(message, status)
        self.fields = fields


def creation_error_details(data, api_key: str) -> str:
    """Keep upstream error structure, but never log echoed credentials or request bodies."""
    sensitive = {'authtoken', 'apikey', 'authorization', 'password', 'secret', 'token',
                 'accesstoken', 'refreshtoken', 'headers', 'request', 'inputdata',
                 'payload', 'description', 'subject'}

    def clean(value, depth=0):
        if depth > 8:
            return '[truncated]'
        if isinstance(value, dict):
            return {
                str(key): '[redacted]' if re.sub(r'[^a-z]', '', str(key).lower()) in sensitive
                else clean(item, depth + 1)
                for key, item in list(value.items())[:100]
            }
        if isinstance(value, list):
            return [clean(item, depth + 1) for item in value[:100]]
        if isinstance(value, str):
            if api_key:
                value = value.replace(api_key, '[redacted]')
            value = re.sub(
                r'(?i)(authtoken|api[_-]?key|authorization|password|access[_-]?token)([\s:=]+)([^\s,;]+)',
                r'\1\2[redacted]', value,
            )
            return value[:4000]
        return value

    if isinstance(data, dict):
        error_keys = ('response_status', 'status', 'status_code', 'error_code', 'code',
                      'error', 'errors', 'message', 'messages', 'detail', 'details',
                      'field', 'fields', 'missing_fields', 'invalid_fields')
        errors = {key: data[key] for key in error_keys if key in data}
        if not errors:
            errors = {'message': 'No recognized error envelope', 'response_keys': list(data.keys())}
    else:
        errors = data
    # Redact before serialization, including keys and truncated output.
    text = json.dumps(clean(errors), ensure_ascii=True, default=str)
    if api_key:
        text = text.replace(api_key, '[redacted]')
    return text[:16000]


def creation_error_fields(data) -> list[str]:
    fields = set()

    def visit(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in ('field', 'field_name', 'fields', 'missing_fields', 'invalid_fields'):
                    items = item if isinstance(item, list) else [item]
                    for field in items:
                        if isinstance(field, str) and re.fullmatch(r'[a-zA-Z_][a-zA-Z0-9_.]*', field):
                            fields.add(field)
                elif key not in ('request', 'input_data', 'payload'):
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(data)
    return sorted(fields)


CONFIG_KEYS = {
    'endpoint': 'manage_engine.endpoint',
    'api_key': 'manage_engine.api_key',
    'verify_ssl': 'manage_engine.verify_ssl',
    'timezone': 'manage_engine.timezone',
    'admin_only': 'manage_engine.admin_only',
}
DEFAULT_TIMEZONE = 'Australia/Sydney'

# Request-local timezone prevents concurrent queries from changing one another's date bounds.
_timezone: ContextVar[ZoneInfo] = ContextVar('manage_engine_timezone', default=ZoneInfo(DEFAULT_TIMEZONE))


async def load_settings() -> dict:
    from open_webui.models.config import Config

    values = await Config.get_many(*CONFIG_KEYS.values())
    settings = {name: values.get(key) for name, key in CONFIG_KEYS.items()}
    settings['endpoint'] = (settings.get('endpoint') or '').strip()
    settings['api_key'] = (settings.get('api_key') or '').strip()
    settings['verify_ssl'] = settings.get('verify_ssl') is not False
    settings['timezone'] = (settings.get('timezone') or '').strip() or DEFAULT_TIMEZONE
    settings['admin_only'] = bool(settings.get('admin_only'))

    try:
        _timezone.set(ZoneInfo(settings['timezone']))
    except Exception:
        log.warning('Invalid ManageEngine timezone %r, falling back to UTC', settings['timezone'])
        _timezone.set(ZoneInfo('UTC'))
    return settings


def is_ready(settings: dict) -> bool:
    """Has the connection details needed to call the API; activation is per chat."""
    return bool(settings.get('endpoint') and settings.get('api_key'))


def get_timezone() -> ZoneInfo:
    return _timezone.get()


def now_local() -> dt.datetime:
    return dt.datetime.now(get_timezone())


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------


def _to_ms(value: dt.datetime) -> int:
    return int(value.timestamp() * 1000)


def _start_of_day(day: dt.date, tz: ZoneInfo) -> dt.datetime:
    return dt.datetime.combine(day, dt.time.min, tzinfo=tz)


def resolve_period(period: Optional[str]) -> Optional[tuple[dt.datetime, dt.datetime]]:
    """Map a named period to an inclusive [start, end) window in the configured timezone."""
    if not period:
        return None
    key = period.strip().lower().replace(' ', '_').replace('-', '_')
    tz = get_timezone()
    today = now_local().date()
    monday = today - dt.timedelta(days=today.weekday())
    first_of_month = today.replace(day=1)

    if key == 'today':
        start, end = today, today + dt.timedelta(days=1)
    elif key == 'yesterday':
        start, end = today - dt.timedelta(days=1), today
    elif key == 'this_week':
        start, end = monday, monday + dt.timedelta(days=7)
    elif key == 'last_week':
        start, end = monday - dt.timedelta(days=7), monday
    elif key == 'next_week':
        start, end = monday + dt.timedelta(days=7), monday + dt.timedelta(days=14)
    elif key == 'this_month':
        next_month = (first_of_month + dt.timedelta(days=32)).replace(day=1)
        start, end = first_of_month, next_month
    elif key == 'last_month':
        prev_month = (first_of_month - dt.timedelta(days=1)).replace(day=1)
        start, end = prev_month, first_of_month
    elif key == 'last_7_days':
        start, end = today - dt.timedelta(days=6), today + dt.timedelta(days=1)
    elif key == 'last_30_days':
        start, end = today - dt.timedelta(days=29), today + dt.timedelta(days=1)
    else:
        raise ManageEngineError(f'Unknown period {period!r}. Use one of: {", ".join(PERIODS)}')

    return _start_of_day(start, tz), _start_of_day(end, tz)


def parse_date(value: Optional[str], end_of_day: bool = False) -> Optional[dt.datetime]:
    """Parse YYYY-MM-DD (or an ISO datetime) in the configured timezone."""
    if not value:
        return None
    tz = get_timezone()
    text = value.strip()
    try:
        if len(text) == 10:
            day = dt.date.fromisoformat(text)
            return _start_of_day(day + dt.timedelta(days=1 if end_of_day else 0), tz)
        parsed = dt.datetime.fromisoformat(text.replace('Z', '+00:00'))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=tz)
    except ValueError as exc:
        raise ManageEngineError(f'Invalid date {value!r}; use YYYY-MM-DD') from exc


def _event_ms(ticket: dict, field: str) -> Optional[int]:
    value = (ticket.get(field) or {}).get('value') if isinstance(ticket.get(field), dict) else None
    if value in (None, ''):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _format_ms(ms: Optional[int]) -> Optional[str]:
    if ms is None:
        return None
    return dt.datetime.fromtimestamp(ms / 1000, get_timezone()).strftime('%Y-%m-%d %H:%M')


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------


class ManageEngineClient:
    def __init__(self, settings: dict):
        endpoint = settings.get('endpoint') or ''
        api_key = settings.get('api_key') or ''
        if not endpoint or not api_key:
            raise ManageEngineError(
                'ManageEngine is not configured. An admin must set the API endpoint and API key '
                'in Admin Settings > ManageEngine.'
            )
        self.base_url = endpoint.rstrip('/')
        self.headers = {'authtoken': api_key, 'Accept': ACCEPT}
        self.ssl: bool = bool(settings.get('verify_ssl', True))
        self.timeout = aiohttp.ClientTimeout(total=30)

    @classmethod
    async def create(cls) -> 'ManageEngineClient':
        return cls(await load_settings())

    def ticket_url(self, ticket_id) -> str:
        return f'{self.base_url}/WorkOrder.do?woMode=viewWO&woID={ticket_id}'

    async def _get(self, path: str, params: Optional[dict] = None, retries: int = 3) -> dict:
        url = f'{self.base_url}{path}'
        last_err: Exception | None = None
        for attempt in range(1, retries + 1):
            try:
                async with aiohttp.ClientSession(timeout=self.timeout, trust_env=True) as session:
                    async with session.get(url, params=params, headers=self.headers, ssl=self.ssl) as resp:
                        text = await resp.text()
                        if resp.status >= 400:
                            raise ManageEngineError(f'SDP API returned HTTP {resp.status}', status=resp.status)
                        try:
                            data = json.loads(text)
                        except ValueError as exc:
                            raise ManageEngineError('SDP returned invalid JSON. Check the API endpoint.') from exc
                        if not isinstance(data, dict):
                            raise ManageEngineError('SDP returned an unexpected response.')
                        statuses = data.get('response_status', [])
                        if isinstance(statuses, dict):
                            statuses = [statuses]
                        if any(str(item.get('status_code')) not in ('2000', 'None') or item.get('status') == 'failed' for item in statuses):
                            raise ManageEngineError('SDP rejected the API request. Check credentials and search fields.')
                        return data
            except (aiohttp.ClientConnectionError, asyncio.TimeoutError) as exc:
                last_err = exc
                log.warning('ManageEngine request failed (attempt %d/%d): %s', attempt, retries, exc)
                if attempt < retries:
                    await asyncio.sleep(1.5 * attempt)
        raise ManageEngineError(f'Could not reach ManageEngine: {last_err}')

    async def create_request(self, payload: dict) -> dict:
        """Submit once. A timeout may mean success upstream, so never retry a POST."""
        try:
            async with aiohttp.ClientSession(timeout=self.timeout, trust_env=True) as session:
                async with session.post(
                    f'{self.base_url}/api/v3/requests',
                    data={'input_data': json.dumps({'request': payload})},
                    headers=self.headers, ssl=self.ssl, allow_redirects=False,
                ) as response:
                    try:
                        data = await response.json(content_type=None)
                    except (ValueError, aiohttp.ClientError) as exc:
                        log.error(
                            'Manage Engine create request failed: method=POST path=/api/v3/requests '
                            'http_status=%s response_format=non-JSON exception=%s',
                            response.status, type(exc).__name__,
                        )
                        raise ManageEngineError('Ticket creation returned an unreadable response. Check ServiceDesk before trying again; a ticket may already exist.') from exc
                    statuses = data.get('response_status', []) if isinstance(data, dict) else []
                    if isinstance(statuses, dict):
                        statuses = [statuses]
                    if not isinstance(statuses, list):
                        statuses = []
                    if response.status >= 300 or any(
                        isinstance(item, dict) and (
                            str(item.get('status_code')) not in ('2000', 'None') or item.get('status') == 'failed'
                        ) for item in statuses
                    ):
                        log.error(
                            'Manage Engine create request failed: method=POST path=/api/v3/requests '
                            'http_status=%s submitted_fields=%s response=%s',
                            response.status, ','.join(sorted(payload)),
                            creation_error_details(data, self.headers.get('authtoken', '')),
                        )
                        if response.status in (401, 403):
                            raise ManageEngineError(
                                f'Ticket creation was denied (HTTP {response.status}). Check the API key and request creation permissions. Detailed API errors are in the server logs.',
                                status=response.status,
                            )
                        fields = creation_error_fields(data)
                        if fields and response.status in (200, 400, 422):
                            raise ManageEngineValidationError(
                                'Please provide or correct these ticket fields: ' + ', '.join(fields) + '.',
                                fields=fields, status=response.status,
                            )
                        hint = (' Check these fields: ' + ', '.join(fields) + '.') if fields else ''
                        raise ManageEngineError(
                            f'ServiceDesk rejected ticket creation (HTTP {response.status}). Check required fields and configured field values.' + hint +
                            ' Detailed API errors are in the server logs.',
                            status=response.status,
                        )
                    ticket = data.get('request') if isinstance(data, dict) else None
                    if not isinstance(ticket, dict) or not str(ticket.get('id', '')).isdigit():
                        raise ManageEngineError('Ticket creation returned no valid ticket ID. Check ServiceDesk before trying again; a ticket may already exist.')
                    return {'created': True, 'ticket': summarize_ticket(ticket, self)}
        except (aiohttp.ClientConnectionError, asyncio.TimeoutError) as exc:
            raise ManageEngineError('Ticket creation could not be confirmed. Check ServiceDesk before trying again; the request will not be retried automatically.') from exc

    async def list_requests_page(self, start_index: int, criteria: list[dict], sort_field: str) -> dict:
        list_info: dict = {
            'row_count': PAGE_SIZE,
            'start_index': start_index,
            'sort_field': sort_field,
            'sort_order': 'desc',
            'get_total_count': True,
        }
        if criteria:
            list_info['search_criteria'] = criteria
        data = await self._get('/api/v3/requests', {'input_data': json.dumps({'list_info': list_info})})
        if not isinstance(data.get('requests'), list):
            raise ManageEngineError('SDP response is missing the requests list. Check the API endpoint.')
        return data

    async def list_technicians(self) -> list[dict]:
        """Read the accessible directory, never infer technicians from existing tickets."""
        technicians = {}
        start_index = 1
        for _ in range(100):
            try:
                data = await self._get('/api/v3/technicians', {'input_data': json.dumps({
                    'list_info': {'row_count': PAGE_SIZE, 'start_index': start_index}
                })})
            except ManageEngineError as exc:
                if exc.status == 403:
                    raise ManageEngineError(
                        'Technician directory access was denied (HTTP 403). Ask your ServiceDesk '
                        'administrator to grant this API account permission to read technicians. '
                        'No ticket search was performed.', status=403,
                    ) from exc
                raise
            rows = data.get('technicians')
            if not isinstance(rows, list):
                raise ManageEngineError('Could not read the technician directory. Check API permissions.')
            previous_count = len(technicians)
            for row in rows:
                if row.get('id') is not None and row.get('name'):
                    technicians[str(row['id'])] = {
                        'id': str(row['id']), 'name': row['name'],
                        'email': row.get('email_id') or '',
                        'login_name': row.get('login_name') or '',
                    }
            has_more = (data.get('list_info') or {}).get('has_more_rows')
            if has_more is False or (has_more is None and len(rows) < PAGE_SIZE):
                return list(technicians.values())
            if not rows or len(technicians) == previous_count:
                break
            start_index += len(rows)
        raise ManageEngineError('The technician directory could not be fully loaded. Please try again or check API pagination.')

    async def list_requesters(self, login_name: Optional[str] = None) -> list[dict]:
        """Users can be requesters even when they also have technician roles."""
        users = {}
        start_index = 1
        for _ in range(100):
            info = {'row_count': PAGE_SIZE, 'start_index': start_index}
            if login_name:
                info['search_fields'] = {'login_name': login_name}
            try:
                data = await self._get('/api/v3/users', {'input_data': json.dumps({
                    'list_info': info, 'fields_required': ['name', 'login_name', 'email_id', 'domain'],
                })})
            except ManageEngineError as exc:
                if exc.status == 403:
                    raise ManageEngineError(
                        "Requester directory access was denied (HTTP 403). Provide the requester's full email address, "
                        'or ask your ServiceDesk administrator to grant this API account permission to read users.',
                        status=403,
                    ) from exc
                raise
            rows = data.get('users')
            if not isinstance(rows, list):
                raise ManageEngineError('Could not read the requester directory. Check API permissions.')
            previous_count = len(users)
            for row in rows:
                if row.get('id') is not None and row.get('name'):
                    users[str(row['id'])] = {
                        'id': str(row['id']), 'name': row['name'],
                        'email': row.get('email_id') or '', 'login_name': row.get('login_name') or '',
                        'domain': (row.get('domain') or {}).get('name', ''),
                    }
            more = (data.get('list_info') or {}).get('has_more_rows')
            if more is False or (more is None and len(rows) < PAGE_SIZE):
                return list(users.values())
            if not rows or len(users) == previous_count:
                break
            start_index += len(rows)
        raise ManageEngineError('The requester directory could not be fully loaded. Check API pagination or provide a full email address.')

    async def get_request(self, ticket_id: str) -> dict:
        data = await self._get(f'/api/v3/requests/{ticket_id}')
        return data.get('request') or {}

    async def test_connection(self) -> int:
        """Fetch one request to verify the endpoint and key. Returns the total count if known."""
        data = await self.list_requests_page(1, [], 'created_time')
        info = data.get('list_info') or {}
        return int(info.get('total_count') or len(data.get('requests') or []))

    async def get_request_notes(self, ticket_id: str) -> list[dict]:
        try:
            data = await self._get(f'/api/v3/requests/{ticket_id}/notes')
            return data.get('notes') or []
        except ManageEngineError as exc:
            log.info('Could not load notes for request %s: %s', ticket_id, exc)
            return []


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


def _name(ticket: dict, field: str) -> str:
    value = ticket.get(field)
    if isinstance(value, dict):
        return (value.get('name') or value.get('email_id') or '').strip()
    return str(value or '').strip()


def _contains(haystack: str, needle: Optional[str]) -> bool:
    return not needle or needle.strip().lower() in (haystack or '').lower()


def _status_matches(ticket: dict, state: str, status: Optional[str]) -> bool:
    name = _name(ticket, 'status').lower()
    if status:
        wanted = {s.strip().lower() for s in status.split(',') if s.strip()}
        if name not in wanted:
            return False
    if state == 'open':
        return name not in CLOSED_STATUSES
    if state == 'closed':
        return name in COMPLETED_STATUSES
    return True


DATE_FIELDS = {
    'created': 'created_time',
    'due': 'due_by_time',
    'resolved': 'resolved_time',
    'updated': 'last_updated_time',
}


def summarize_ticket(ticket: dict, client: ManageEngineClient) -> dict:
    ticket_id = ticket.get('id')
    summary = {
        'id': ticket_id,
        'display_id': ticket.get('display_id') or ticket_id,
        'subject': ticket.get('subject'),
        'status': _name(ticket, 'status') or None,
        'priority': _name(ticket, 'priority') or None,
        'technician': _name(ticket, 'technician') or 'Unassigned',
        'requester': _name(ticket, 'requester') or None,
        'account': _name(ticket, 'account') or None,
        'group': _name(ticket, 'group') or None,
        'created': _format_ms(_event_ms(ticket, 'created_time')),
        'due_by': _format_ms(_event_ms(ticket, 'due_by_time')),
        'resolved': _format_ms(_event_ms(ticket, 'resolved_time') or _event_ms(ticket, 'completed_time')),
        'overdue': None,
        'url': client.ticket_url(ticket_id) if ticket_id else None,
    }
    due_ms = _event_ms(ticket, 'due_by_time')
    if due_ms is not None and _name(ticket, 'status').lower() not in CLOSED_STATUSES:
        summary['overdue'] = due_ms < _to_ms(now_local())
    return {k: v for k, v in summary.items() if v is not None}


def _count(items: list[dict], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        value = item.get(key) or 'Unspecified'
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))


async def search_tickets(
    technician: Optional[str] = None,
    technician_id: Optional[str] = None,
    requester: Optional[str] = None,
    account: Optional[str] = None,
    keyword: Optional[str] = None,
    state: str = 'open',
    status: Optional[str] = None,
    priority: Optional[str] = None,
    overdue: bool = False,
    date_field: str = 'auto',
    period: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = 20,
) -> dict:
    client = await ManageEngineClient.create()

    state = (state or 'open').strip().lower()
    if state not in ('open', 'closed', 'all'):
        state = 'open'
    date_field = (date_field or 'auto').strip().lower()
    if date_field == 'auto':
        date_field = 'outstanding' if state == 'open' else 'resolved' if state == 'closed' else 'created'
    if date_field not in (*DATE_FIELDS, 'outstanding'):
        raise ManageEngineError('Invalid date field')
    field = DATE_FIELDS.get(date_field, 'created_time')
    limit = max(1, min(int(limit or 20), MAX_RETURNED))

    window = resolve_period(period)
    start = parse_date(date_from)
    end = parse_date(date_to, end_of_day=True)
    if window and not (start or end):
        start, end = window
    if start and end and start >= end:
        raise ManageEngineError('Start date must be before the end date')
    # Outstanding means the current backlog, including tickets created before the period.
    start_ms = _to_ms(start) if start and date_field != 'outstanding' else None
    end_ms = _to_ms(end) if end else None

    # Server-side criteria (re-checked client-side below).
    criteria: list[dict] = []

    def add(criterion: dict):
        if criteria:
            criterion['logical_operator'] = 'AND'
        criteria.append(criterion)

    if technician_id:
        add({'field': 'technician.id', 'condition': 'is', 'value': technician_id})
    elif technician:
        add({'field': 'technician.name', 'condition': 'contains', 'value': technician.strip()})
    if requester:
        add({'field': 'requester.name', 'condition': 'contains', 'value': requester.strip()})
    if account:
        add({'field': 'account.name', 'condition': 'contains', 'value': account.strip()})
    # Keyword also matches display_id; apply locally to avoid excluding numeric IDs.
    if status:
        add({'field': 'status.name', 'condition': 'is', 'values': [s.strip() for s in status.split(',') if s.strip()]})
    elif state == 'open':
        add({'field': 'status.name', 'condition': 'is not', 'values': ['Resolved', 'Closed', 'Completed', 'Cancelled', 'Canceled']})
    elif state == 'closed':
        add({'field': 'status.name', 'condition': 'is', 'values': ['Resolved', 'Closed', 'Completed']})
    if overdue:
        add({'field': 'due_by_time', 'condition': 'less than', 'value': str(_to_ms(now_local()))})
    if priority:
        add({'field': 'priority.name', 'condition': 'is', 'value': priority.strip()})
    if start_ms is not None and date_field != 'resolved':
        add({'field': field, 'condition': 'greater than', 'value': str(start_ms - 1)})
    if end_ms is not None and date_field != 'resolved':
        add({'field': field, 'condition': 'less than', 'value': str(end_ms)})

    sort_field = 'due_by_time' if field == 'due_by_time' else 'created_time'

    def matches(ticket: dict) -> bool:
        if overdue:
            due = _event_ms(ticket, 'due_by_time')
            if due is None or due >= _to_ms(now_local()) or _name(ticket, 'status').lower() in CLOSED_STATUSES:
                return False
        if technician_id:
            if str((ticket.get('technician') or {}).get('id')) != technician_id:
                return False
        elif not _contains(_name(ticket, 'technician'), technician):
            return False
        if not _contains(_name(ticket, 'requester'), requester):
            return False
        if not _contains(_name(ticket, 'account'), account):
            return False
        if keyword and not (
            _contains(ticket.get('subject') or '', keyword) or _contains(str(ticket.get('display_id') or ''), keyword)
        ):
            return False
        if not _status_matches(ticket, state, status):
            return False
        if priority and _name(ticket, 'priority').lower() != priority.strip().lower():
            return False
        if start_ms is not None or end_ms is not None:
            ms = _event_ms(ticket, field)
            if date_field == 'resolved':
                ms = ms or _event_ms(ticket, 'completed_time')
            if ms is None:
                return False
            if start_ms is not None and ms < start_ms:
                return False
            if end_ms is not None and ms >= end_ms:
                return False
        return True

    async def run(use_criteria: bool) -> tuple[list[dict], bool]:
        found: list[dict] = []
        start_index = 1
        truncated = False
        for page in range(MAX_PAGES):
            data = await client.list_requests_page(start_index, criteria if use_criteria else [], sort_field)
            rows = data.get('requests') or []
            found.extend(t for t in rows if matches(t))
            has_more = (data.get('list_info') or {}).get('has_more_rows')
            if not rows or not has_more:
                break
            start_index += len(rows)
            if page == MAX_PAGES - 1:
                truncated = True
        return found, truncated

    used_fallback = False
    try:
        tickets, truncated = await run(True)
    except ManageEngineError as exc:
        if not criteria or exc.status not in (400, 422):
            raise
        log.info('SDP rejected search criteria, falling back to client-side filtering: %s', exc)
        used_fallback = True
        tickets, truncated = await run(False)

    summaries = [summarize_ticket(t, client) for t in tickets]
    result = {
        'today': now_local().strftime('%Y-%m-%d (%A)'),
        'filters': {
            k: v
            for k, v in {
                'technician': technician,
                'requester': requester,
                'account': account,
                'keyword': keyword,
                'state': state,
                'status': status,
                'priority': priority,
                'overdue': overdue,
                'date_field': date_field if (start or end) else None,
                'from': start.strftime('%Y-%m-%d') if start else None,
                'to (exclusive)': end.strftime('%Y-%m-%d') if end else None,
            }.items()
            if v
        },
        'complete': not truncated,
        'timezone': str(get_timezone()),
        'date_semantics': 'Current outstanding tickets created before the range end; includes older backlog, not historical status.' if date_field == 'outstanding' else date_field,
        'total_matched': len(summaries),
        'by_status': _count(summaries, 'status'),
        'by_priority': _count(summaries, 'priority'),
        'by_technician': _count(summaries, 'technician'),
        'tickets': summaries[:limit],
    }
    if len(summaries) > limit:
        result['note'] = f'Showing {limit} of {len(summaries)} matching tickets.'
    if truncated:
        result['warning'] = (
            f'Only the newest {MAX_PAGES * PAGE_SIZE} tickets were scanned; results may be incomplete. '
            'Narrow the search (e.g. add a period or technician).'
        )
    if used_fallback:
        result['search_mode'] = 'client-side'
    return result


async def get_ticket(ticket_id: str, include_notes: bool = True) -> dict:
    client = await ManageEngineClient.create()
    ticket_id = str(ticket_id).strip().lstrip('#')
    if not re.fullmatch(r'[0-9]+', ticket_id):
        raise ManageEngineError('Ticket ID must contain digits only')
    ticket = await client.get_request(ticket_id)
    if not ticket:
        raise ManageEngineError(f'Ticket {ticket_id} not found')

    detail = summarize_ticket(ticket, client)
    description = ticket.get('description') or ''
    if description:
        detail['description'] = _strip_html(description)[:4000]
    for field in ('category', 'subcategory', 'site', 'mode', 'request_type', 'impact', 'urgency', 'level'):
        name = _name(ticket, field)
        if name:
            detail[field] = name
    resolution = (ticket.get('resolution') or {}).get('content') if isinstance(ticket.get('resolution'), dict) else None
    if resolution:
        detail['resolution'] = _strip_html(resolution)[:2000]

    if include_notes:
        notes = await client.get_request_notes(ticket_id)
        detail['notes'] = [
            {
                'by': _name(note, 'created_by') or None,
                'at': _format_ms(_event_ms(note, 'created_time')),
                'text': _strip_html(note.get('description') or '')[:1000],
            }
            for note in notes[:10]
        ]
    return detail


def _strip_html(value: str) -> str:
    import html
    import re

    text = re.sub(r'<\s*br\s*/?>|</p>|</div>|</li>', '\n', value, flags=re.I)
    text = re.sub(r'<[^>]+>', '', text)
    text = html.unescape(text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()
