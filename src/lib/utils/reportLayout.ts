/** A small, validated print vocabulary: the model chooses structure, never arbitrary CSS. */
export type ReportSectionLayout = {
	title: string;
	purpose: string;
	presentation: 'narrative' | 'comparison' | 'process' | 'architecture' | 'actions';
	paragraphStyle: 'block' | 'indented';
	startOnNewPage: boolean;
};
export type ReportDesign = {
	documentType: 'note' | 'brief' | 'report';
	coverPage: boolean;
	contents: boolean;
	numberedSections: boolean;
	showSectionHeadings: boolean;
	density: 'compact' | 'comfortable';
};
export type ReportLayout = {
	design?: ReportDesign;
	budget?: { maxWords: number; maxDiagrams: number };
	title: string;
	subtitle: string;
	sections: ReportSectionLayout[];
};

const plainHeading = (text: string, length: number) =>
	text
		.replace(/[\r\n\t]+/g, ' ')
		.replace(/[#*`<>]/g, '')
		.replace(/\s+/g, ' ')
		.trim()
		.slice(0, length);

export const parseReportLayout = (text: string): ReportLayout => {
	let value: any;
	try {
		value = JSON.parse(text.trim().replace(/^```(?:json)?\s*\n([\s\S]*?)\n```$/i, '$1'));
	} catch {
		throw new Error('layout: expected valid JSON without commentary or a surrounding document');
	}
	const requireField = (valid: boolean, path: string, expected: string, actual: unknown) => {
		if (!valid)
			throw new Error(
				`${path}: expected ${expected}; received ${actual === null ? 'null' : Array.isArray(actual) ? 'array' : typeof actual}`
			);
	};
	requireField(
		value && typeof value === 'object' && !Array.isArray(value),
		'layout',
		'an object',
		value
	);
	requireField(
		typeof value.title === 'string' && !!plainHeading(value.title, 160),
		'layout.title',
		'a non-empty string',
		value.title
	);
	requireField(
		typeof value.subtitle === 'string',
		'layout.subtitle',
		'a string (empty allowed)',
		value.subtitle
	);
	requireField(
		Array.isArray(value.sections) && value.sections.length >= 1 && value.sections.length <= 16,
		'layout.sections',
		'an array of 1–16 sections',
		value.sections
	);
	const design = value.design;
	if (value.budget !== undefined) {
		requireField(
			Number.isInteger(value.budget?.maxWords) &&
				value.budget.maxWords >= 80 &&
				value.budget.maxWords <= 12000,
			'layout.budget.maxWords',
			'an integer from 80 to 12000',
			value.budget?.maxWords
		);
		requireField(
			Number.isInteger(value.budget?.maxDiagrams) &&
				value.budget.maxDiagrams >= 0 &&
				value.budget.maxDiagrams <= 3,
			'layout.budget.maxDiagrams',
			'an integer from 0 to 3',
			value.budget?.maxDiagrams
		);
	}
	requireField(design && typeof design === 'object', 'layout.design', 'an object', design);
	requireField(
		['note', 'brief', 'report'].includes(design.documentType),
		'layout.design.documentType',
		'one of note, brief, report',
		design.documentType
	);
	requireField(
		['compact', 'comfortable'].includes(design.density),
		'layout.design.density',
		'compact or comfortable',
		design.density
	);
	for (const key of ['coverPage', 'contents', 'numberedSections', 'showSectionHeadings'])
		requireField(
			typeof design[key] === 'boolean',
			`layout.design.${key}`,
			'a boolean',
			design[key]
		);
	for (const [index, section] of value.sections.entries()) {
		const path = `layout.sections[${index}]`;
		requireField(section && typeof section === 'object', path, 'an object', section);
		requireField(
			typeof section.title === 'string' && !!plainHeading(section.title, 160),
			`${path}.title`,
			'a non-empty string',
			section.title
		);
		requireField(
			typeof section.purpose === 'string',
			`${path}.purpose`,
			'a string',
			section.purpose
		);
		requireField(
			['narrative', 'comparison', 'process', 'architecture', 'actions'].includes(
				section.presentation
			),
			`${path}.presentation`,
			'narrative, comparison, process, architecture or actions',
			section.presentation
		);
		requireField(
			['block', 'indented'].includes(section.paragraphStyle),
			`${path}.paragraphStyle`,
			'block or indented',
			section.paragraphStyle
		);
		requireField(
			typeof section.startOnNewPage === 'boolean',
			`${path}.startOnNewPage`,
			'a boolean',
			section.startOnNewPage
		);
	}
	return {
		...(value.budget
			? { budget: { maxWords: value.budget.maxWords, maxDiagrams: value.budget.maxDiagrams } }
			: {}),
		design: resolveReportDesign(value),
		title: plainHeading(value.title, 160),
		subtitle: plainHeading(value.subtitle, 300),
		sections: value.sections.map((section: ReportSectionLayout) => ({
			title: plainHeading(section.title, 160),
			purpose: section.purpose,
			presentation: section.presentation,
			paragraphStyle: section.paragraphStyle,
			startOnNewPage: section.startOnNewPage
		}))
	};
};

/** Keep short deliverables proportional even if the planner requests unnecessary packaging. */
export const resolveReportDesign = (layout?: ReportLayout): ReportDesign => {
	const design: ReportDesign = layout?.design ?? {
		documentType: 'report',
		coverPage: true,
		contents: true,
		numberedSections: true,
		showSectionHeadings: true,
		density: 'comfortable'
	};
	if (design.documentType === 'note')
		return {
			...design,
			coverPage: false,
			contents: false,
			numberedSections: false,
			showSectionHeadings: (layout?.sections.length ?? 1) > 1 && design.showSectionHeadings
		};
	return {
		...design,
		coverPage: design.documentType === 'report' && design.coverPage,
		contents: design.contents && (layout?.sections.length ?? 0) >= 3,
		showSectionHeadings: (layout?.sections.length ?? 0) > 1 || design.showSectionHeadings
	};
};

/** Balance text-heavy columns while keeping narrow labels and numeric columns readable. */
export const reportColumnWeights = (rows: string[][]): number[] => {
	const columns = rows[0]?.length ?? 0;
	if (!columns) return [];
	const weights = Array.from({ length: columns }, (_, index) => {
		const lengths = rows.map((row) =>
			Array.from(row[index] ?? '').reduce(
				(sum, char) => sum + (/[^\x00-\x7F]/.test(char) ? 2 : 1),
				0
			)
		);
		const average = lengths.reduce((sum, length) => sum + length, 0) / lengths.length;
		return Math.max(8, Math.min(40, Math.sqrt(average + Math.max(...lengths)) * 3));
	});
	const total = weights.reduce((sum, weight) => sum + weight, 0);
	return weights.map((weight) => weight / total);
};
