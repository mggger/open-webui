import { describe, expect, it } from 'vitest';

import {
	collectReportModelNames,
	extractDocumentTitle,
	formatReportTimestamp,
	normalizeReportMessages,
	prepareDocumentMarkdown,
	sanitizeReportFileName,
	selectDocumentMessages
} from './exportReportPdfModel';

describe('exportReportPdfModel', () => {
	it('sanitizes file names and falls back when empty', () => {
		expect(sanitizeReportFileName('  risk: "report" / draft  ')).toBe('risk_ _report_ _ draft');
		expect(sanitizeReportFileName('   ', 'conversation')).toBe('conversation');
	});

	it('formats second and millisecond timestamps and rejects invalid values', () => {
		expect(formatReportTimestamp(1_700_000_000, 'en-US')).toContain('2023');
		expect(formatReportTimestamp(1_700_000_000_000, 'en-US')).toContain('2023');
		expect(formatReportTimestamp(undefined)).toBe('');
		expect(formatReportTimestamp(Number.NaN)).toBe('');
	});

	it('normalizes the visible conversation branch and prefers structured output text', () => {
		const messages = normalizeReportMessages([
			{ role: 'system', content: 'hidden system prompt' },
			{ role: 'user', content: '  hello  ', timestamp: 1_700_000_000 },
			{
				role: 'assistant',
				content: 'fallback content',
				output: [
					{ type: 'message', content: [{ type: 'output_text', text: 'structured answer' }] }
				],
				model: 'model-id',
				modelName: 'Model Display',
				timestamp: 1_700_000_001,
				meta: { response_ended_at: 1_700_000_002 }
			},
			{ role: 'assistant', content: '   ' }
		]);

		expect(messages).toHaveLength(2);
		expect(messages[0]).toMatchObject({ index: 1, role: 'user', content: 'hello' });
		expect(messages[1]).toMatchObject({
			index: 2,
			role: 'assistant',
			content: 'structured answer',
			modelName: 'Model Display',
			timestamp: 1_700_000_002
		});
		expect(collectReportModelNames(messages)).toEqual(['Model Display']);
	});

	it('removes document delivery instructions without touching operational copy steps', () => {
		const markdown = [
			'# IT Support Ticket SOP – Full Standard',
			'(Ready to be saved as a .docx file – just copy the contents into Word or any DOCX-compatible editor and save.)',
			'Below is a ready-to-use SOP (Standard Operating Procedure) template for ticket handling.',
			'Copy the content into a word-processor (e.g., Microsoft Word, Google Docs, LibreOffice) and save it as .docx.',
			'',
			'## Procedure',
			'1. Copy the ticket ID into the incident log.',
			'2. Save the ticket as unresolved until the owner confirms closure.'
		].join('\n');

		expect(prepareDocumentMarkdown(markdown)).toBe(
			[
				'# IT Support Ticket SOP – Full Standard',
				'',
				'## Procedure',
				'1. Copy the ticket ID into the incident log.',
				'2. Save the ticket as unresolved until the owner confirms closure.'
			].join('\n')
		);
		expect(extractDocumentTitle(markdown)).toBe('IT Support Ticket SOP – Full Standard');
	});

	it('preserves delivery-like text inside fenced code blocks', () => {
		const markdown = [
			'# Export guide',
			'```text',
			'Copy the contents into Word and save as .docx.',
			'```'
		].join('\n');

		expect(prepareDocumentMarkdown(markdown)).toBe(markdown);
	});

	it('exports the latest usable assistant response as the document body', () => {
		const messages = selectDocumentMessages([
			{ role: 'user', content: 'Create an SOP.' },
			{ role: 'assistant', content: '# Draft SOP\n\nOld draft.' },
			{ role: 'user', content: 'Make it final.' },
			{ role: 'assistant', content: '# Final SOP\n\nApproved procedure.' },
			{ role: 'assistant', content: '   ' }
		]);

		expect(messages).toHaveLength(1);
		expect(messages[0]).toMatchObject({
			index: 1,
			role: 'assistant',
			content: '# Final SOP\n\nApproved procedure.'
		});
	});
});
