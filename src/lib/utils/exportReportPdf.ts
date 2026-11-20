import '@fontsource/ibm-plex-sans/latin-400.css';
import '@fontsource/ibm-plex-sans/latin-500.css';
import '@fontsource/ibm-plex-sans/latin-600.css';
import '@fontsource/ibm-plex-sans/latin-700.css';
import '@fontsource/ibm-plex-mono/latin-400.css';
import '@fontsource/ibm-plex-mono/latin-500.css';

import DOMPurify from 'dompurify';
import { marked } from 'marked';

import { removeAllDetails } from '$lib/utils';
import { reportColumnWeights, resolveReportDesign } from './reportLayout';
import { renderReportDiagrams } from './reportDiagrams';
import type { ConversationReport, ReportProgress } from './generateConversationReport';
import {
	extractDocumentTitle,
	sanitizeReportFileName,
	selectDocumentMessages,
	type ReportPdfMessage,
	type ReportSourceMessage
} from './exportReportPdfModel';

export type ReportPdfOptions = {
	title: string;
	fileName?: string;
	brandName?: string;
	brandTagline?: string;
	brandLogoUrl?: string;
	footerNote?: string;
	report?: ConversationReport;
	onProgress?: (progress: ReportProgress) => void;
};

type PdfBlock = {
	element: HTMLElement;
	height: number;
	keepWithNext: boolean;
	pageBreakBefore?: boolean;
};

const PAGE_WIDTH_MM = 210;
const PAGE_HEIGHT_MM = 297;
const PX_PER_MM = 96 / 25.4;
const PAGE_WIDTH_PX = Math.round(PAGE_WIDTH_MM * PX_PER_MM);
const PAGE_HEIGHT_PX = Math.round(PAGE_HEIGHT_MM * PX_PER_MM);
const MARGIN_X_PX = Math.round(18 * PX_PER_MM);
const BODY_TOP_PX = Math.round(24 * PX_PER_MM);
const BODY_WIDTH_PX = PAGE_WIDTH_PX - MARGIN_X_PX * 2;
const BODY_HEIGHT_PX = Math.round((PAGE_HEIGHT_MM - 24 - 20) * PX_PER_MM);
const BLOCK_GAP_PX = 10;

const DEFAULT_BRAND = {
	name: 'ARCHER & ROUND',
	tagline: 'GOVERN. ASSURE. PROTECT.',
	logoUrl: '/assets/images/archer_letterhead_logo.svg',
	footerNote: 'ARCHER & ROUND · archerround.com · 1800 841 224'
};

const REPORT_CSS = `
.pdf-report-offscreen {
	position: fixed;
	left: -10000px;
	top: 0;
	width: ${PAGE_WIDTH_PX}px;
	background: #ffffff;
	color: #161616;
	font-family: 'IBM Plex Sans', 'Inter', 'Helvetica Neue', Arial, 'PingFang SC', 'Microsoft YaHei', sans-serif;
	font-size: 14px;
	line-height: 1.6;
}
.pdf-report-offscreen[data-density="compact"] { line-height: 1.5; }
.pdf-report-offscreen[data-density="compact"] h2 { font-size: 20px; padding-top: 8px; margin-bottom: 10px; }
.pdf-inline-title { padding: 8px 0 20px; }
.pdf-inline-title p { color: #525252; }
.pdf-report-page {
	position: relative;
	width: ${PAGE_WIDTH_PX}px;
	height: ${PAGE_HEIGHT_PX}px;
	background: #ffffff;
	color: #161616;
	overflow: hidden;
}
.pdf-report-header {
	position: absolute;
	left: ${MARGIN_X_PX}px;
	right: ${MARGIN_X_PX}px;
	top: ${Math.round(8 * PX_PER_MM)}px;
	display: flex;
	align-items: center;
	justify-content: space-between;
	gap: 16px;
	font-size: 11px;
	letter-spacing: 0.06em;
	text-transform: uppercase;
}
.pdf-report-brand { display: flex; align-items: center; gap: 10px; }
.pdf-brand-logo { height: 32px; width: auto; display: block; }
.pdf-brand-name { font-size: 13px; font-weight: 600; letter-spacing: 0.04em; color: #161616; }
.pdf-brand-tagline { font-size: 9px; letter-spacing: 0.08em; color: #0f62fe; margin-top: 2px; }
.pdf-report-header-rule {
	position: absolute;
	left: ${MARGIN_X_PX}px;
	right: ${MARGIN_X_PX}px;
	top: ${Math.round(19 * PX_PER_MM)}px;
	height: 2px;
	background: #0f62fe;
}
.pdf-report-body {
	position: absolute;
	left: ${MARGIN_X_PX}px;
	top: ${BODY_TOP_PX}px;
	width: ${BODY_WIDTH_PX}px;
	height: ${BODY_HEIGHT_PX}px;
	overflow: hidden;
	display: flex;
	flex-direction: column;
}
.pdf-report-body > * { flex-shrink: 0; }
.pdf-report-measure {
	position: static;
	height: auto;
	overflow: visible;
}
.pdf-block { margin: 0 0 ${BLOCK_GAP_PX}px; }
.pdf-report-body p { margin: 0 0 8px; }
.pdf-report-body h1,
.pdf-report-body h2,
.pdf-report-body h3,
.pdf-report-body h4,
.pdf-report-body h5,
.pdf-report-body h6 {
	font-weight: 600;
	color: #161616;
	margin: 12px 0 6px;
	line-height: 1.3;
}
.pdf-report-body h1 { font-size: 26px; line-height: 1.2; margin-top: 0; }
.pdf-report-body h2 { font-size: 23px; padding-top: 14px; margin-bottom: 16px; color: #161616; letter-spacing: -0.4px; }
.pdf-report-body h3 { font-size: 15.5px; }
.pdf-report-body h4,
.pdf-report-body h5,
.pdf-report-body h6 { font-size: 14px; }
.pdf-report-body ul,
.pdf-report-body ol { padding-left: 22px; margin: 0 0 8px; }
.pdf-report-body li { margin: 4px 0; }
.pdf-report-body table {
	width: 100%;
	border-collapse: collapse;
	table-layout: fixed;
	font-size: 12.5px;
	margin: 0 0 14px;
}
.pdf-report-body th {
	background: #e8f0ff;
	font-weight: 600;
	text-align: left;
	border-bottom: 1px solid #c6c6c6;
	padding: 9px 10px;
	vertical-align: top;
	word-break: break-word;
}
.pdf-report-body td {
	border-bottom: 1px solid #e0e0e0;
	padding: 9px 10px;
	vertical-align: top;
	word-break: break-word;
}
.pdf-report-body pre {
	background: #f4f4f4;
	border-left: 3px solid #c6c6c6;
	padding: 10px 12px;
	font-family: 'IBM Plex Mono', monospace;
	font-size: 12px;
	line-height: 1.45;
	white-space: pre-wrap;
	word-break: break-word;
	margin: 0 0 10px;
}
.pdf-report-body code {
	font-family: 'IBM Plex Mono', monospace;
	font-size: 12px;
	background: #f4f4f4;
	padding: 1px 4px;
}
.pdf-report-body pre code { background: transparent; padding: 0; }
.pdf-report-body blockquote {
	border-left: 3px solid #0f62fe;
	background: #f4f4f4;
	padding: 8px 12px;
	color: #393939;
	margin: 0 0 10px;
}
.pdf-report-body a { color: #0f62fe; text-decoration: underline; word-break: break-word; }
.pdf-report-body img { max-width: 100%; height: auto; display: block; margin: 0 auto; }
.pdf-report-body hr { border: none; border-top: 1px solid #e0e0e0; margin: 12px 0; }
.pdf-report-body tbody tr:nth-child(even) { background: #f4f4f4; }
.pdf-report-cover { padding: 72px 0 0; }
.pdf-cover-label { color: #0f62fe; font-size: 12px; letter-spacing: 2px; }
.pdf-report-body .pdf-report-cover h1 { font-size: 42px; font-weight: 500; margin: 54px 0 32px; line-height: 1.2; overflow-wrap: anywhere; }
.pdf-cover-subtitle { font-size: 20px; color: #525252; max-width: 560px; margin-bottom: 40px !important; }
.pdf-report-body p.pdf-indented { text-indent: 2em; }
.pdf-report-body p.pdf-caption { font-size: 12px; color: #525252; margin: 16px 0 8px; }
.pdf-report-body figure { margin: 8px 0 18px; padding: 12px; border: 1px solid #e0e0e0; background: #fff; }
.pdf-report-body figure img { max-height: 620px; object-fit: contain; }
.pdf-cover-contents { margin-top: 56px; }
.pdf-cover-contents h3, .pdf-contents-title { font-size: 12px; letter-spacing: 1.4px; color: #0f62fe; margin-bottom: 20px; }
.pdf-cover-contents div, .pdf-contents-item { padding: 10px 0; border-bottom: 1px solid #e0e0e0; font-size: 16px; }
.pdf-cover-contents span, .pdf-contents-item span { display: inline-block; width: 42px; color: #0f62fe; font-family: 'IBM Plex Mono', monospace; font-size: 12px; }
.pdf-image-placeholder {
	border: 1px solid #e0e0e0;
	background: #f4f4f4;
	color: #525252;
	padding: 12px;
	font-size: 12px;
}
`;

const escapeHtml = (value: string): string =>
	value
		.replace(/&/g, '&amp;')
		.replace(/</g, '&lt;')
		.replace(/>/g, '&gt;')
		.replace(/"/g, '&quot;')
		.replace(/'/g, '&#39;');

const ensureFonts = async (): Promise<void> => {
	if (!document.fonts) return;
	await Promise.all([
		document.fonts.load('400 14px "IBM Plex Sans"'),
		document.fonts.load('600 14px "IBM Plex Sans"'),
		document.fonts.load('400 12px "IBM Plex Mono"')
	]);
	await document.fonts.ready;
};

const createOffscreenRoot = (): HTMLDivElement => {
	const root = document.createElement('div');
	root.className = 'pdf-report-offscreen';
	root.setAttribute('aria-hidden', 'true');
	const style = document.createElement('style');
	style.textContent = REPORT_CSS;
	root.appendChild(style);
	document.body.appendChild(root);
	return root;
};

const createMeasureBody = (root: HTMLElement): HTMLDivElement => {
	const measureBody = document.createElement('div');
	measureBody.className = 'pdf-report-body pdf-report-measure';
	root.appendChild(measureBody);
	return measureBody;
};

const measureElement = (element: HTMLElement, measureBody: HTMLElement): number => {
	measureBody.appendChild(element);
	const style = getComputedStyle(element);
	const height =
		element.getBoundingClientRect().height +
		(parseFloat(style.marginTop) || 0) +
		(parseFloat(style.marginBottom) || 0);
	measureBody.removeChild(element);
	return height;
};

const prepareImages = async (root: HTMLElement): Promise<void> => {
	const images = Array.from(root.querySelectorAll('img'));
	await Promise.all(
		images.map(
			(image) =>
				new Promise<void>((resolve) => {
					if (image.complete && image.naturalWidth > 0) {
						resolve();
						return;
					}
					const timeout = setTimeout(() => resolve(), 5000);
					image.onload = () => {
						clearTimeout(timeout);
						resolve();
					};
					image.onerror = () => {
						clearTimeout(timeout);
						const placeholder = document.createElement('div');
						placeholder.className = 'pdf-image-placeholder';
						placeholder.textContent = `[Image: ${image.alt || image.getAttribute('src') || 'unavailable'}]`;
						image.replaceWith(placeholder);
						resolve();
					};
				})
		)
	);
};

const createCodeBlock = (text: string): HTMLPreElement => {
	const pre = document.createElement('pre');
	const code = document.createElement('code');
	code.textContent = text;
	pre.appendChild(code);
	return pre;
};

const splitCodeBlock = (pre: HTMLPreElement, measureBody: HTMLElement): HTMLElement[] => {
	const text = pre.textContent ?? '';
	const lines = text.split('\n');
	const chunks: HTMLElement[] = [];
	let current: string[] = [];

	for (const line of lines) {
		current.push(line);
		const candidate = createCodeBlock(current.join('\n'));
		if (measureElement(candidate, measureBody) > BODY_HEIGHT_PX && current.length > 1) {
			current.pop();
			chunks.push(createCodeBlock(current.join('\n')));
			current = [line];
		}
	}
	if (current.length) chunks.push(createCodeBlock(current.join('\n')));
	return chunks.length ? chunks : [createCodeBlock('')];
};

const textSlice = (element: HTMLElement, start: number, end: number): HTMLElement => {
	const clone = element.cloneNode(false) as HTMLElement;
	const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
	const range = document.createRange();
	let offset = 0;
	let begun = false;
	while (walker.nextNode()) {
		const node = walker.currentNode;
		const length = node.textContent?.length ?? 0;
		if (!begun && start < offset + length) {
			range.setStart(node, Math.max(0, start - offset));
			begun = true;
		}
		if (begun && end <= offset + length) {
			range.setEnd(node, end - offset);
			break;
		}
		offset += length;
	}
	if (begun) clone.appendChild(range.cloneContents());
	return clone;
};

const splitTextElement = (
	element: HTMLElement,
	measureBody: HTMLElement,
	limit = BODY_HEIGHT_PX
): HTMLElement[] => {
	const text = element.textContent ?? '';
	if (!text.trim()) return [element];
	let low = 1,
		high = text.length,
		best = 0;
	while (low <= high) {
		const mid = Math.floor((low + high) / 2);
		if (measureElement(textSlice(element, 0, mid), measureBody) <= limit) {
			best = mid;
			low = mid + 1;
		} else high = mid - 1;
	}
	if (!best || best === text.length) return [element];
	const space = text.lastIndexOf(' ', best);
	if (space > best * 0.6) best = space + 1;
	const rest = textSlice(element, best, text.length);
	rest.classList.remove('pdf-indented');
	return [textSlice(element, 0, best), rest];
};

const splitContainer = (element: HTMLElement, measureBody: HTMLElement): HTMLElement[] => {
	const children = Array.from(element.children) as HTMLElement[];
	if (!children.length) return splitTextElement(element, measureBody);

	return children.map((child) => {
		const wrapper = element.cloneNode(false) as HTMLElement;
		wrapper.appendChild(child);
		return wrapper;
	});
};

const splitToFit = async (
	element: HTMLElement,
	measureBody: HTMLElement
): Promise<HTMLElement[]> => {
	await prepareImages(element);
	if (measureElement(element, measureBody) <= BODY_HEIGHT_PX) return [element];

	const tag = element.tagName.toLowerCase();
	let parts: HTMLElement[] = [];

	if (tag === 'table' || tag === 'ul' || tag === 'ol') return [element];
	if (tag === 'pre') parts = splitCodeBlock(element as HTMLPreElement, measureBody);
	else if (tag === 'img') {
		(element as HTMLImageElement).style.maxHeight = `${BODY_HEIGHT_PX}px`;
		(element as HTMLImageElement).style.width = 'auto';
		parts = [element];
	} else if (tag === 'blockquote' || tag === 'div' || tag === 'section')
		parts = splitContainer(element, measureBody);
	else parts = splitTextElement(element, measureBody);

	if (parts.length <= 1) return parts;

	const result: HTMLElement[] = [];
	for (const part of parts) {
		result.push(...(await splitToFit(part, measureBody)));
	}
	return result;
};

const finalizeBlock = (
	element: HTMLElement,
	keepWithNext: boolean,
	measureBody: HTMLElement
): PdfBlock => {
	element.classList.add('pdf-block');
	const height = measureElement(element, measureBody);
	return { element, height, keepWithNext };
};

const markdownToBlocks = async (
	content: string,
	measureBody: HTMLElement,
	report: ConversationReport
): Promise<PdfBlock[]> => {
	const raw = removeAllDetails(content ?? '');
	const html = DOMPurify.sanitize((await marked.parse(raw)) as string, {
		FORBID_TAGS: ['script', 'iframe', 'style', 'button', 'input', 'select', 'textarea'],
		FORBID_ATTR: ['onerror', 'onclick', 'onload']
	});
	const container = document.createElement('div');
	container.innerHTML = html;
	if (report.layout) {
		while (container.firstElementChild && container.firstElementChild.tagName !== 'H2')
			container.firstElementChild.remove();
	}
	const diagrams = report.diagrams ?? (await renderReportDiagrams(content));
	for (const code of Array.from(container.querySelectorAll('pre > code.language-mermaid'))) {
		const source = (code.textContent ?? '').trimEnd();
		const diagram = diagrams[source];
		if (!diagram) throw new Error('Missing rendered report diagram');
		const figure = document.createElement('figure');
		const image = document.createElement('img');
		image.src = diagram.dataUrl;
		image.alt = code.parentElement?.previousElementSibling?.textContent || 'Diagram';
		const scale = Math.min((BODY_WIDTH_PX - 26) / diagram.width, 620 / diagram.height, 1);
		image.style.width = `${Math.round(diagram.width * scale)}px`;
		image.style.height = `${Math.round(diagram.height * scale)}px`;
		figure.appendChild(image);
		code.parentElement!.replaceWith(figure);
	}
	for (const table of Array.from(container.querySelectorAll('table'))) {
		const weights = reportColumnWeights(
			Array.from(table.rows).map((row) =>
				Array.from(row.cells).map((cell) => cell.textContent ?? '')
			)
		);
		const colgroup = document.createElement('colgroup');
		weights.forEach((weight) => {
			const col = document.createElement('col');
			col.style.width = `${weight * 100}%`;
			colgroup.appendChild(col);
		});
		table.prepend(colgroup);
	}
	await prepareImages(container);

	const blocks: PdfBlock[] = [];
	let sectionIndex = -1;
	for (const child of Array.from(container.children) as HTMLElement[]) {
		const tag = child.tagName.toLowerCase();
		if (tag === 'h2') {
			sectionIndex++;
			if (!resolveReportDesign(report.layout).showSectionHeadings) continue;
		}
		const section = report.layout?.sections[sectionIndex];
		const caption =
			tag === 'p' &&
			child.children.length === 1 &&
			child.firstElementChild?.tagName === 'EM' &&
			['TABLE', 'FIGURE'].includes(child.nextElementSibling?.tagName ?? '');
		if (caption) child.classList.add('pdf-caption');
		else if (tag === 'p' && section?.paragraphStyle === 'indented')
			child.classList.add('pdf-indented');
		child.classList.add('pdf-block');
		const keepWithNext = /^h[1-6]$/.test(tag) || caption;
		const parts = await splitToFit(child, measureBody);
		for (const part of parts) {
			blocks.push({
				...finalizeBlock(part, keepWithNext, measureBody),
				pageBreakBefore:
					tag === 'h2' &&
					sectionIndex > 0 &&
					resolveReportDesign(report.layout).documentType !== 'note' &&
					section?.startOnNewPage
			});
		}
	}
	return blocks;
};

const buildBlocks = async (
	messages: ReportPdfMessage[],
	measureBody: HTMLElement,
	report: ConversationReport
): Promise<PdfBlock[]> => {
	const blocks: PdfBlock[] = [];
	for (const message of messages) {
		blocks.push(...(await markdownToBlocks(message.content, measureBody, report)));
	}
	return blocks;
};

const splitForRemainingSpace = (
	element: HTMLElement,
	measureBody: HTMLElement,
	height: number
): HTMLElement[] => {
	if (height < 100) return [element];
	if (['P', 'PRE'].includes(element.tagName)) return splitTextElement(element, measureBody, height);
	const table = element.tagName === 'TABLE';
	if (!table && !['UL', 'OL'].includes(element.tagName)) return [element];
	const items = table
		? Array.from(element.querySelectorAll('tbody tr'))
		: Array.from(element.children);
	if (items.length < 2) return [element];
	const chunk = (from: number, to: number) => {
		const copy = element.cloneNode(true) as HTMLElement;
		const rows = table ? Array.from(copy.querySelectorAll('tbody tr')) : Array.from(copy.children);
		rows.forEach((row, index) => {
			if (index < from || index >= to) row.remove();
		});
		if (copy.tagName === 'OL')
			copy.setAttribute('start', String(Number(element.getAttribute('start') || 1) + from));
		return copy;
	};
	let count = 0;
	for (let end = 1; end < items.length; end++) {
		if (measureElement(chunk(0, end), measureBody) > height) break;
		count = end;
	}
	return count ? [chunk(0, count), chunk(count, items.length)] : [element];
};

const paginateBlocks = (input: PdfBlock[], measureBody: HTMLElement): PdfBlock[][] => {
	const blocks = [...input];
	const pages: PdfBlock[][] = [];
	let current: PdfBlock[] = [],
		used = 0;
	const pushPage = () => {
		if (current.length) pages.push(current);
		current = [];
		used = 0;
	};
	for (let index = 0; index < blocks.length; index++) {
		let block = blocks[index];
		if (block.pageBreakBefore && current.length) pushPage();
		let following = 0;
		if (block.keepWithNext) {
			for (let next = index + 1; next < blocks.length; next++) {
				const candidate = blocks[next];
				let minimumHeight = candidate.height;
				if (candidate.element.tagName === 'TABLE') {
					const firstRow = candidate.element.cloneNode(true) as HTMLElement;
					Array.from(firstRow.querySelectorAll('tbody tr'))
						.slice(1)
						.forEach((row) => row.remove());
					minimumHeight = measureElement(firstRow, measureBody);
				} else if (['UL', 'OL'].includes(candidate.element.tagName)) {
					const firstItem = candidate.element.cloneNode(true) as HTMLElement;
					Array.from(firstItem.children)
						.slice(1)
						.forEach((item) => item.remove());
					minimumHeight = measureElement(firstItem, measureBody);
				} else if (!candidate.keepWithNext && candidate.element.tagName !== 'FIGURE')
					minimumHeight = Math.min(candidate.height, 100);
				following += minimumHeight;
				if (!candidate.keepWithNext) break;
			}
		}
		if (block.keepWithNext && current.length && used + block.height + following > BODY_HEIGHT_PX)
			pushPage();
		if (used + block.height > BODY_HEIGHT_PX) {
			let parts = !block.keepWithNext
				? splitForRemainingSpace(block.element, measureBody, BODY_HEIGHT_PX - used)
				: [block.element];
			if (parts.length === 1 && current.length) {
				pushPage();
				if (block.height > BODY_HEIGHT_PX)
					parts = splitForRemainingSpace(block.element, measureBody, BODY_HEIGHT_PX);
			}
			if (parts.length > 1) {
				block = finalizeBlock(parts[0], false, measureBody);
				blocks.splice(index + 1, 0, finalizeBlock(parts[1], false, measureBody));
			}
		}
		if (block.height > BODY_HEIGHT_PX + 1)
			throw new Error(
				'A report block exceeds the printable page. Please simplify the table or figure.'
			);
		current.push(block);
		used += block.height;
	}
	pushPage();
	return pages.length ? pages : [[]];
};

const prepareBrandLogo = async (logoUrl?: string): Promise<string | null> => {
	if (!logoUrl) return null;

	try {
		const response = await fetch(logoUrl);
		if (!response.ok) return null;
		const blob = await response.blob();
		const sourceDataUrl = await new Promise<string>((resolve, reject) => {
			const reader = new FileReader();
			reader.onload = () => resolve(reader.result as string);
			reader.onerror = reject;
			reader.readAsDataURL(blob);
		});
		const image = await new Promise<HTMLImageElement>((resolve, reject) => {
			const logoImage = new Image();
			logoImage.onload = () => resolve(logoImage);
			logoImage.onerror = reject;
			logoImage.src = sourceDataUrl;
		});
		const canvas = document.createElement('canvas');
		canvas.width = 484;
		canvas.height = 368;
		const context = canvas.getContext('2d');
		if (!context) return null;
		context.clearRect(0, 0, canvas.width, canvas.height);
		context.drawImage(image, 0, 0, canvas.width, canvas.height);
		return canvas.toDataURL('image/png');
	} catch {
		return null;
	}
};

const renderPage = (
	blocks: PdfBlock[],
	options: ReportPdfOptions,
	logoDataUrl: string | null
): HTMLDivElement => {
	const brandName = options.brandName ?? DEFAULT_BRAND.name;
	const brandTagline = options.brandTagline ?? DEFAULT_BRAND.tagline;
	const logoHtml = logoDataUrl
		? `<img class="pdf-brand-logo" src="${logoDataUrl}" alt="${escapeHtml(brandName)}">`
		: '';
	const page = document.createElement('div');
	page.className = 'pdf-report-page';
	page.innerHTML = `
		<div class="pdf-report-header">
			<div class="pdf-report-brand">
				${logoHtml}
				<div>
					<div class="pdf-brand-name">${escapeHtml(brandName)}</div>
					<div class="pdf-brand-tagline">${escapeHtml(brandTagline)}</div>
				</div>
			</div>
		</div>
		<div class="pdf-report-header-rule"></div>
	`;
	const body = document.createElement('div');
	body.className = 'pdf-report-body';
	blocks.forEach((block) => body.appendChild(block.element));
	page.appendChild(body);
	return page;
};

const drawFooter = (
	pdf: import('jspdf').jsPDF,
	pageNumber: number,
	totalPages: number,
	options: ReportPdfOptions
): void => {
	pdf.setDrawColor(224, 224, 224);
	pdf.setLineWidth(0.3);
	pdf.line(18, 282, 192, 282);
	pdf.setFont('helvetica', 'normal');
	pdf.setFontSize(8);
	pdf.setTextColor(82, 82, 82);
	const left = options.footerNote ?? DEFAULT_BRAND.footerNote;
	pdf.text(left, 18, 287);
	pdf.text(`Page ${pageNumber} / ${totalPages}`, 192, 287, { align: 'right' });
};

export const buildReportPdf = async (
	sourceMessages: ReportSourceMessage[],
	options: ReportPdfOptions
): Promise<import('jspdf').jsPDF> => {
	const { generateConversationReport } = await import('./generateConversationReport');
	const report =
		options.report ??
		(await generateConversationReport(
			sourceMessages,
			options.title,
			localStorage.token,
			options.onProgress
		));
	const messages = selectDocumentMessages([{ role: 'assistant', content: report.markdown }]);
	options = { ...options, title: report.title, fileName: report.title };
	if (!messages.length) throw new Error('No exportable messages');

	const [{ default: jsPDF }, { default: html2canvas }] = await Promise.all([
		import('jspdf'),
		import('html2canvas-pro')
	]);

	await ensureFonts();
	const logoDataUrl = await prepareBrandLogo(options.brandLogoUrl ?? DEFAULT_BRAND.logoUrl);
	const design = resolveReportDesign(report.layout);
	const root = createOffscreenRoot();
	root.dataset.density = design.density;
	const measureBody = createMeasureBody(root);

	try {
		const blocks = await buildBlocks(messages, measureBody, report);
		const cover = document.createElement('section');
		cover.className = design.coverPage ? 'pdf-report-cover' : 'pdf-inline-title';
		cover.innerHTML = `${design.coverPage ? `<div class="pdf-cover-label">${escapeHtml(options.brandName ?? DEFAULT_BRAND.name)} / REPORT</div>` : ''}<h1>${escapeHtml(report.title)}</h1>${report.layout?.subtitle ? `<p class="${design.coverPage ? 'pdf-cover-subtitle' : 'pdf-inline-subtitle'}">${escapeHtml(report.layout.subtitle)}</p>` : ''}${design.coverPage ? `<p>${escapeHtml(report.generatedAt)}</p>` : ''}`;
		const frontPages: PdfBlock[][] = design.coverPage
			? [[{ element: cover, height: BODY_HEIGHT_PX, keepWithNext: false }]]
			: [];
		if (report.layout && design.contents) {
			const contents = document.createElement('div');
			contents.className = 'pdf-cover-contents';
			contents.innerHTML =
				`<h3>${/[\u3400-\u9fff]/.test(report.title) ? '报告导览' : 'IN THIS REPORT'}</h3>` +
				report.layout.sections
					.map(
						(section, index) =>
							`<div class="pdf-contents-item"><span>${design.numberedSections ? String(index + 1).padStart(2, '0') : '—'}</span>${escapeHtml(section.title)}</div>`
					)
					.join('');
			if (design.coverPage && report.layout.sections.length <= 4) cover.appendChild(contents);
			if (
				!design.coverPage ||
				!cover.contains(contents) ||
				measureElement(cover, measureBody) > BODY_HEIGHT_PX - 20
			) {
				contents.remove();
				const tocBlocks: PdfBlock[] = [];
				for (const child of Array.from(contents.children) as HTMLElement[]) {
					for (const part of await splitToFit(child, measureBody))
						tocBlocks.push(finalizeBlock(part, child.tagName === 'H3', measureBody));
				}
				if (design.coverPage) frontPages.push(...paginateBlocks(tocBlocks, measureBody));
				else blocks.unshift(...tocBlocks);
			}
		}
		const coverHeading = cover.querySelector('h1')!;
		for (
			let size = 40;
			measureElement(cover, measureBody) > BODY_HEIGHT_PX - 20 && size >= 26;
			size -= 2
		)
			coverHeading.style.fontSize = `${size}px`;
		if (measureElement(cover, measureBody) > BODY_HEIGHT_PX)
			throw new Error('The report title is too long for the cover');
		if (!design.coverPage) blocks.unshift(finalizeBlock(cover, true, measureBody));
		const pages = paginateBlocks(blocks, measureBody);
		pages.unshift(...frontPages);
		const pdf = new jsPDF({ unit: 'mm', format: 'a4', orientation: 'p', compress: true });
		const documentTitle = extractDocumentTitle(messages[0].content) || 'Document';
		pdf.setProperties({
			title: documentTitle,
			subject: documentTitle,
			author: options.brandName ?? DEFAULT_BRAND.name,
			creator: options.brandName ?? DEFAULT_BRAND.name,
			keywords: ''
		});

		for (let index = 0; index < pages.length; index += 1) {
			const pageElement = renderPage(pages[index], options, logoDataUrl);
			root.appendChild(pageElement);
			await prepareImages(pageElement);
			await new Promise((resolve) => requestAnimationFrame(resolve));

			const canvas = await html2canvas(pageElement, {
				backgroundColor: '#ffffff',
				useCORS: true,
				logging: false,
				scale: 2,
				width: PAGE_WIDTH_PX,
				height: PAGE_HEIGHT_PX,
				windowWidth: PAGE_WIDTH_PX
			});

			if (index > 0) pdf.addPage();
			pdf.addImage(
				canvas.toDataURL('image/jpeg', 0.95),
				'JPEG',
				0,
				0,
				PAGE_WIDTH_MM,
				PAGE_HEIGHT_MM,
				undefined,
				'FAST'
			);
			drawFooter(pdf, index + 1, pages.length, options);

			root.removeChild(pageElement);
			canvas.width = 0;
			canvas.height = 0;
			await new Promise((resolve) => setTimeout(resolve, 0));
		}

		return pdf;
	} finally {
		root.remove();
	}
};

export const downloadReportPdf = async (
	sourceMessages: ReportSourceMessage[],
	options: ReportPdfOptions
): Promise<void> => {
	const { generateConversationReport } = await import('./generateConversationReport');
	const report =
		options.report ??
		(await generateConversationReport(
			sourceMessages,
			options.title,
			localStorage.token,
			options.onProgress
		));
	const pdf = await buildReportPdf(sourceMessages, { ...options, report });
	const fileName = sanitizeReportFileName(options.fileName ?? report.title, 'report');
	pdf.save(`${fileName}.pdf`);
};
