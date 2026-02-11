<script lang="ts">
	import { createEventDispatcher, getContext, onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { settings, tools } from '$lib/stores';
	import { getTools } from '$lib/apis/tools';

	const dispatch = createEventDispatcher();
	const i18n = getContext('i18n');

	export let saveSettings: (updated: Record<string, unknown>) => Promise<void> | void;

	let command = '';
	let systemPrompt = '';
	let userInput = '';
	let mode: 'tools' | 'web_search' | 'deep_research' = 'tools';
	let toolIds: string[] = [];

	const toggleTool = (toolId: string) => {
		if (toolIds.includes(toolId)) {
			toolIds = toolIds.filter((id) => id !== toolId);
			return;
		}
		toolIds = [...toolIds, toolId];
	};

	onMount(async () => {
		const config = $settings?.commandAutomation ?? null;

		command = (config?.command ?? '').trim();
		systemPrompt = config?.systemPrompt ?? '';
		userInput = config?.userInput ?? '';
		mode = (config?.mode ?? 'tools') as 'tools' | 'web_search' | 'deep_research';
		toolIds = Array.isArray(config?.toolIds) ? config.toolIds : [];

		if (!$tools) {
			tools.set(await getTools(localStorage.token));
		}
	});
</script>

<form
	id="tab-command-automation"
	class="flex flex-col h-full justify-between space-y-3 text-sm"
	on:submit|preventDefault={async () => {
		if (command && !command.trim().startsWith('!')) {
			toast.error($i18n.t('Command must start with "!"'));
			return;
		}
		if (command.trim() && !userInput.trim()) {
			toast.error($i18n.t('User Input is required when Command is set.'));
			return;
		}

		await saveSettings({
			commandAutomation: command.trim()
				? {
						command: command.trim(),
						systemPrompt: systemPrompt.trim(),
						userInput: userInput.trim(),
						mode,
						toolIds: mode === 'tools' ? toolIds : []
					}
				: null
		});
		dispatch('save');
	}}
>
	<div class="py-1 overflow-y-scroll max-h-[28rem] md:max-h-full space-y-4">
		<div class="space-y-1.5">
			<div class="text-sm font-medium">{$i18n.t('Command')}</div>
			<input
				class="w-full py-2 px-2 text-xs rounded-lg bg-transparent border border-gray-200 dark:border-gray-800 outline-hidden"
				bind:value={command}
				placeholder="!do_search"
				autocomplete="off"
			/>
			<div class="text-xs text-gray-600 dark:text-gray-400">
				{$i18n.t('When input starts with this command, prompt and tools will be auto-applied.')}
			</div>
		</div>

		<div class="space-y-1.5">
			<div class="text-sm font-medium">{$i18n.t('User Input')}</div>
			<textarea
				class="w-full py-2 px-2 text-xs rounded-lg bg-transparent border border-gray-200 dark:border-gray-800 outline-hidden resize-y min-h-24"
				bind:value={userInput}
				placeholder={$i18n.t('Replacement user input')}
			></textarea>
		</div>

		<div class="space-y-1.5">
			<div class="text-sm font-medium">{$i18n.t('System Prompt')}</div>
			<textarea
				class="w-full py-2 px-2 text-xs rounded-lg bg-transparent border border-gray-200 dark:border-gray-800 outline-hidden resize-y min-h-24"
				bind:value={systemPrompt}
				placeholder={$i18n.t('Extra system prompt to append')}
			></textarea>
		</div>

		<div class="space-y-1.5">
			<div class="text-sm font-medium">{$i18n.t('Mode')}</div>
			<select
				class="w-full py-2 px-2 text-xs rounded-lg bg-transparent border border-gray-200 dark:border-gray-800 outline-hidden"
				bind:value={mode}
			>
				<option value="tools">{$i18n.t('Tools')}</option>
				<option value="web_search">{$i18n.t('Web Search')}</option>
				<option value="deep_research">{$i18n.t('Deep Research')}</option>
			</select>
		</div>

		{#if mode === 'tools'}
			<div class="space-y-1.5">
				<div class="text-sm font-medium">{$i18n.t('Select Tools')}</div>
				<div class="text-xs text-gray-600 dark:text-gray-400">
					{$i18n.t('Only selected tools will be enabled when the command is used.')}
				</div>
				{#if ($tools ?? []).length === 0}
					<div class="text-xs text-gray-500">{$i18n.t('No tools available')}</div>
				{:else}
					<div class="space-y-1.5 max-h-44 overflow-y-auto border border-gray-100 dark:border-gray-850 rounded-lg p-2">
						{#each $tools as tool}
							<button
								type="button"
								class="w-full text-left px-2 py-1.5 rounded-md text-xs transition border {toolIds.includes(tool.id)
									? 'border-gray-500 dark:border-gray-400 bg-gray-100/70 dark:bg-gray-850/70'
									: 'border-gray-200 dark:border-gray-800 hover:bg-gray-50 dark:hover:bg-gray-900'}"
								on:click={() => toggleTool(tool.id)}
							>
								<div class="font-medium">{tool?.name ?? tool.id}</div>
								<div class="text-gray-500 mt-0.5">{tool.id}</div>
							</button>
						{/each}
					</div>
				{/if}
			</div>
		{/if}
	</div>

	<div class="flex justify-end text-sm font-medium">
		<button
			class="px-3.5 py-1.5 text-sm font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full"
			type="submit"
		>
			{$i18n.t('Save')}
		</button>
	</div>
</form>
