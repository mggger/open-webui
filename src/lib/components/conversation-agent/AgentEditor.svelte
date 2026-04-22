<script lang="ts">
	import { createEventDispatcher, getContext } from 'svelte';
	import type { ConversationAgent } from '$lib/apis/conversation-agents';

	const i18n = getContext('i18n');
	const dispatch = createEventDispatcher();

	export let agent: ConversationAgent;
	export let models: any[] = [];

	let name = agent.name ?? '';
	let description = agent.description ?? '';
	let systemPrompt = agent.system_prompt ?? '';
	let modelId = agent.model_id ?? '';

	$: dirty =
		name !== (agent.name ?? '') ||
		description !== (agent.description ?? '') ||
		systemPrompt !== (agent.system_prompt ?? '') ||
		modelId !== (agent.model_id ?? '');

	const save = () => {
		dispatch('save', {
			name,
			description,
			system_prompt: systemPrompt,
			model_id: modelId || null
		});
	};

	const handleStart = () => {
		if (dirty) {
			save();
		}
		if (!modelId) {
			alert($i18n.t('Please select a model before starting.'));
			return;
		}
		dispatch('start');
	};
</script>

<div class="flex flex-col gap-5">
	<div>
		<label class="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1" for="agent-name"
			>{$i18n.t('Name')}</label
		>
		<input
			id="agent-name"
			class="w-full px-3 py-2 rounded-lg border border-gray-200 dark:border-gray-800 bg-transparent text-sm focus:outline-none focus:border-gray-400 dark:focus:border-gray-600"
			bind:value={name}
			placeholder={$i18n.t('Agent name')}
		/>
	</div>

	<div>
		<label
			class="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1"
			for="agent-description">{$i18n.t('Description')}</label
		>
		<input
			id="agent-description"
			class="w-full px-3 py-2 rounded-lg border border-gray-200 dark:border-gray-800 bg-transparent text-sm focus:outline-none focus:border-gray-400 dark:focus:border-gray-600"
			bind:value={description}
			placeholder={$i18n.t('Optional short description')}
		/>
	</div>

	<div>
		<label class="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1" for="agent-model"
			>{$i18n.t('Model')}</label
		>
		<select
			id="agent-model"
			class="w-full px-3 py-2 rounded-lg border border-gray-200 dark:border-gray-800 bg-transparent text-sm focus:outline-none focus:border-gray-400 dark:focus:border-gray-600"
			bind:value={modelId}
		>
			<option value="">{$i18n.t('Select a model')}</option>
			{#each models as m}
				<option value={m.id}>{m.name ?? m.id}</option>
			{/each}
		</select>
	</div>

	<div>
		<label
			class="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1"
			for="agent-system-prompt">{$i18n.t('Background knowledge / System prompt')}</label
		>
		<textarea
			id="agent-system-prompt"
			class="w-full min-h-[180px] px-3 py-2 rounded-lg border border-gray-200 dark:border-gray-800 bg-transparent text-sm font-mono focus:outline-none focus:border-gray-400 dark:focus:border-gray-600 resize-y"
			bind:value={systemPrompt}
			placeholder={$i18n.t(
				'Describe who this agent is, what it knows, and how it should respond...'
			)}
		></textarea>
	</div>

	<div class="flex items-center justify-between pt-2">
		<button
			type="button"
			class="text-sm text-red-500 hover:text-red-600 transition"
			on:click={() => dispatch('delete')}
		>
			{$i18n.t('Delete')}
		</button>

		<div class="flex items-center gap-2">
			<button
				type="button"
				class="px-3 py-1.5 rounded-lg text-sm border border-gray-200 dark:border-gray-800 hover:bg-gray-50 dark:hover:bg-gray-900 transition disabled:opacity-50"
				disabled={!dirty}
				on:click={save}
			>
				{$i18n.t('Save')}
			</button>
			<button
				type="button"
				class="px-4 py-1.5 rounded-lg text-sm bg-gray-900 text-white hover:bg-gray-800 dark:bg-gray-100 dark:text-gray-900 dark:hover:bg-white transition"
				on:click={handleStart}
			>
				{$i18n.t('Start conversation')}
			</button>
		</div>
	</div>
</div>
