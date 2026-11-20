// Builds static/assets/docx/archer-response-template.docx, the template used by the
// per-response "Word document (.docx)" export (src/lib/utils/exportDocx.ts).
//
//   node scripts/build-docx-template.mjs
//
// Same layout as the per-response PDF export: the Archer & Round letterhead, footer and
// logo (copied verbatim from docs/archer_template.docx) with the response content in
// between, patched into the {{CONTENT}} placeholder at export time. Page 1 carries the
// letterhead at the top of the body; later pages repeat it via the page header.
// The output can also be opened and restyled in Word/WPS as long as the placeholder
// and the style names (Heading 1-6, Archer Quote, Archer Code) are kept.
import fs from 'node:fs';
import JSZip from 'jszip';
import { BorderStyle, Document, Packer, Paragraph, ShadingType, TextRun } from 'docx';

const SOURCE_TEMPLATE = 'docs/archer_template.docx';
const OUTPUT = 'static/assets/docx/archer-response-template.docx';

// Body styles mirror the per-response PDF export (ResponseMessage.svelte, .pdf-export-snapshot):
// Helvetica/Arial 14px black text, plain bold headings, grey quote rule, light code panels.
const FONT = 'Arial';
const BLACK = '000000';
const QUOTE_RULE = 'CCCCCC';
const CODE_FILL = 'F6F8FA';

const heading = (size) => ({
	run: { font: FONT, size, bold: true, color: BLACK },
	paragraph: { keepNext: true, spacing: { before: 240, after: 120, line: 300 } }
});

const doc = new Document({
	creator: 'Archer & Round',
	title: 'Archer & Round',
	styles: {
		default: {
			document: {
				run: { font: FONT, size: 21, color: BLACK },
				paragraph: { spacing: { before: 120, after: 120, line: 360 } }
			},
			heading1: heading(33),
			heading2: heading(28),
			heading3: heading(26),
			heading4: heading(22),
			heading5: heading(22),
			heading6: heading(22),
			hyperlink: { run: { color: '1A56DB', underline: {} } }
		},
		paragraphStyles: [
			{
				id: 'ArcherQuote',
				name: 'Archer Quote',
				basedOn: 'Normal',
				next: 'Normal',
				run: { color: '333333' },
				paragraph: {
					indent: { left: 200 },
					border: { left: { style: BorderStyle.SINGLE, size: 18, color: QUOTE_RULE, space: 8 } }
				}
			},
			{
				id: 'ArcherCode',
				name: 'Archer Code',
				basedOn: 'Normal',
				next: 'Normal',
				run: { font: 'Consolas', size: 18, color: BLACK },
				paragraph: {
					spacing: { before: 120, after: 120, line: 276 },
					shading: { type: ShadingType.CLEAR, fill: CODE_FILL, color: 'auto' },
					border: {
						top: { style: BorderStyle.SINGLE, size: 4, color: CODE_FILL, space: 6 },
						bottom: { style: BorderStyle.SINGLE, size: 4, color: CODE_FILL, space: 6 },
						left: { style: BorderStyle.SINGLE, size: 4, color: CODE_FILL, space: 6 },
						right: { style: BorderStyle.SINGLE, size: 4, color: CODE_FILL, space: 6 }
					}
				}
			}
		]
	},
	sections: [
		{
			properties: { page: { size: { width: 11906, height: 16838 } } },
			children: [new Paragraph({ children: [new TextRun('{{CONTENT}}')] })]
		}
	]
});

const zip = await JSZip.loadAsync(await Packer.toBuffer(doc));
const letterhead = await JSZip.loadAsync(fs.readFileSync(SOURCE_TEMPLATE));

for (const part of [
	'word/header1.xml',
	'word/footer1.xml',
	'word/_rels/header1.xml.rels',
	'word/media/logo.png'
]) {
	zip.file(part, await letterhead.file(part).async('uint8array'));
}

// Some letterhead paragraphs only set w:after, so they would inherit the body's line
// spacing and space-before from the styles above; pin them to compact spacing.
for (const part of ['word/header1.xml', 'word/footer1.xml']) {
	const xml = (await zip.file(part).async('string')).replace(
		/<w:spacing w:after="(\d+)"\/>/g,
		'<w:spacing w:before="0" w:after="$1" w:line="252" w:lineRule="auto"/>'
	);
	zip.file(part, xml);
}

// An empty header for the first page: page 1 carries the letterhead in the body instead.
const W_NS = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main';
zip.file(
	'word/header2.xml',
	`<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:hdr xmlns:w="${W_NS}"><w:p><w:pPr><w:spacing w:before="0" w:after="0"/></w:pPr></w:p></w:hdr>`
);

const relsPath = 'word/_rels/document.xml.rels';
let rels = await zip.file(relsPath).async('string');
rels = rels.replace(
	'</Relationships>',
	'<Relationship Id="rIdArcherHeader" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/header" Target="header1.xml"/>' +
		'<Relationship Id="rIdArcherHeaderFirst" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/header" Target="header2.xml"/>' +
		'<Relationship Id="rIdArcherFooter" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer" Target="footer1.xml"/>' +
		'<Relationship Id="rIdArcherLogo" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/logo.png"/>' +
		'</Relationships>'
);
zip.file(relsPath, rels);

let contentTypes = await zip.file('[Content_Types].xml').async('string');
contentTypes = contentTypes.replace(
	'</Types>',
	'<Override PartName="/word/header1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"/>' +
		'<Override PartName="/word/header2.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"/>' +
		'<Override PartName="/word/footer1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/>' +
		'</Types>'
);
if (!contentTypes.includes('Extension="png"')) {
	contentTypes = contentTypes.replace(
		'</Types>',
		'<Default Extension="png" ContentType="image/png"/></Types>'
	);
}
zip.file('[Content_Types].xml', contentTypes);

// Like the PDF export, page 1 starts with the letterhead as part of the body, so it shows
// even in viewers that don't render page headers (e.g. macOS Quick Look, some previews).
// Later pages repeat it through the regular page header.
const headerXml = await zip.file('word/header1.xml').async('string');
const letterheadBlock = headerXml
	.replace(/^[\s\S]*?<w:hdr[^>]*>/, '')
	.replace(/<\/w:hdr>\s*$/, '')
	.replace(/r:embed="rId1"/g, 'r:embed="rIdArcherLogo"')
	.replace(/docPr id="(\d+)"/g, 'docPr id="90$1"')
	.replace(
		/<w:drawing>/g,
		'<w:drawing xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">'
	);
if (!letterheadBlock.includes('rIdArcherLogo')) {
	throw new Error('Failed to copy the letterhead into the body');
}

// Wire the letterhead into the section and use docs/archer_template.docx's page margins.
let documentXml = await zip.file('word/document.xml').async('string');
documentXml = documentXml
	.replace('<w:body>', `<w:body>${letterheadBlock}`)
	.replace(
		'<w:sectPr>',
		'<w:sectPr><w:headerReference w:type="default" r:id="rIdArcherHeader"/><w:headerReference w:type="first" r:id="rIdArcherHeaderFirst"/>' +
			'<w:footerReference w:type="default" r:id="rIdArcherFooter"/><w:footerReference w:type="first" r:id="rIdArcherFooter"/>'
	)
	.replace(
		/<w:pgMar [^>]*\/>/,
		'<w:pgMar w:top="1224" w:right="1224" w:bottom="1224" w:left="1224" w:header="720" w:footer="720" w:gutter="0"/>'
	);
// w:titlePg (different first page) must precede w:docGrid inside w:sectPr.
documentXml = documentXml.includes('<w:docGrid')
	? documentXml.replace('<w:docGrid', '<w:titlePg/><w:docGrid')
	: documentXml.replace('</w:sectPr>', '<w:titlePg/></w:sectPr>');
if (
	!documentXml.includes('rIdArcherHeader') ||
	!documentXml.includes('w:top="1224"') ||
	!documentXml.includes('<w:titlePg/>')
) {
	throw new Error('Failed to wire the letterhead into word/document.xml');
}
zip.file('word/document.xml', documentXml);

fs.writeFileSync(OUTPUT, await zip.generateAsync({ type: 'nodebuffer', compression: 'DEFLATE' }));
console.log(`wrote ${OUTPUT}`);
