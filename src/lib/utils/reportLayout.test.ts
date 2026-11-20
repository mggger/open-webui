import { describe, expect, it } from 'vitest';
import { parseReportLayout, reportColumnWeights, resolveReportDesign } from './reportLayout';
describe('print layout contracts', () => {
	it('rejects arbitrary style payloads and invalid enum values', () => {
		expect(() =>
			parseReportLayout(
				JSON.stringify({
					title: 'Title',
					subtitle: '',
					sections: [
						{
							title: 'Section',
							purpose: '',
							presentation: 'html',
							paragraphStyle: 'block',
							startOnNewPage: false
						}
					]
				})
			)
		).toThrow();
	});
	it('allocates more width to narrative columns without starving labels', () => {
		const widths = reportColumnWeights([
			['Status', 'Details'],
			['Open', 'An extended explanation of the proposed architecture and its constraints']
		]);
		expect(widths.reduce((sum, value) => sum + value, 0)).toBeCloseTo(1);
		expect(widths[1]).toBeGreaterThan(widths[0]);
		expect(widths[0]).toBeGreaterThan(0.15);
	});
});

it('allows a full report without mandatory cover or contents', () => {
	const design = {
		documentType: 'report' as const,
		coverPage: false,
		contents: false,
		numberedSections: false,
		showSectionHeadings: true,
		density: 'comfortable' as const
	};
	const result = parseReportLayout(
		JSON.stringify({
			title: 'Assessment',
			subtitle: '',
			design,
			sections: [
				{
					title: 'Findings',
					purpose: 'Evidence',
					presentation: 'comparison',
					paragraphStyle: 'block',
					startOnNewPage: false
				}
			]
		})
	);
	expect(result.design).toEqual(design);
});
it('removes unnecessary packaging from a concise note', () => {
	const result = resolveReportDesign({
		title: 'Confirmation',
		subtitle: '',
		design: {
			documentType: 'note',
			coverPage: true,
			contents: true,
			numberedSections: true,
			showSectionHeadings: true,
			density: 'compact'
		},
		sections: [
			{
				title: 'Body',
				purpose: '',
				presentation: 'narrative',
				paragraphStyle: 'block',
				startOnNewPage: false
			}
		]
	});
	expect(result).toMatchObject({
		coverPage: false,
		contents: false,
		numberedSections: false,
		showSectionHeadings: false
	});
});
