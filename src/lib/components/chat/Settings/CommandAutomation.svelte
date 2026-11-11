<script lang="ts">
	import { createEventDispatcher, getContext, onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { settings, tools } from '$lib/stores';
	import { getTools } from '$lib/apis/tools';

	const dispatch = createEventDispatcher();
	const i18n = getContext('i18n');

	export let saveSettings: (updated: Record<string, unknown>) => Promise<void> | void;

	type CommandAutomationEntry = {
		command: string;
		systemPrompt: string;
		userInput: string;
		mode: 'tools' | 'web_search' | 'deep_research';
		toolIds: string[];
	};

	const createEmptyEntry = (): CommandAutomationEntry => ({
		command: '',
		systemPrompt: '',
		userInput: '',
		mode: 'tools',
		toolIds: []
	});

	const normalizeEntry = (entry: unknown): CommandAutomationEntry => {
		const item: Record<string, unknown> =
			typeof entry === 'object' && entry !== null ? (entry as Record<string, unknown>) : {};
		return {
			command: typeof item?.command === 'string' ? item.command.trim() : '',
			systemPrompt: typeof item?.systemPrompt === 'string' ? item.systemPrompt : '',
			userInput: typeof item?.userInput === 'string' ? item.userInput : '',
			mode: item?.mode === 'web_search' || item?.mode === 'deep_research' ? item.mode : 'tools',
			toolIds: Array.isArray(item?.toolIds)
				? item.toolIds.filter((toolId) => typeof toolId === 'string' && toolId.trim())
				: []
		};
	};

	const normalizeConfig = (config: unknown): CommandAutomationEntry[] => {
		if (Array.isArray(config)) {
			return config.map((entry) => normalizeEntry(entry));
		}
		if (typeof config === 'object' && config !== null) {
			return [normalizeEntry(config)];
		}
		return [];
	};

	let entries: CommandAutomationEntry[] = [createEmptyEntry()];

	const updateEntry = (index: number, updated: Partial<CommandAutomationEntry>) => {
		entries = entries.map((entry, i) => (i === index ? { ...entry, ...updated } : entry));
	};

	const addEntry = () => {
		entries = [...entries, createEmptyEntry()];
	};

	const removeEntry = (index: number) => {
		const filtered = entries.filter((_, i) => i !== index);
		entries = filtered.length > 0 ? filtered : [createEmptyEntry()];
	};

	const toggleTool = (index: number, toolId: string) => {
		const entry = entries[index];
		if (!entry) return;
		const hasTool = entry.toolIds.includes(toolId);
		updateEntry(index, {
			toolIds: hasTool ? entry.toolIds.filter((id) => id !== toolId) : [...entry.toolIds, toolId]
		});
	};

	onMount(async () => {
		const config = $settings?.commandAutomation ?? null;
		const normalizedEntries = normalizeConfig(config);
		entries = normalizedEntries.length > 0 ? normalizedEntries : [createEmptyEntry()];

		if (!$tools) {
			tools.set(await getTools(localStorage.token));
		}
	});
</script>

<form
	id="tab-command-automation"
	class="flex flex-col h-full justify-between space-y-3 text-sm"
	on:submit|preventDefault={async () => {
		const sanitizedEntries = entries
			.map((entry) => ({
				command: entry.command.trim(),
				systemPrompt: entry.systemPrompt.trim(),
				userInput: entry.userInput.trim(),
				mode: entry.mode,
				toolIds: entry.mode === 'tools' ? entry.toolIds : []
			}))
			.filter((entry) => entry.command);

		for (const entry of sanitizedEntries) {
			if (!entry.command.startsWith('!')) {
				toast.error($i18n.t('Command must start with "!"'));
				return;
			}
			if (!entry.userInput) {
				toast.error($i18n.t('User Input is required when Command is set.'));
				return;
			}
		}

		await saveSettings({
			commandAutomation:
				sanitizedEntries.length === 0
					? null
					: sanitizedEntries.length === 1
						? sanitizedEntries[0]
						: sanitizedEntries
		});
		dispatch('save');
	}}
>
	<div class="py-1 overflow-y-scroll max-h-[28rem] md:max-h-full space-y-4">
		<div class="flex justify-start">
			<button
				type="button"
				class="px-3.5 py-1.5 text-sm font-medium hover:bg-black/5 dark:hover:bg-white/5 outline outline-1 outline-gray-300 dark:outline-gray-800 rounded-full"
				on:click={addEntry}
			>
				{$i18n.t('Add')} {$i18n.t('Command')}
			</button>
		</div>

		{#each entries as entry, index}
			<div class="space-y-4 rounded-xl border border-gray-100 dark:border-gray-850/70 p-3">
				<div class="flex items-center justify-between">
					<div class="text-sm font-medium">{$i18n.t('Command')} #{index + 1}</div>
					<button
						type="button"
						class="text-xs text-gray-500 hover:text-red-500"
						on:click={() => removeEntry(index)}
					>
						{$i18n.t('Remove')}
					</button>
				</div>

				<div class="space-y-1.5">
					<div class="text-sm font-medium">{$i18n.t('Command')}</div>
					<input
						class="w-full py-2 px-2 text-xs rounded-lg bg-transparent border border-gray-200 dark:border-gray-800 outline-hidden"
						value={entry.command}
						on:input={(e) => {
							updateEntry(index, { command: (e.target as HTMLInputElement).value });
						}}
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
						value={entry.userInput}
						on:input={(e) => {
							updateEntry(index, { userInput: (e.target as HTMLTextAreaElement).value });
						}}
						placeholder={$i18n.t('Replacement user input')}
					></textarea>
				</div>

				<div class="space-y-1.5">
					<div class="text-sm font-medium">{$i18n.t('System Prompt')}</div>
					<textarea
						class="w-full py-2 px-2 text-xs rounded-lg bg-transparent border border-gray-200 dark:border-gray-800 outline-hidden resize-y min-h-24"
						value={entry.systemPrompt}
						on:input={(e) => {
							updateEntry(index, { systemPrompt: (e.target as HTMLTextAreaElement).value });
						}}
						placeholder={$i18n.t('Extra system prompt to append')}
					></textarea>
				</div>

				<div class="space-y-1.5">
					<div class="text-sm font-medium">{$i18n.t('Mode')}</div>
					<select
						class="w-full py-2 px-2 text-xs rounded-lg bg-transparent border border-gray-200 dark:border-gray-800 outline-hidden"
						value={entry.mode}
						on:change={(e) => {
							updateEntry(index, {
								mode: (e.target as HTMLSelectElement).value as
									| 'tools'
									| 'web_search'
									| 'deep_research'
							});
						}}
					>
						<option value="tools">{$i18n.t('Tools')}</option>
						<option value="web_search">{$i18n.t('Web Search')}</option>
						<option value="deep_research">{$i18n.t('Deep Research')}</option>
					</select>
				</div>

				{#if entry.mode === 'tools'}
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
										class="w-full text-left px-2 py-1.5 rounded-md text-xs transition border {entry.toolIds.includes(
											tool.id
										)
											? 'border-gray-500 dark:border-gray-400 bg-gray-100/70 dark:bg-gray-850/70'
											: 'border-gray-200 dark:border-gray-800 hover:bg-gray-50 dark:hover:bg-gray-900'}"
										on:click={() => toggleTool(index, tool.id)}
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
		{/each}
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
