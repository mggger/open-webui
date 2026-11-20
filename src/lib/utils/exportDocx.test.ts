import { readFile } from 'node:fs/promises';
import { afterEach, expect, it, vi } from 'vitest';
import JSZip from 'jszip';
import { buildMessageDocx } from './exportDocx';
afterEach(() => {
	vi.unstubAllGlobals();
});
it('builds an editable report with cover, page break and repeating table header', async () => {
	const template = await readFile('static/assets/docx/archer-response-template.docx');
	vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(template)));
	const buffer = await buildMessageDocx(
		'# 项目报告\n\n## 摘要\n\n关键发现。\n\n| 项目 | 状态 |\n| --- | --- |\n| 方案 | 待确认 |',
		{ title: '项目报告', reportDate: '2026-11-20' },
		'nodebuffer'
	);
	const archive = await JSZip.loadAsync(buffer);
	const xml = await archive.file('word/document.xml')!.async('string');
	expect(xml).toContain('ARCHER &amp; ROUND / REPORT');
	expect(xml).toContain('项目报告');
	expect(xml).toContain('w:type="page"');
	expect(xml).toContain('w:tblHeader');
	expect(xml).toContain('E8F0FF');
	expect(xml).not.toContain('{{CONTENT}}');
});

it('embeds diagrams, honors section indentation and keeps captions with figures', async () => {
	const template = await readFile('static/assets/docx/archer-response-template.docx');
	vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(template)));
	const source = 'flowchart TB\nA --> B';
	const layout = {
		title: 'Client report',
		subtitle: 'Architecture',
		sections: [
			{
				title: 'Design',
				purpose: '',
				presentation: 'architecture' as const,
				paragraphStyle: 'indented' as const,
				startOnNewPage: false
			}
		]
	};
	const png =
		'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aD1sAAAAASUVORK5CYII=';
	const bytes = await buildMessageDocx(
		'# Client report\n\nArchitecture\n\n## 01 Design\n\nA paragraph.\n\n*Figure 1 Flow*\n\n```mermaid\n' +
			source +
			'\n```',
		{
			title: layout.title,
			reportDate: '2026-11-20',
			layout,
			diagrams: { [source]: { dataUrl: png, width: 400, height: 300 } }
		},
		'nodebuffer'
	);
	const zip = await JSZip.loadAsync(bytes);
	const xml = await zip.file('word/document.xml')!.async('string');
	expect(xml).toContain('w:firstLine="420"');
	expect(xml).toContain('w:keepNext');
	expect(xml).toContain('wp:extent');
	expect(xml).not.toContain('flowchart');
	expect(xml).not.toContain('AI-assisted');
	expect(xml).not.toContain('Conversation insights');
	expect(
		Object.keys(zip.files).filter((name) => name.startsWith('word/media/') && name.endsWith('.png'))
			.length
	).toBeGreaterThan(0);
});

it('exports a short note with an inline title and no cover, contents or chapter heading', async () => {
	const template = await readFile('static/assets/docx/archer-response-template.docx');
	vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(template)));
	const layout = {
		title: 'Delivery confirmation',
		subtitle: '',
		design: {
			documentType: 'note' as const,
			coverPage: false,
			contents: false,
			numberedSections: false,
			showSectionHeadings: false,
			density: 'compact' as const
		},
		sections: [
			{
				title: 'Confirmation',
				purpose: '',
				presentation: 'narrative' as const,
				paragraphStyle: 'block' as const,
				startOnNewPage: false
			}
		]
	};
	const bytes = await buildMessageDocx(
		'# Delivery confirmation\n\n## Confirmation\n\nDelivery is Friday.',
		{ title: layout.title, reportDate: '2026-11-20', layout },
		'nodebuffer'
	);
	const zip = await JSZip.loadAsync(bytes);
	const xml = await zip.file('word/document.xml')!.async('string');
	expect(xml).toContain('Delivery confirmation');
	expect(xml).toContain('Delivery is Friday.');
	expect(xml).not.toContain('ARCHER &amp; ROUND / REPORT');
	expect(xml).not.toContain('IN THIS REPORT');
	expect(xml).not.toContain('w:type="page"');
	expect(xml).not.toContain('>Confirmation<');
});
