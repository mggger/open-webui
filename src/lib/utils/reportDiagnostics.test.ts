import { afterEach, describe, expect, it, vi } from 'vitest';
import { makeReportError, reportFailureDetails } from './reportDiagnostics';
import { generateOpenAIChatCompletion } from '$lib/apis/openai';

afterEach(() => {
	vi.restoreAllMocks();
});
describe('report diagnostics', () => {
	it('retains HTTP error details from completion requests', async () => {
		vi.spyOn(globalThis, 'fetch').mockResolvedValue(
			new Response(JSON.stringify({ error: { message: 'Model unavailable' } }), {
				status: 503,
				headers: { 'x-request-id': 'server-1' }
			})
		);
		await expect(
			generateOpenAIChatCompletion('test', {}, undefined, undefined, { requestId: 'client-1' })
		).rejects.toMatchObject({ message: 'Model unavailable', status: 503, requestId: 'server-1' });
	});
	it('records a sanitized failure and keeps the original trace', async () => {
		const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('{}'));
		const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
		const error = makeReportError(
			new Error('Access denied Bearer secret-value api_key=private-value'),
			{ traceId: 'trace-1', stage: 'writing', section: 3 }
		);
		const description = reportFailureDetails(error, 'export-pdf', 'session-token');
		expect(description).toContain('writing · section 3');
		expect(description).toContain('trace-1');
		expect(description).not.toContain('secret-value');
		expect(description).not.toContain('private-value');
		const body = String(fetchMock.mock.calls[0][1]?.body);
		expect(body).not.toContain('session-token');
		expect(JSON.parse(body)).toMatchObject({
			traceId: 'trace-1',
			stage: 'writing',
			event: 'failed'
		});
		expect(warn).toHaveBeenCalled();
	});
});
