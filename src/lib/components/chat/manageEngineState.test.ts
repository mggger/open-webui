import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { parse } from 'svelte/compiler';
import ts from 'typescript';
import { describe, expect, it } from 'vitest';

const source = (file: string) => readFileSync(`src/lib/components/chat/${file}.svelte`, 'utf8');
const walk = (node: any, visit: (node: any) => void) => {
	if (!node || typeof node !== 'object') return;
	visit(node);
	for (const value of Object.values(node)) {
		if (Array.isArray(value)) value.forEach((child) => walk(child, visit));
		else if (value && typeof value === 'object') walk(value, visit);
	}
};

// Exercise the production state handlers without booting sockets and the whole chat page.
const createState = () => {
	const chat = source('Chat');
	const ast = parse(chat, { modern: true });
	const names = ['restoreChatInput', 'resetInput', 'getFeatures'];
	const handlers: string[] = [];
	walk(ast.instance?.content, (node) => {
		if (node.type === 'VariableDeclarator' && names.includes(node.id?.name)) {
			handlers.push(`const ${chat.slice(node.start, node.end)};`);
		}
	});
	expect(handlers).toHaveLength(names.length);
	const script = `
		let manageEngineEnabled = false;
		let prompt, files, selectedToolIds, selectedSkillIds, selectedFilterIds, pendingOAuthTools;
		let webSearchEnabled, deepSearchEnabled, fileSearchEnabled, fileSearchDirectory;
		let imageGenerationEnabled, codeInterpreterEnabled;
		const $temporaryChatEnabled = false, messageInput = { setText() {} };
		const tick = async () => {}, setDefaults = async () => {}, selectedModelIds = ['model'];
		const $config = {features: {}}, $settings = {}, $user = {role:'admin'};
		const $showCallOverlay = false, bridge = null, webSearchActive = false;
		${handlers.join('\n')}
		({ restoreChatInput, resetInput, getFeatures, set: value => { manageEngineEnabled = value; } });
	`;
	return runInNewContext(
		ts.transpileModule(script, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText
	);
};

describe('Manage Engine conversation state', () => {
	it('binds the same state through the home wrapper and every follow-up input', () => {
		for (const file of ['Chat', 'Placeholder', 'MessageInput']) {
			let count = 0;
			walk(parse(source(file), { modern: true }).fragment, (node) => {
				if (
					node.type !== 'Component' ||
					!['MessageInput', 'Placeholder', 'IntegrationsMenu'].includes(node.name)
				)
					return;
				count++;
				expect(
					node.attributes.some(
						(attribute: any) =>
							attribute.type === 'BindDirective' && attribute.name === 'manageEngineEnabled'
					)
				).toBe(true);
			});
			expect(count).toBeGreaterThan(0);
		}
		const placeholder = parse(source('Placeholder'), { modern: true });
		expect(
			placeholder.instance?.content.body.some(
				(node: any) =>
					node.type === 'ExportNamedDeclaration' &&
					node.declaration?.declarations?.some(
						(declaration: any) => declaration.id.name === 'manageEngineEnabled'
					)
			)
		).toBe(true);
	});

	it('keeps the enabled draft through model defaults and successive request payloads', async () => {
		const state = createState();
		await state.restoreChatInput(
			JSON.stringify({ prompt: 'wang剩下的tickets', manageEngineEnabled: true })
		);
		await state.resetInput();
		expect(state.getFeatures().manage_engine).toBe(true);
		// Follow-up requests use the same conversation choice, until explicitly disabled.
		expect(state.getFeatures().manage_engine).toBe(true);
		state.set(false);
		expect(state.getFeatures().manage_engine).toBe(false);
	});

	it('restores a disabled or older chat draft without inheriting another chat state', async () => {
		const state = createState();
		state.set(true);
		await state.restoreChatInput(JSON.stringify({ prompt: 'normal chat' }));
		await state.resetInput();
		expect(state.getFeatures().manage_engine).toBe(false);
	});
});
