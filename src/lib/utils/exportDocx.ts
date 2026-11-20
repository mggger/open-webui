import fileSaver from 'file-saver';
import { extractDocumentTitle, prepareDocumentMarkdown } from './exportReportPdfModel';
import { reportColumnWeights, resolveReportDesign, type ReportLayout } from './reportLayout';
import { renderReportDiagrams, type ReportDiagrams } from './reportDiagrams';
const { saveAs } = fileSaver;

// Built by scripts/build-docx-template.mjs: the Archer & Round letterhead (header,
// footer, logo copied from docs/archer_template.docx) and the paragraph styles
// (Normal, Heading 1-6, Archer Quote, Archer Code) matching the PDF export. Exports
// patch the response into its {{CONTENT}} placeholder.
const RESPONSE_TEMPLATE_URL = '/assets/docx/archer-response-template.docx';

// Shared enterprise palette for PDF and editable Word output.
const TABLE_RULE = 'E0E0E0';
const TABLE_HEADER_FILL = 'E8F0FF';
const INLINE_CODE_FILL = 'F4F4F4';
const HR_RULE = 'E0E0E0';

// Formatting is applied directly to every paragraph and run (not only through the
// template's styles) because some viewers, e.g. macOS Quick Look, ignore styles.xml.
const FONT = 'IBM Plex Sans';
const BODY_SIZE = 21; // half-points (10.5pt, ~14px like the PDF)
const BODY_COLOR = '161616';
const CODE_FONT = 'Courier New';
const CODE_FILL = 'F4F4F4';
const QUOTE_RULE = '0F62FE';
// A4 width minus the template's 1224-twip side margins.
const CONTENT_WIDTH = 11906 - 2 * 1224;
const BODY_SPACING = { before: 120, after: 120, line: 360 };
const HEADING_SIZE: Record<number, number> = { 1: 40, 2: 32, 3: 28, 4: 24, 5: 22, 6: 22 };

const BULLET_LIST = 'archer-bullet';
const NUMBERED_LIST = 'archer-number';

type RunSpec = {
	text: string;
	bold?: boolean;
	italic?: boolean;
	code?: boolean;
	strike?: boolean;
	href?: string;
	color?: string;
};

type ConvertContext = {
	docxLib: any;
	nextListInstance: number;
	layout?: ReportLayout;
	diagrams: ReportDiagrams;
	sectionIndex: number;
};

export type MessageDocxMeta = {
	// Stored as the document's title property.
	title?: string;
	reportDate?: string;
	layout?: ReportLayout;
	diagrams?: ReportDiagrams;
};

// marked's lexer HTML-escapes text/codespan token content (e.g. "&" -> "&amp;")
// since it assumes the caller renders to HTML. Decode it back before putting it
// into a docx TextRun, which expects literal text.
const decodeHtmlEntities = (text: string): string =>
	text
		.replace(/&lt;/g, '<')
		.replace(/&gt;/g, '>')
		.replace(/&quot;/g, '"')
		.replace(/&#0*39;|&#x0*27;/gi, "'")
		.replace(/&nbsp;/g, ' ')
		.replace(/&amp;/g, '&');

// Flattens marked's nested inline tokens (strong/em/del/link/codespan/...) into a
// flat list of styled text runs, threading formatting down through nesting.
const flattenInline = (tokens: any[] = [], fmt: Partial<RunSpec> = {}): RunSpec[] => {
	const runs: RunSpec[] = [];

	tokens.forEach((token) => {
		switch (token.type) {
			case 'strong':
				runs.push(...flattenInline(token.tokens, { ...fmt, bold: true }));
				break;
			case 'em':
				runs.push(...flattenInline(token.tokens, { ...fmt, italic: true }));
				break;
			case 'del':
				runs.push(...flattenInline(token.tokens, { ...fmt, strike: true }));
				break;
			case 'codespan':
				runs.push({ ...fmt, code: true, text: decodeHtmlEntities(token.text ?? '') });
				break;
			case 'link':
				runs.push(...flattenInline(token.tokens, { ...fmt, href: token.href }));
				break;
			case 'image':
				runs.push({
					...fmt,
					text: token.text ? `[${decodeHtmlEntities(token.text)}]` : (token.href ?? '')
				});
				break;
			case 'br':
				runs.push({ ...fmt, text: '\n' });
				break;
			case 'escape':
			case 'text':
				if (token.tokens) {
					runs.push(...flattenInline(token.tokens, fmt));
				} else {
					runs.push({ ...fmt, text: decodeHtmlEntities(token.text ?? token.raw ?? '') });
				}
				break;
			default:
				if (token.tokens) {
					runs.push(...flattenInline(token.tokens, fmt));
				} else if (token.text) {
					runs.push({ ...fmt, text: decodeHtmlEntities(token.text) });
				}
		}
	});

	return runs;
};

// Converts flattened runs into docx TextRun/ExternalHyperlink paragraph children.
const runsToChildren = (runs: RunSpec[], docxLib: any, base: Record<string, any> = {}) => {
	const { TextRun, ExternalHyperlink, ShadingType } = docxLib;
	const children: any[] = [];

	runs.forEach((run) => {
		(run.text ?? '').split('\n').forEach((line, idx) => {
			if (idx > 0) children.push(new TextRun({ text: '', break: 1 }));
			if (!line) return;

			const runOptions: any = {
				font: FONT,
				size: BODY_SIZE,
				color: BODY_COLOR,
				...base,
				text: line,
				bold: run.bold || base.bold,
				italics: run.italic || base.italics,
				strike: run.strike
			};
			if (run.color) runOptions.color = run.color;
			if (run.href) {
				runOptions.color = '0F62FE';
				runOptions.underline = {};
			}
			if (run.code) {
				runOptions.font = CODE_FONT;
				runOptions.size = 20;
				runOptions.shading = { type: ShadingType.CLEAR, fill: INLINE_CODE_FILL, color: 'auto' };
			}

			if (run.href) {
				children.push(
					new ExternalHyperlink({
						link: run.href,
						children: [new TextRun({ ...runOptions, style: 'Hyperlink' })]
					})
				);
			} else {
				children.push(new TextRun(runOptions));
			}
		});
	});

	return children.length ? children : [new TextRun({ text: '', font: FONT, size: BODY_SIZE })];
};

const listItemRuns = (item: any): { runs: RunSpec[]; nested: any[]; blocks: any[] } => {
	const runs: RunSpec[] = [];
	const nested: any[] = [];
	const blocks: any[] = [];

	(item.tokens ?? []).forEach((sub: any) => {
		if (sub.type === 'list') {
			nested.push(sub);
		} else if (sub.type === 'text' || sub.type === 'paragraph') {
			if (runs.length) runs.push({ text: '\n' });
			runs.push(...flattenInline(sub.tokens ?? [{ type: 'text', text: sub.text }]));
		} else if (sub.type === 'space') {
			return;
		} else {
			blocks.push(sub);
		}
	});

	return { runs, nested, blocks };
};

const tableBlock = (token: any, ctx: ConvertContext) => {
	const {
		Paragraph,
		Table,
		TableRow,
		TableCell,
		WidthType,
		BorderStyle,
		ShadingType,
		AlignmentType
	} = ctx.docxLib;

	const rule = { style: BorderStyle.SINGLE, size: 4, color: TABLE_RULE };
	const alignOf = (idx: number) =>
		token.align?.[idx] === 'center'
			? AlignmentType.CENTER
			: token.align?.[idx] === 'right'
				? AlignmentType.RIGHT
				: AlignmentType.LEFT;

	// Explicit twip widths as well as the percentage, for viewers that ignore percentages.
	const weights = reportColumnWeights(
		[token.header, ...(token.rows ?? [])].map((row: any[]) => row.map((c: any) => c.text ?? ''))
	);
	const columnWidths = weights.map((weight) => Math.floor(CONTENT_WIDTH * weight));

	const cell = (tokens: any[], idx: number, header: boolean, rowIndex = 0) =>
		new TableCell({
			width: { size: columnWidths[idx], type: WidthType.DXA },
			shading: header
				? { type: ShadingType.CLEAR, fill: TABLE_HEADER_FILL, color: 'auto' }
				: { type: ShadingType.CLEAR, fill: rowIndex % 2 ? 'F4F4F4' : 'FFFFFF', color: 'auto' },
			margins: { top: 120, bottom: 120, left: 140, right: 140 },
			children: [
				new Paragraph({
					alignment: alignOf(idx),
					spacing: { before: 0, after: 0, line: 260 },
					children: runsToChildren(flattenInline(tokens, header ? { bold: true } : {}), ctx.docxLib)
				})
			]
		});

	return [
		new Table({
			width: { size: 100, type: WidthType.PERCENTAGE },
			columnWidths,
			borders: {
				top: rule,
				bottom: rule,
				left: rule,
				right: rule,
				insideHorizontal: rule,
				insideVertical: rule
			},
			rows: [
				new TableRow({
					tableHeader: true,
					children: (token.header ?? []).map((c: any, idx: number) => cell(c.tokens, idx, true))
				}),
				...(token.rows ?? []).map(
					(row: any[], rowIndex: number) =>
						new TableRow({
							cantSplit: true,
							children: row.map((c, idx) => cell(c.tokens, idx, false, rowIndex))
						})
				)
			]
		}),
		new Paragraph({ spacing: { before: 0, after: 120 }, children: [] })
	];
};

// Walks marked's block-level tokens and builds the matching docx Paragraph/Table nodes.
// Direct formatting keeps the typography consistent in viewers that ignore template styles.
const blocksToDocx = (tokens: any[], ctx: ConvertContext, listLevel = 0): any[] => {
	const { Paragraph, TextRun, HeadingLevel, BorderStyle, ShadingType } = ctx.docxLib;

	const headingLevel: Record<number, string> = {
		1: HeadingLevel.HEADING_1,
		2: HeadingLevel.HEADING_2,
		3: HeadingLevel.HEADING_3,
		4: HeadingLevel.HEADING_4,
		5: HeadingLevel.HEADING_5,
		6: HeadingLevel.HEADING_6
	};

	const out: any[] = [];
	const design = resolveReportDesign(ctx.layout);
	const paragraphSpacing =
		design.density === 'compact' ? { before: 60, after: 120, line: 300 } : BODY_SPACING;

	const pushList = (token: any, level: number) => {
		const ordered = Boolean(token.ordered);
		const instance = ordered ? ctx.nextListInstance++ : 0;

		(token.items ?? []).forEach((item: any) => {
			const { runs, nested, blocks } = listItemRuns(item);
			out.push(
				new Paragraph({
					numbering: {
						reference: ordered ? NUMBERED_LIST : BULLET_LIST,
						level: Math.min(level, 2),
						...(ordered ? { instance } : {})
					},
					spacing: { before: 0, after: 80, line: BODY_SPACING.line },
					children: runsToChildren(runs, ctx.docxLib)
				})
			);
			out.push(...blocksToDocx(blocks, ctx, level));
			nested.forEach((sub) => pushList(sub, level + 1));
		});
	};

	tokens.forEach((token, tokenIndex) => {
		const nextToken = tokens.slice(tokenIndex + 1).find((next) => next.type !== 'space');
		const caption =
			token.type === 'paragraph' &&
			token.tokens?.length === 1 &&
			token.tokens[0].type === 'em' &&
			(nextToken?.type === 'table' || (nextToken?.type === 'code' && nextToken.lang === 'mermaid'));
		switch (token.type) {
			case 'heading':
				if (token.depth === 2) {
					ctx.sectionIndex++;
					if (!design.showSectionHeadings) break;
				}
				if (
					token.depth === 2 &&
					ctx.sectionIndex > 0 &&
					design.documentType !== 'note' &&
					ctx.layout?.sections[ctx.sectionIndex]?.startOnNewPage
				)
					out.push(new Paragraph({ children: [new ctx.docxLib.PageBreak()] }));
				out.push(
					new Paragraph({
						heading: headingLevel[token.depth] ?? HeadingLevel.HEADING_6,
						keepNext: true,
						spacing: { before: 240, after: 120, line: 300 },
						children: runsToChildren(flattenInline(token.tokens), ctx.docxLib, {
							bold: true,
							color: BODY_COLOR,
							size: HEADING_SIZE[token.depth] ?? 22
						})
					})
				);
				break;

			case 'paragraph':
				out.push(
					new Paragraph({
						spacing: caption ? { before: 180, after: 100, line: 260 } : paragraphSpacing,
						widowControl: true,
						keepNext: caption,
						indent:
							!caption && ctx.layout?.sections[ctx.sectionIndex]?.paragraphStyle === 'indented'
								? { firstLine: 420 }
								: undefined,
						children: runsToChildren(
							flattenInline(token.tokens),
							ctx.docxLib,
							caption ? { size: 18, color: '525252' } : {}
						)
					})
				);
				break;

			case 'blockquote':
				(token.tokens ?? []).forEach((inner: any) => {
					if (inner.type === 'paragraph' || inner.type === 'text') {
						out.push(
							new Paragraph({
								style: 'ArcherQuote',
								spacing: BODY_SPACING,
								indent: { left: 200 },
								border: {
									left: { style: BorderStyle.SINGLE, size: 18, color: QUOTE_RULE, space: 8 }
								},
								children: runsToChildren(flattenInline(inner.tokens ?? []), ctx.docxLib, {
									color: '525252'
								})
							})
						);
					} else {
						out.push(...blocksToDocx([inner], ctx, listLevel));
					}
				});
				break;

			case 'list':
				pushList(token, listLevel);
				break;

			case 'code': {
				if (token.lang?.trim().toLowerCase() === 'mermaid') {
					const figure = ctx.diagrams[token.text];
					if (!figure) throw new Error('Missing rendered report diagram');
					const scale = Math.min(CONTENT_WIDTH / 15 / figure.width, 580 / figure.height, 1);
					out.push(
						new Paragraph({
							alignment: ctx.docxLib.AlignmentType.CENTER,
							keepLines: true,
							spacing: { before: 120, after: 240 },
							children: [
								new ctx.docxLib.ImageRun({
									type: 'png',
									data: figure.dataUrl,
									transformation: {
										width: Math.round(figure.width * scale),
										height: Math.round(figure.height * scale)
									},
									altText: {
										title: ctx.layout?.sections[ctx.sectionIndex]?.title ?? 'Diagram',
										description: 'Diagram',
										name: 'Figure'
									}
								})
							]
						})
					);
					break;
				}
				const children: any[] = [];
				const codeRun = { font: CODE_FONT, size: 18, color: BODY_COLOR };
				const codeEdge = { style: BorderStyle.SINGLE, size: 4, color: CODE_FILL, space: 6 };
				(token.text ?? '').split('\n').forEach((line: string, idx: number) => {
					if (idx > 0) children.push(new TextRun({ ...codeRun, text: '', break: 1 }));
					children.push(new TextRun({ ...codeRun, text: line || ' ' }));
				});
				out.push(
					new Paragraph({
						style: 'ArcherCode',
						spacing: { before: 120, after: 120, line: 276 },
						shading: { type: ShadingType.CLEAR, fill: CODE_FILL, color: 'auto' },
						border: { top: codeEdge, bottom: codeEdge, left: codeEdge, right: codeEdge },
						children
					})
				);
				break;
			}

			case 'table':
				out.push(...tableBlock(token, ctx));
				break;

			case 'hr':
				out.push(
					new Paragraph({
						spacing: { before: 120, after: 240 },
						border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: HR_RULE, space: 1 } },
						children: []
					})
				);
				break;

			case 'space':
				break;

			case 'html': {
				const stripped = decodeHtmlEntities((token.raw ?? '').replace(/<[^>]*>/g, '').trim());
				if (stripped) {
					out.push(
						new Paragraph({
							spacing: BODY_SPACING,
							children: runsToChildren([{ text: stripped }], ctx.docxLib)
						})
					);
				}
				break;
			}

			default:
				if (token.raw && token.raw.trim()) {
					out.push(
						new Paragraph({
							spacing: BODY_SPACING,
							children: runsToChildren([{ text: token.raw.trim() }], ctx.docxLib)
						})
					);
				}
		}
	});

	return out;
};

const listNumbering = (docxLib: typeof import('docx')) => {
	const { LevelFormat, AlignmentType } = docxLib;
	type Format = (typeof LevelFormat)[keyof typeof LevelFormat];
	const level = (lvl: number, format: Format, text: string) => ({
		level: lvl,
		format,
		text,
		alignment: AlignmentType.LEFT,
		style: {
			paragraph: { indent: { left: 360 + lvl * 360, hanging: 280 } },
			run: { font: FONT, size: BODY_SIZE, color: BODY_COLOR }
		}
	});

	return {
		config: [
			{
				reference: BULLET_LIST,
				levels: [
					level(0, LevelFormat.BULLET, '•'),
					level(1, LevelFormat.BULLET, '◦'),
					level(2, LevelFormat.BULLET, '▪')
				]
			},
			{
				reference: NUMBERED_LIST,
				levels: [
					level(0, LevelFormat.DECIMAL, '%1.'),
					level(1, LevelFormat.LOWER_LETTER, '%2.'),
					level(2, LevelFormat.LOWER_ROMAN, '%3.')
				]
			}
		]
	};
};

// Chat titles often start with an emoji (e.g. "👋 Friendly Greeting"); drop pictographs
// for the document title property.
const cleanTitle = (title?: string) =>
	(title ?? '')
		.replace(/[\p{Extended_Pictographic}\u{FE0F}\u{200D}]/gu, '')
		.replace(/\s+/g, ' ')
		.trim() || 'Document';

type DocxOutputType = 'blob' | 'nodebuffer' | 'arraybuffer';

// Exported separately from downloadMessageAsDocx so the output can be built and
// inspected (e.g. in tests, as 'nodebuffer') without going through the browser-only save step.
export const buildMessageDocx = async <T extends DocxOutputType = 'blob'>(
	markdown: string,
	meta: MessageDocxMeta = {},
	outputType: T = 'blob' as T
) => {
	const [docxLib, { marked }] = await Promise.all([import('docx'), import('marked')]);
	const { patchDocument, PatchType, Paragraph, TextRun, PageBreak } = docxLib;

	const documentMarkdown = prepareDocumentMarkdown(markdown ?? '');
	const diagrams = meta.diagrams ?? (await renderReportDiagrams(documentMarkdown));
	const ctx: ConvertContext = {
		docxLib,
		nextListInstance: 1,
		layout: meta.layout,
		diagrams,
		sectionIndex: -1
	};
	const tokens = marked.lexer(documentMarkdown);
	const firstSection = tokens.findIndex((token) => token.type === 'heading' && token.depth === 2);
	const bodyChildren = blocksToDocx(
		meta.layout && firstSection >= 0 ? tokens.slice(firstSection) : tokens,
		ctx
	);

	const design = resolveReportDesign(meta.layout);
	if (meta.reportDate && design.coverPage) {
		bodyChildren.unshift(
			new Paragraph({
				spacing: { before: 1000, after: 600 },
				children: [
					new TextRun({ text: 'ARCHER & ROUND / REPORT', font: FONT, size: 22, color: '0F62FE' })
				]
			}),
			new Paragraph({
				spacing: { after: 500 },
				children: [
					new TextRun({ text: cleanTitle(meta.title), font: FONT, size: 64, color: BODY_COLOR })
				]
			}),
			new Paragraph({
				children: [
					new TextRun({ text: meta.layout?.subtitle ?? '', font: FONT, size: 28, color: '525252' })
				],
				spacing: { after: 600 }
			}),
			new Paragraph({
				spacing: { before: 300, after: 300 },
				children: [new TextRun({ text: meta.reportDate, font: FONT, size: 22, color: '525252' })]
			}),
			...(meta.layout && design.contents
				? [
						...(meta.layout.sections.length > 4
							? [new Paragraph({ children: [new PageBreak()] })]
							: []),
						new Paragraph({
							spacing: { before: 600, after: 240 },
							children: [
								new TextRun({
									text: /[\u3400-\u9fff]/.test(meta.title ?? '') ? '报告导览' : 'IN THIS REPORT',
									font: FONT,
									size: 20,
									color: '0F62FE',
									bold: true
								})
							]
						}),
						...meta.layout.sections.map(
							(section, index) =>
								new Paragraph({
									spacing: { after: 160 },
									children: [
										new TextRun({
											text: `${design.numberedSections ? String(index + 1).padStart(2, '0') + '   ' : ''}${section.title}`,
											font: FONT,
											size: 24,
											color: BODY_COLOR
										})
									]
								})
						)
					]
				: []),
			new Paragraph({ children: [new PageBreak()] })
		);
	}

	if (meta.layout && !design.coverPage) {
		const intro = [
			new Paragraph({
				keepNext: true,
				spacing: { before: 120, after: 240 },
				children: [
					new TextRun({
						text: cleanTitle(meta.title ?? meta.layout.title),
						font: FONT,
						size: 40,
						bold: true,
						color: BODY_COLOR
					})
				]
			})
		];
		if (meta.layout.subtitle)
			intro.push(
				new Paragraph({
					keepNext: true,
					spacing: { after: 240 },
					children: [
						new TextRun({ text: meta.layout.subtitle, font: FONT, size: 24, color: '525252' })
					]
				})
			);
		if (design.contents)
			intro.push(
				...meta.layout.sections.map(
					(section, index) =>
						new Paragraph({
							spacing: { after: 100 },
							children: [
								new TextRun({
									text: `${design.numberedSections ? String(index + 1).padStart(2, '0') + '   ' : ''}${section.title}`,
									font: FONT,
									size: BODY_SIZE,
									color: BODY_COLOR
								})
							]
						})
				)
			);
		bodyChildren.unshift(...intro);
	}

	const templateRes = await fetch(RESPONSE_TEMPLATE_URL);
	if (!templateRes.ok) {
		throw new globalThis.Error(`Unable to load DOCX template: ${templateRes.status}`);
	}

	return patchDocument({
		outputType,
		data: await templateRes.arrayBuffer(),
		numbering: listNumbering(docxLib),
		properties: { title: cleanTitle(extractDocumentTitle(documentMarkdown) || meta.title) },
		patches: {
			CONTENT: {
				type: PatchType.DOCUMENT,
				children: bodyChildren.length ? bodyChildren : [new Paragraph({ text: '' })]
			}
		}
	});
};

export const downloadMessageAsDocx = async (
	markdown: string,
	fileName: string,
	meta: MessageDocxMeta = {}
) => {
	const blob = await buildMessageDocx(markdown, meta, 'blob');
	saveAs(blob, `${fileName}.docx`);
};
