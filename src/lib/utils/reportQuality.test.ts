import { describe, expect, it } from 'vitest';
import {
	constrainReportPlan,
	parseReportBodies,
	reportQualityIssues,
	reportWordCount
} from './reportQuality';
import type { ReportLayout } from './reportLayout';
const outline = (): ReportLayout => ({
	title: 'Ticket SOP',
	subtitle: '',
	design: {
		documentType: 'report',
		coverPage: true,
		contents: true,
		numberedSections: true,
		showSectionHeadings: true,
		density: 'comfortable'
	},
	sections: ['Purpose', 'Procedure', 'Revision History'].map((title) => ({
		title,
		purpose: 'Distinct content',
		presentation: 'process',
		paragraphStyle: 'block',
		startOnNewPage: true
	}))
});
const graph = 'flowchart TB\nA["Submit"] --> B["Triage"]\nB --> C["Resolve"]\nC --> D["Close"]';
const fence = (source: string) => '```mermaid\n' + source + '\n```';
describe('document-wide editorial controls', () => {
	it('counts CJK characters for repetition checks', () => {
		expect(reportWordCount('这是简短说明')).toBe(6);
	});
	it('removes packaging and chapter breaks from a routine SOP and assigns one diagram owner', () => {
		const layout = outline();
		layout.design!.documentType = 'brief';
		const budget = constrainReportPlan(layout);
		expect(budget).toEqual({ targetWords: 900, maxDiagrams: 1, diagramSections: [0] });
		expect(layout.design).toMatchObject({
			documentType: 'brief',
			coverPage: false,
			contents: false
		});
		expect(layout.sections.every((section) => !section.startOnNewPage)).toBe(true);
	});
	it('preserves a detailed outline regardless of its length estimate', () => {
		const layout = outline();
		layout.sections = Array.from({ length: 12 }, (_, i) => ({
			...layout.sections[0],
			title: 'Topic ' + i
		}));
		layout.budget = { targetWords: 350, maxDiagrams: 0 };
		expect(() => constrainReportPlan(layout)).not.toThrow();
		expect(layout.design!.documentType).toBe('report');
		expect(layout.sections).toHaveLength(12);
	});
	it('detects the three repeated workflows even when node IDs change', () => {
		const renamed = graph
			.replace(/\bA\b/g, 'W')
			.replace(/\bB\b/g, 'X')
			.replace(/\bC\b/g, 'Y')
			.replace(/\bD\b/g, 'Z');
		const issues = reportQualityIssues([fence(graph), fence(renamed), fence(graph)], outline(), {
			targetWords: 1000,
			maxDiagrams: 1,
			diagramSections: [1]
		});
		expect(issues.join(' ')).toContain('Repeated diagram');
		expect(issues.join(' ')).toContain('found 3');
	});
	it('detects complete documents pasted into individual sections and repeated prose', () => {
		const text =
			'All tickets must record the submitter and the issue description with relevant supporting evidence before the service desk assigns the request to the appropriate resolver group.';
		const issues = reportQualityIssues(
			['### Purpose\n\n' + text, text, '## Procedure\n\nSteps'],
			outline(),
			{ targetWords: 1000, maxDiagrams: 0, diagramSections: [] }
		);
		expect(issues.some((issue) => issue.includes('outline heading'))).toBe(true);
		expect(issues.some((issue) => issue.includes('repeat substantial content'))).toBe(true);
	});
	it('counts repeated diagrams without rejecting content above its length estimate', () => {
		const issues = reportQualityIssues(
			[fence(graph) + '\n\n' + fence(graph), 'word '.repeat(100), 'Records'],
			outline(),
			{ targetWords: 80, maxDiagrams: 1, diagramSections: [0] }
		);
		expect(issues.some((issue) => issue.includes('maximum is'))).toBe(false);
		expect(issues.some((issue) => issue.includes('found 2'))).toBe(true);
	});
	it('preserves distinct conditional instructions and allows a concise complete document', () => {
		const bodies = [
			'This procedure covers ticket handling.',
			'Escalate P1 tickets within 15 minutes.\n\nEscalate P2 tickets within 30 minutes.',
			'Version 1.0: initial issue.'
		];
		expect(
			reportQualityIssues(bodies, outline(), {
				targetWords: 300,
				maxDiagrams: 0,
				diagramSections: []
			})
		).toEqual([]);
		expect(
			parseReportBodies(JSON.stringify({ sections: bodies.map((body) => ({ body })) }), outline())
		).toEqual(bodies);
	});
});

it('accepts non-repetitive content far beyond an 800-word estimate', () => {
	const body = Array.from({ length: 1600 }, (_, i) => 'requirement' + i).join(' ');
	expect(
		reportQualityIssues([body], outline(), {
			targetWords: 800,
			maxDiagrams: 0,
			diagramSections: []
		})
	).toEqual([]);
});
