import { getOutputText } from '$lib/components/chat/Messages/structuredOutput';

export type ReportPdfRole = 'user' | 'assistant';

export type ReportSourceMessage = {
	role?: string;
	content?: string;
	output?: Parameters<typeof getOutputText>[0];
	model?: string;
	modelName?: string;
	timestamp?: number;
	meta?: {
		response_ended_at?: number;
		[key: string]: unknown;
	};
	[key: string]: unknown;
};

export type ReportPdfMessage = {
	index: number;
	role: ReportPdfRole;
	content: string;
	modelName: string;
	timestamp?: number;
};

const normalizeDeliveryText = (text: string): string =>
	text
		.replace(/[\u2010-\u2015\u2212]/g, '-')
		.replace(/[\u00a0\u202f]/g, ' ')
		.replace(/\s+/g, ' ')
		.trim()
		.toLowerCase();

const isDeliveryInstruction = (text: string): boolean => {
	const value = normalizeDeliveryText(text);
	if (!value) return false;

	return (
		/^(?:below|here)\s+(?:is|are)\b.*\b(?:template|document|report|sop|standard operating procedure)\b/.test(
			value
		) ||
		/^(?:please\s+)?(?:copy|paste)\b.*\b(?:word|word-processor|docx|editor|save)\b/.test(value) ||
		/^ready\s+to\s+be\s+saved\b/.test(value) ||
		/^save\s+(?:this|it|the\s+(?:content|contents|document|template|file))\b.*\bas\b/.test(value) ||
		/\bdocx-compatible\s+editor\b/.test(value) ||
		/\b(?:ready-to-use|ready to use)\b.*\b(?:template|document|sop)\b/.test(value)
	);
};

const stripDeliveryParentheticals = (text: string): string =>
	text.replace(/([(（])([^()（）\n]*)([)）])/g, (match, _open, inner: string) =>
		isDeliveryInstruction(inner) ? '' : match
	);

export const prepareDocumentMarkdown = (markdown: string): string => {
	const lines = (markdown ?? '').replace(/\r\n?/g, '\n').split('\n');
	const kept: string[] = [];
	let fence: string | null = null;

	for (const line of lines) {
		const fenceMatch = line.match(/^\s{0,3}(`{3,}|~{3,})/);
		if (fenceMatch) {
			const marker = fenceMatch[1][0];
			if (!fence) fence = marker;
			else if (fence === marker) fence = null;
			kept.push(line);
			continue;
		}

		if (fence) {
			kept.push(line);
			continue;
		}

		const withoutParentheticals = stripDeliveryParentheticals(line).trimEnd();
		const plain = withoutParentheticals
			.replace(/^\s{0,3}(?:>\s*)?(?:#{1,6}\s*)?/, '')
			.replace(/<[^>]*>/g, '')
			.replace(/^[*_`~]+|[*_`~]+$/g, '')
			.trim();

		if (isDeliveryInstruction(plain)) continue;
		kept.push(withoutParentheticals);
	}

	return kept
		.join('\n')
		.replace(/\n{3,}/g, '\n\n')
		.trim();
};

export const extractDocumentTitle = (markdown: string): string => {
	const lines = prepareDocumentMarkdown(markdown).split('\n');
	let fence: string | null = null;

	for (const line of lines) {
		const fenceMatch = line.match(/^\s{0,3}(`{3,}|~{3,})/);
		if (fenceMatch) {
			const marker = fenceMatch[1][0];
			if (!fence) fence = marker;
			else if (fence === marker) fence = null;
			continue;
		}
		if (fence) continue;

		const heading = line.match(/^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$/);
		if (!heading) continue;

		return heading[1]
			.replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1')
			.replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
			.replace(/[*_`~]/g, '')
			.trim();
	}

	return '';
};

export const sanitizeReportFileName = (name: string, fallback = 'conversation'): string => {
	const cleaned = (name ?? '')
		.replace(/[\\/:*?"<>|]+/g, '_')
		.replace(/\s+/g, ' ')
		.trim()
		.slice(0, 100);
	return cleaned || fallback;
};

export const formatReportTimestamp = (timestamp: number | undefined, locale = 'en-US'): string => {
	if (timestamp === undefined || timestamp === null || !Number.isFinite(timestamp)) return '';
	const milliseconds = timestamp < 1_000_000_000_000 ? timestamp * 1000 : timestamp;
	const date = new Date(milliseconds);
	if (Number.isNaN(date.getTime())) return '';
	return date.toLocaleString(locale, {
		year: 'numeric',
		month: 'short',
		day: 'numeric',
		hour: '2-digit',
		minute: '2-digit'
	});
};

export const normalizeReportMessages = (
	messages: ReportSourceMessage[] = []
): ReportPdfMessage[] => {
	const normalized: ReportPdfMessage[] = [];

	for (const message of messages) {
		if (message?.role !== 'user' && message?.role !== 'assistant') continue;

		const content = prepareDocumentMarkdown(getOutputText(message.output) || message.content || '');
		if (!content) continue;

		normalized.push({
			index: normalized.length + 1,
			role: message.role,
			content,
			modelName: message.modelName || message.model || '',
			timestamp:
				message.role === 'assistant'
					? (message.meta?.response_ended_at ?? message.timestamp)
					: message.timestamp
		});
	}

	return normalized;
};

export const selectDocumentMessages = (
	messages: ReportSourceMessage[] = []
): ReportPdfMessage[] => {
	const normalized = normalizeReportMessages(messages);
	const lastAssistant = [...normalized].reverse().find((message) => message.role === 'assistant');
	const selected = lastAssistant ?? normalized[normalized.length - 1];
	return selected ? [{ ...selected, index: 1 }] : [];
};

export const collectReportModelNames = (messages: ReportPdfMessage[]): string[] => {
	const names = new Set<string>();
	for (const message of messages) {
		if (message.role === 'assistant' && message.modelName) names.add(message.modelName);
	}
	return Array.from(names);
};
