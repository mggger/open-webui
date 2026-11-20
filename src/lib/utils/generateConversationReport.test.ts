import { beforeEach, describe, expect, it, vi } from 'vitest';
vi.mock('./reportDiagnostics', async (original) => ({
	...(await original<typeof import('./reportDiagnostics')>()),
	recordReportDiagnostic: vi.fn()
}));
vi.mock('$lib/apis/openai', () => ({ generateOpenAIChatCompletion: vi.fn() }));
vi.mock('./reportDiagrams', async (original) => ({
	...(await original<typeof import('./reportDiagrams')>()),
	renderReportDiagram: vi.fn()
}));
import { generateOpenAIChatCompletion } from '$lib/apis/openai';
import { generateConversationReport } from './generateConversationReport';
import { renderReportDiagram } from './reportDiagrams';
const complete = vi.mocked(generateOpenAIChatCompletion);
const documentResponse = (...bodies: string[]) =>
	response(JSON.stringify({ sections: bodies.map((body) => ({ body })) }));
const layout = {
	title: 'Report',
	subtitle: 'Scope and decisions',
	design: {
		documentType: 'report',
		coverPage: true,
		contents: false,
		numberedSections: true,
		showSectionHeadings: true,
		density: 'comfortable'
	},
	sections: [
		{
			title: 'Summary',
			purpose: 'Key findings',
			presentation: 'narrative',
			paragraphStyle: 'block',
			startOnNewPage: false
		},
		{
			title: 'Architecture',
			purpose: 'Explain flow',
			presentation: 'architecture',
			paragraphStyle: 'block',
			startOnNewPage: false
		}
	]
};
const response = (content: string) => ({
	choices: [{ finish_reason: 'stop', message: { content } }]
});
beforeEach(() => {
	vi.clearAllMocks();
	complete.mockReset();
	complete.mockImplementation(async (_token, body: any) => {
		const instruction = body.messages[0].content;
		if (instruction.includes('ART DIRECTOR:')) return response(JSON.stringify(layout));
		if (instruction.includes('EVIDENCE EDITOR:'))
			return response('Verified notes and source links.');
		return documentResponse('Evidence-based explanation.', 'A distinct technical explanation.');
	});
});
describe('conversation report generation', () => {
	it('plans and writes the complete document in two calls and shares results across formats', async () => {
		const messages = [
			{ role: 'user', content: 'Initial requirements' },
			{ role: 'assistant', model: 'internal', content: 'Findings' }
		];
		const stages: string[] = [];
		const first = await generateConversationReport(messages, 'Test', 'a', (p) =>
			stages.push(p.stage)
		);
		const second = await generateConversationReport(messages, 'Test', 'a');
		expect(first).toEqual(second);
		expect(complete).toHaveBeenCalledTimes(2);
		expect(first.markdown).toContain('## 02 Architecture');
		expect(first.layout?.design).toMatchObject({
			documentType: 'brief',
			coverPage: false,
			contents: false
		});
		expect(stages).toEqual(['organizing', 'designing', 'writing']);
		const body = complete.mock.calls[0][1] as any;
		expect(body.model).toBe('internal');
		expect(body.messages[1].content).toContain('Initial requirements');
		expect(body.messages[1].content).toContain('Findings');
	});
	it('does not cache truncation and permits retry', async () => {
		const messages = [{ role: 'assistant', model: 'internal', content: 'Retry scenario' }];
		complete.mockResolvedValueOnce({ choices: [{ finish_reason: 'length' }] });
		await expect(generateConversationReport(messages, 'Retry', 'b')).rejects.toThrow('truncated');
		await expect(generateConversationReport(messages, 'Retry', 'b')).resolves.toHaveProperty(
			'title',
			'Report'
		);
	});
	it('reads every excerpt without the former conversation-length cutoff', async () => {
		await generateConversationReport(
			[{ role: 'assistant', model: 'internal', content: 'START' + 'x'.repeat(245000) + 'END' }],
			'Long',
			'c'
		);
		const calls = complete.mock.calls.filter((call) =>
			(call[1] as any).messages[0].content.includes('EVIDENCE EDITOR:')
		);
		expect(calls).toHaveLength(13);
		expect((calls[0][1] as any).messages[1].content).toContain('START');
		expect((calls[12][1] as any).messages[1].content).toContain('END');
	});
	it('retries malformed layouts before writing', async () => {
		complete.mockResolvedValueOnce(response('{bad json'));
		const result = await generateConversationReport(
			[{ role: 'assistant', model: 'internal', content: 'Layout retry' }],
			'Layout',
			'd'
		);
		expect(result.layout?.sections).toHaveLength(2);
		expect(complete).toHaveBeenCalledTimes(3);
	});
	it('repairs failed diagrams and retains the rendered figure', async () => {
		const code = 'flowchart TB\nA[Client] --> B[Service]';
		complete
			.mockResolvedValueOnce(
				response(JSON.stringify({ ...layout, sections: [layout.sections[1]] }))
			)
			.mockResolvedValueOnce(documentResponse('```mermaid\ninvalid diagram\n```'))
			.mockResolvedValueOnce(response(code));
		vi.mocked(renderReportDiagram)
			.mockRejectedValueOnce(new Error('Syntax error'))
			.mockResolvedValueOnce({ dataUrl: 'data:image/png;base64,AA==', width: 400, height: 300 });
		const report = await generateConversationReport(
			[{ role: 'assistant', model: 'internal', content: 'Diagram data' }],
			'Diagram',
			'e'
		);
		expect(report.markdown).toContain(code);
		expect(report.markdown).not.toContain('invalid diagram');
		expect(report.diagrams?.[code].width).toBe(400);
	});
	it('designs a short note from the entire conversation without a long report pipeline', async () => {
		const note = {
			...layout,
			design: {
				documentType: 'note',
				coverPage: true,
				contents: true,
				numberedSections: true,
				showSectionHeadings: true,
				density: 'compact'
			},
			sections: [layout.sections[0]]
		};
		complete
			.mockResolvedValueOnce(response(JSON.stringify(note)))
			.mockResolvedValueOnce(documentResponse('The delivery date is Friday.'));
		const report = await generateConversationReport(
			[
				{ role: 'user', content: 'Earlier request: Monday delivery' },
				{ role: 'assistant', model: 'internal', content: 'Correction: delivery is Friday.' }
			],
			'Delivery',
			'short-note'
		);
		expect(complete).toHaveBeenCalledTimes(2);
		expect((complete.mock.calls[0][1] as any).messages[1].content).toContain('Earlier request');
		expect((complete.mock.calls[0][1] as any).messages[1].content).toContain('Correction');
		expect(report.layout?.design).toMatchObject({
			documentType: 'note',
			coverPage: false,
			contents: false,
			numberedSections: false,
			showSectionHeadings: false
		});
		expect(report.markdown).toContain('The delivery date is Friday.');
	});
});

describe('report failure diagnostics', () => {
	it('preserves the failing stage, HTTP status and request identifier', async () => {
		complete.mockRejectedValueOnce(
			Object.assign(new Error('Upstream unavailable'), { status: 503, requestId: 'upstream-123' })
		);
		await expect(
			generateConversationReport(
				[{ role: 'assistant', model: 'internal', content: 'diagnostic failure' }],
				'Failure',
				'diagnostic'
			)
		).rejects.toMatchObject({
			diagnostic: {
				stage: 'designing',
				status: 503,
				requestId: 'upstream-123',
				message: 'Upstream unavailable'
			}
		});
	});
	it('reports the invalid layout field after bounded repair attempts', async () => {
		complete.mockResolvedValue(
			response(
				JSON.stringify({
					...layout,
					design: { ...layout.design, documentType: 'note|brief|report' }
				})
			)
		);
		await expect(
			generateConversationReport(
				[{ role: 'assistant', model: 'internal', content: 'invalid layout diagnostic' }],
				'Failure',
				'invalid-layout'
			)
		).rejects.toMatchObject({
			diagnostic: {
				stage: 'designing',
				code: 'INVALID_LAYOUT',
				message: expect.stringContaining('design.documentType')
			}
		});
		expect(complete).toHaveBeenCalledTimes(3);
		expect((complete.mock.calls[1][1] as any).messages[1].content).toContain('design.documentType');
	});
});

describe('diagram alternatives', () => {
	it.each([
		'flowchart TB\n"Submit Ticket" --> "Support Group"',
		'flowchart TB\nA["Route to Support Group"]flowchart TB D["Resolve"]'
	])('finishes a client-ready report when repairs still fail: %s', async (broken) => {
		const fallback =
			'1. Submit the ticket.\n2. Route it to the support group.\n3. Resolve the request.';
		complete.mockImplementation(async (_token, body: any) => {
			const instruction = body.messages[0].content;
			if (instruction.includes('ART DIRECTOR:'))
				return response(JSON.stringify({ ...layout, sections: [layout.sections[1]] }));
			if (instruction.includes('Recompose the complete section')) return response(fallback);
			if (instruction.includes('DIAGRAM EDITOR:')) return response(broken);
			return documentResponse('*Support process*\n\n```mermaid\n' + broken + '\n```');
		});
		vi.mocked(renderReportDiagram).mockRejectedValue(new Error('Parse error'));
		const report = await generateConversationReport(
			[{ role: 'assistant', model: 'internal', content: broken }],
			'Process',
			broken
		);
		expect(report.markdown).toContain(fallback);
		expect(report.markdown).not.toContain('mermaid');
		expect(report.markdown).not.toContain('Parse error');
		expect(report.markdown).not.toContain('*Support process*');
		expect(renderReportDiagram).toHaveBeenCalledTimes(3);
		expect(report.diagrams).toEqual({});
	});
});

describe('whole-document repair', () => {
	it('reviews repeated full SOPs once instead of rendering their duplicate figures', async () => {
		const code = 'flowchart TB\nA["Submit"] --> B["Resolve"]';
		const diagram = '```mermaid\n' + code + '\n```';
		complete
			.mockResolvedValueOnce(response(JSON.stringify(layout)))
			.mockResolvedValueOnce(documentResponse('### Summary\n\n' + diagram, diagram))
			.mockResolvedValueOnce(documentResponse('Submitters provide issue details.', diagram));
		vi.mocked(renderReportDiagram).mockResolvedValue({
			dataUrl: 'data:image/png;base64,AA==',
			width: 400,
			height: 300
		});
		const report = await generateConversationReport(
			[{ role: 'assistant', model: 'internal', content: 'Ticket process regression' }],
			'Regression',
			'whole-review'
		);
		expect(complete).toHaveBeenCalledTimes(3);
		expect(renderReportDiagram).toHaveBeenCalledTimes(1);
		expect(report.markdown.split('```mermaid')).toHaveLength(2);
		const repair = JSON.parse((complete.mock.calls[2][1] as any).messages[1].content);
		expect(repair.issues.join(' ')).toContain('Repeated diagram');
		expect(repair.previousDraft).toContain('### Summary');
		expect(complete.mock.calls[0][2]).toBe('/api/v1/reports');
	});
	it('does not export a document that repeatedly ignores quality repairs', async () => {
		complete.mockResolvedValueOnce(response(JSON.stringify(layout)));
		complete.mockImplementation(async () =>
			documentResponse('## Summary\n\nEntire SOP again', '## Architecture\n\nEntire SOP again')
		);
		await expect(
			generateConversationReport(
				[{ role: 'assistant', model: 'internal', content: 'Invalid full documents' }],
				'Bad',
				'quality-gate'
			)
		).rejects.toMatchObject({ diagnostic: { code: 'DOCUMENT_QUALITY' } });
		expect(complete).toHaveBeenCalledTimes(4);
	});
});
