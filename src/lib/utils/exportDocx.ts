import fileSaver from 'file-saver';
const { saveAs } = fileSaver;

// Built by scripts/build-docx-template.mjs: the Archer & Round letterhead (header,
// footer, logo copied from docs/archer_template.docx) and the paragraph styles
// (Normal, Heading 1-6, Archer Quote, Archer Code) matching the PDF export. Exports
// patch the response into its {{CONTENT}} placeholder.
const RESPONSE_TEMPLATE_URL = '/assets/docx/archer-response-template.docx';

// Mirrors the PDF export's table/code colours (.pdf-export-snapshot in ResponseMessage.svelte).
const TABLE_RULE = 'CCCCCC';
const TABLE_HEADER_FILL = 'F0F0F0';
const INLINE_CODE_FILL = 'F4F4F4';
const HR_RULE = 'DDDDDD';

// Formatting is applied directly to every paragraph and run (not only through the
// template's styles) because some viewers, e.g. macOS Quick Look, ignore styles.xml.
const FONT = 'Arial';
const BODY_SIZE = 21; // half-points (10.5pt, ~14px like the PDF)
const BODY_COLOR = '000000';
const CODE_FONT = 'Consolas';
const CODE_FILL = 'F6F8FA';
const QUOTE_RULE = 'CCCCCC';
// A4 width minus the template's 1224-twip side margins.
const CONTENT_WIDTH = 11906 - 2 * 1224;
const BODY_SPACING = { before: 120, after: 120, line: 360 };
const HEADING_SIZE: Record<number, number> = { 1: 33, 2: 28, 3: 26, 4: 22, 5: 22, 6: 22 };

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

type ConvertContext = { docxLib: any; nextListInstance: number };

export type MessageDocxMeta = {
	// Stored as the document's title property.
	title?: string;
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
				runOptions.color = '1A56DB';
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
	const { Paragraph, Table, TableRow, TableCell, WidthType, BorderStyle, ShadingType, AlignmentType } =
		ctx.docxLib;

	const rule = { style: BorderStyle.SINGLE, size: 4, color: TABLE_RULE };
	const alignOf = (idx: number) =>
		token.align?.[idx] === 'center'
			? AlignmentType.CENTER
			: token.align?.[idx] === 'right'
				? AlignmentType.RIGHT
				: AlignmentType.LEFT;

	// Explicit twip widths as well as the percentage, for viewers that ignore percentages.
	const columnCount = Math.max(1, token.header?.length ?? 1);
	const columnWidth = Math.floor(CONTENT_WIDTH / columnCount);

	const cell = (tokens: any[], idx: number, header: boolean) =>
		new TableCell({
			width: { size: columnWidth, type: WidthType.DXA },
			shading: header
				? { type: ShadingType.CLEAR, fill: TABLE_HEADER_FILL, color: 'auto' }
				: undefined,
			margins: { top: 80, bottom: 80, left: 120, right: 120 },
			children: [
				new Paragraph({
					alignment: alignOf(idx),
					spacing: { before: 0, after: 0, line: 260 },
					children: runsToChildren(
						flattenInline(tokens, header ? { bold: true } : {}),
						ctx.docxLib
					)
				})
			]
		});

	return [
		new Table({
			width: { size: 100, type: WidthType.PERCENTAGE },
			columnWidths: Array(columnCount).fill(columnWidth),
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
					(row: any[]) =>
						new TableRow({
							children: row.map((c, idx) => cell(c.tokens, idx, false))
						})
				)
			]
		}),
		new Paragraph({ spacing: { before: 0, after: 120 }, children: [] })
	];
};

// Walks marked's block-level tokens and builds the matching docx Paragraph/Table nodes.
// Fonts, sizes and colours come from the template's styles; only structure is set here.
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

	tokens.forEach((token) => {
		switch (token.type) {
			case 'heading':
				out.push(
					new Paragraph({
						heading: headingLevel[token.depth] ?? HeadingLevel.HEADING_6,
						keepNext: true,
						spacing: { before: 240, after: 120, line: 300 },
						children: runsToChildren(flattenInline(token.tokens), ctx.docxLib, {
							bold: true,
							size: HEADING_SIZE[token.depth] ?? 22
						})
					})
				);
				break;

			case 'paragraph':
				out.push(
					new Paragraph({
						spacing: BODY_SPACING,
						children: runsToChildren(flattenInline(token.tokens), ctx.docxLib)
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
									color: '333333'
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
		.trim() || 'Response';

type DocxOutputType = 'blob' | 'nodebuffer' | 'arraybuffer';

// Exported separately from downloadMessageAsDocx so the output can be built and
// inspected (e.g. in tests, as 'nodebuffer') without going through the browser-only save step.
export const buildMessageDocx = async <T extends DocxOutputType = 'blob'>(
	markdown: string,
	meta: MessageDocxMeta = {},
	outputType: T = 'blob' as T
) => {
	const [docxLib, { marked }] = await Promise.all([import('docx'), import('marked')]);
	const { patchDocument, PatchType, Paragraph } = docxLib;

	const ctx: ConvertContext = { docxLib, nextListInstance: 1 };
	const bodyChildren = blocksToDocx(marked.lexer(markdown ?? ''), ctx);

	const templateRes = await fetch(RESPONSE_TEMPLATE_URL);
	if (!templateRes.ok) {
		throw new globalThis.Error(`Unable to load DOCX template: ${templateRes.status}`);
	}

	return patchDocument({
		outputType,
		data: await templateRes.arrayBuffer(),
		numbering: listNumbering(docxLib),
		properties: { title: cleanTitle(meta.title) },
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
