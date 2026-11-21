import { marked } from 'marked';
import type { ReportLayout } from './reportLayout';
import { resolveReportDesign } from './reportLayout';

export type ReportBudget = { targetWords: number; maxDiagrams: number; diagramSections: number[] };

/** A CJK character is a unit; other languages use words. Never budget by tokens or chat turns. */
export const reportWordCount = (text: string): number =>
	(
		text.match(
			/[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}\p{Script=Hangul}]|[\p{L}\p{N}]+(?:['’-][\p{L}\p{N}]+)*/gu
		) ?? []
	).length;

export const constrainReportPlan = (layout: ReportLayout): ReportBudget => {
	const type = resolveReportDesign(layout).documentType;
	// An editorial estimate, never an export limit or a proxy for source complexity.
	const targetWords =
		layout.budget?.targetWords ?? (type === 'note' ? 250 : type === 'brief' ? 900 : 1800);
	const figureLimit = type === 'report' ? 3 : 1;
	const eligible = layout.sections.flatMap((section, index) =>
		['process', 'architecture'].includes(section.presentation) ? [index] : []
	);
	const maxDiagrams = Math.min(
		layout.budget?.maxDiagrams ?? eligible.length,
		figureLimit,
		eligible.length
	);
	const diagramSections = eligible.slice(0, maxDiagrams);
	if (type !== 'report') {
		layout.design = {
			...resolveReportDesign(layout),
			documentType: type === 'note' ? 'note' : 'brief',
			coverPage: false,
			contents: false,
			density: 'compact'
		};
		layout.sections = layout.sections.map((section) => ({ ...section, startOnNewPage: false }));
	}
	layout.budget = { targetWords, maxDiagrams };
	return { targetWords, maxDiagrams, diagramSections };
};

export const parseReportBodies = (text: string, layout: ReportLayout): string[] => {
	let result: any;
	try {
		result = JSON.parse(text.trim().replace(/^```(?:json)?\s*\n([\s\S]*?)\n```$/i, '$1'));
	} catch {
		throw new Error(
			'Return a JSON object with a sections array of {body: string}; no surrounding document or commentary.'
		);
	}
	if (!Array.isArray(result?.sections) || result.sections.length !== layout.sections.length)
		throw new Error(`Return exactly ${layout.sections.length} section bodies in outline order.`);
	return result.sections.map((section: any, index: number) => {
		if (typeof section?.body !== 'string' || !section.body.trim())
			throw new Error(`Section ${index + 1} must have a nonempty body.`);
		return section.body.trim();
	});
};

const normalize = (text: string) => text.replace(/\s+/g, ' ').trim().toLowerCase();
const headingKey = (text: string) =>
	normalize(text.replace(/^[\d.、\s]+/, '').replace(/[*_`]/g, ''));
const shingles = (text: string) => {
	const words = normalize(text).match(/[\p{L}\p{N}]+/gu) ?? [];
	return new Set(words.slice(0, -3).map((_, i) => words.slice(i, i + 4).join(' ')));
};

/** Detect problems across the entire document before any figures are rendered. Never cut facts to fit. */
export const reportQualityIssues = (
	bodies: string[],
	layout: ReportLayout,
	budget: ReportBudget
): string[] => {
	const issues = new Set<string>();
	const titles = new Set(layout.sections.map((section) => headingKey(section.title)));
	const paragraphs: { text: string; section: number; shingles: Set<string> }[] = [];
	const seenFigures = new Set<string>();
	let figureCount = 0;
	for (const [index, body] of bodies.entries()) {
		marked.walkTokens(marked.lexer(body), (block) => {
			if (block.type === 'heading' && (block.depth <= 2 || titles.has(headingKey(block.text))))
				issues.add(
					`Section ${index + 1} repeats a document/outline heading (${block.text}); write only its assigned body, not a complete document.`
				);
			if (!['paragraph', 'table'].includes(block.type) || reportWordCount(block.raw) < 18) return;
			const text = normalize(block.raw);
			const grams = shingles(text);
			for (const previous of paragraphs) {
				const overlap = [...grams].filter((gram) => previous.shingles.has(gram)).length;
				const similarity = overlap / Math.max(1, grams.size + previous.shingles.size - overlap);
				if (text === previous.text || (grams.size >= 16 && similarity >= 0.8)) {
					issues.add(
						`Sections ${previous.section + 1} and ${index + 1} repeat substantial content. Keep each fact in one appropriate location and use a short reference elsewhere.`
					);
					break;
				}
			}
			paragraphs.push({ text, section: index, shingles: grams });
		});
		// Count every occurrence, not only unique sources (render-cache reuse is not deduplication).
		marked.walkTokens(marked.lexer(body), (block) => {
			if (block.type !== 'code' || block.lang?.trim().toLowerCase() !== 'mermaid') return;
			figureCount++;
			if (!budget.diagramSections.includes(index))
				issues.add(
					`Section ${index + 1} is not assigned a diagram. Keep figures in their planned section only.`
				);
			const signature = normalize(block.text);
			// Label-set matches are a review signal, not permission to silently delete relationships.
			const labels = [...block.text.matchAll(/"([^"\n]+)"/g)]
				.map((match) => normalize(match[1]))
				.sort();
			const labelKey = labels.length >= 4 ? JSON.stringify(labels) : signature;
			if (seenFigures.has(signature) || seenFigures.has(labelKey))
				issues.add(
					'Repeated diagram: retain one canonical figure for each subject and reference it elsewhere; preserve distinct relationships.'
				);
			seenFigures.add(signature);
			seenFigures.add(labelKey);
		});
	}
	if (figureCount > budget.maxDiagrams)
		issues.add(
			`Use at most ${budget.maxDiagrams} diagrams across the entire document, not per section (found ${figureCount}).`
		);
	return [...issues];
};
