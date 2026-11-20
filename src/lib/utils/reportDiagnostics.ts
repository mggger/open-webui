import { WEBUI_API_BASE_URL } from '$lib/constants';

export type ReportDiagnostic = {
	traceId: string;
	event: 'failed' | 'retry';
	stage: string;
	message: string;
	code: string;
	model?: string;
	requestId?: string;
	status?: number;
	section?: number;
	attempt?: number;
	elapsedMs?: number;
};

const safeMessage = (value: string) =>
	value
		.replace(/Bearer\s+\S+/gi, 'Bearer [redacted]')
		.replace(/\bsk-[\w-]+/g, '[redacted]')
		.replace(
			/(?:token|api[_-]?key|authorization)\s*[=:]\s*["']?[^\s,"'}]+/gi,
			'[redacted credential]'
		)
		.replace(/[\r\n\t]+/g, ' ')
		.slice(0, 1200);

export const reportErrorMessage = (error: unknown): string => {
	if (typeof error === 'string') return safeMessage(error);
	if (error && typeof error === 'object') {
		const value = error as any;
		const message =
			value.message ??
			value.detail?.message ??
			value.detail?.error?.message ??
			value.error?.message ??
			value.detail;
		if (typeof message === 'string') return safeMessage(message);
	}
	return 'Unknown report export error';
};

export class ReportExportError extends Error {
	constructor(
		public diagnostic: ReportDiagnostic,
		cause?: unknown
	) {
		super(diagnostic.message, { cause });
		this.name = 'ReportExportError';
	}
}

export const makeReportError = (
	error: unknown,
	context: Omit<ReportDiagnostic, 'message' | 'code' | 'event'>
): ReportExportError => {
	if (error instanceof ReportExportError) return error;
	const source = error as
		| { status?: number; requestId?: string; code?: string; name?: string }
		| undefined;
	return new ReportExportError(
		{
			...context,
			event: 'failed',
			message: reportErrorMessage(error),
			code:
				source?.code ??
				(source?.name === 'AbortError' ? 'REQUEST_TIMEOUT' : 'REPORT_EXPORT_FAILED'),
			...(typeof source?.status === 'number' ? { status: source.status } : {}),
			...(source?.requestId ? { requestId: source.requestId } : {})
		},
		error
	);
};

export const recordReportDiagnostic = (diagnostic: ReportDiagnostic, token: string): void => {
	const safe = { ...diagnostic, message: safeMessage(diagnostic.message) };
	// console.error is deliberately stripped by production builds; warnings are retained.
	console.warn('[report-export]', safe);
	if (!token) return;
	const controller = new AbortController();
	const timer = setTimeout(() => controller.abort(), 4000);
	void fetch(`${WEBUI_API_BASE_URL}/reports/diagnostics`, {
		method: 'POST',
		credentials: 'include',
		signal: controller.signal,
		headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
		body: JSON.stringify(safe)
	})
		.then((response) => {
			if (!response.ok)
				console.warn('[report-export] Diagnostic delivery failed', {
					traceId: safe.traceId,
					status: response.status
				});
		})
		.catch(() => {
			console.warn(
				'[report-export] Diagnostic delivery unavailable; details remain in the browser console',
				{ traceId: safe.traceId }
			);
		})
		.finally(() => clearTimeout(timer));
};

export const reportFailureDetails = (error: unknown, stage: string, token: string): string => {
	const failure = makeReportError(error, { traceId: crypto.randomUUID(), stage });
	recordReportDiagnostic(failure.diagnostic, token);
	const d = failure.diagnostic;
	return `${d.stage}${d.section ? ` · section ${d.section}` : ''}${d.status ? ` · HTTP ${d.status}` : ''}\n${d.message}\nReference: ${d.traceId}`;
};
