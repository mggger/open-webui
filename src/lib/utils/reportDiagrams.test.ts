import { describe, expect, it } from 'vitest';
import { replaceReportDiagram, reportDiagramSources } from './reportDiagrams';

describe('report diagram replacement', () => {
	it('replaces repeated fenced diagrams without changing identical prose', () => {
		const original = 'flowchart TB\n"Submit Ticket" --> "Support Group"';
		const fixed = 'flowchart TB\nA["Submit Ticket"] --> B["Support Group"]';
		const markdown =
			original + '\n\n```mermaid\n' + original + '\n```\n\n~~~mermaid\n' + original + '\n~~~\n';
		const result = replaceReportDiagram(markdown, original, fixed);
		expect(result.startsWith(original)).toBe(true);
		expect(reportDiagramSources(result)).toEqual([fixed]);
		expect(result.split(fixed)).toHaveLength(3);
	});
});
