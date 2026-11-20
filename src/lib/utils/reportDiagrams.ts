import { marked } from 'marked';

export type ReportDiagram = { dataUrl: string; width: number; height: number };
export type ReportDiagrams = Record<string, ReportDiagram>;

export const reportDiagramSources = (markdown: string): string[] => {
	const sources: string[] = [];
	marked.walkTokens(marked.lexer(markdown), (token) => {
		if (token.type === 'code' && token.lang?.trim().toLowerCase() === 'mermaid')
			sources.push(token.text);
	});
	return [...new Set(sources)];
};

/** Replace fenced tokens, including repeated figures, without changing prose. */
export const replaceReportDiagram = (
	markdown: string,
	original: string,
	replacement: string
): string => {
	const blocks: string[] = [];
	marked.walkTokens(marked.lexer(markdown), (token) => {
		if (
			token.type === 'code' &&
			token.lang?.trim().toLowerCase() === 'mermaid' &&
			token.text === original
		)
			blocks.push(token.raw);
	});
	for (const raw of blocks)
		markdown = markdown.split(raw).join('~~~mermaid\n' + replacement + '\n~~~\n\n');
	return markdown;
};

/** Per-diagram configuration avoids changing the chat's shared Mermaid theme. */
export const renderReportDiagram = async (source: string): Promise<ReportDiagram> => {
	if (
		!/^(?:flowchart|graph|sequenceDiagram|classDiagram|erDiagram|stateDiagram-v2|gantt|pie|timeline)\b/.test(
			source.trim()
		)
	)
		throw new Error('Use a supported, self-contained Mermaid diagram');
	if (/%%\{|^---|^\s*click\s|https?:|<\/?(?:script|iframe|img)|@\{/im.test(source))
		throw new Error('Diagram configuration, links and external resources are not supported');
	const [{ default: mermaid }, { renderMermaidDiagram }] = await Promise.all([
		import('mermaid'),
		import('$lib/utils')
	]);
	const config = `---\nconfig:\n  theme: base\n  htmlLabels: false\n  fontFamily: Arial, sans-serif\n  themeVariables:\n    primaryColor: '#edf5ff'\n    primaryTextColor: '#161616'\n    primaryBorderColor: '#0f62fe'\n    lineColor: '#525252'\n    secondaryColor: '#f4f4f4'\n    tertiaryColor: '#ffffff'\n    fontSize: '16px'\n  flowchart:\n    htmlLabels: false\n    useMaxWidth: false\n    padding: 18\n---\n`;
	const svg = await renderMermaidDiagram(mermaid, config + source);
	const parsed = new DOMParser().parseFromString(svg, 'image/svg+xml');
	const root = parsed.documentElement;
	const viewBox = root
		.getAttribute('viewBox')
		?.split(/[\s,]+/)
		.map(Number);
	const width = viewBox?.[2] || parseFloat(root.getAttribute('width') || '800');
	const height = viewBox?.[3] || parseFloat(root.getAttribute('height') || '500');
	if (!(width > 0 && height > 0)) throw new Error('Invalid diagram dimensions');
	// A diagram must remain legible in an A4 body; request simplification instead of shrinking indefinitely.
	if (width > 1500 || height > 1600 || width / height > 4.5 || height / width > 3)
		throw new Error('Diagram is too dense for a report page; split it into smaller diagrams');
	root.setAttribute('width', String(width));
	root.setAttribute('height', String(height));
	const url = URL.createObjectURL(
		new Blob([new XMLSerializer().serializeToString(root)], { type: 'image/svg+xml' })
	);
	try {
		const image = new Image();
		await new Promise<void>((resolve, reject) => {
			image.onload = () => resolve();
			image.onerror = () => reject(new Error('Unable to rasterize report diagram'));
			image.src = url;
		});
		const scale = Math.min(3, 3000 / Math.max(width, height));
		const canvas = document.createElement('canvas');
		canvas.width = Math.ceil(width * scale);
		canvas.height = Math.ceil(height * scale);
		const context = canvas.getContext('2d');
		if (!context) throw new Error('Unable to create diagram canvas');
		context.fillStyle = '#ffffff';
		context.fillRect(0, 0, canvas.width, canvas.height);
		context.drawImage(image, 0, 0, canvas.width, canvas.height);
		return { dataUrl: canvas.toDataURL('image/png'), width, height };
	} finally {
		URL.revokeObjectURL(url);
	}
};

export const renderReportDiagrams = async (markdown: string): Promise<ReportDiagrams> => {
	const diagrams: ReportDiagrams = {};
	// Mermaid owns shared rendering state; serialize render operations.
	for (const source of reportDiagramSources(markdown))
		diagrams[source] = await renderReportDiagram(source);
	return diagrams;
};
