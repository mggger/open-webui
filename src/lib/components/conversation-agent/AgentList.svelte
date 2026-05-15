<script lang="ts">
	import { createEventDispatcher, getContext } from 'svelte';
	import { toast } from 'svelte-sonner';

	import {
		deleteConversationAgentById,
		type ConversationAgent
	} from '$lib/apis/conversation-agents';

	const i18n = getContext('i18n');
	const dispatch = createEventDispatcher();

	export let agents: ConversationAgent[] = [];

	type Scenario = {
		counterpart_name?: string;
		counterpart_role?: string;
		counterpart_company?: string;
		goal?: string;
	};

	const scenarioOf = (a: ConversationAgent): Scenario => {
		const m = (a.meta ?? {}) as Record<string, any>;
		return (m.scenario ?? {}) as Scenario;
	};

	const initialsOf = (a: ConversationAgent): string => {
		const sc = scenarioOf(a);
		const base = sc.counterpart_name || a.name || '?';
		const parts = base
			.replace(/[^\p{L}\p{N}\s]/gu, '')
			.split(/\s+/)
			.filter(Boolean)
			.slice(0, 2);
		return (
			parts.map((s: string) => s[0]?.toUpperCase() ?? '').join('') || '?'
		);
	};

	const handleDelete = async (id: string, e: MouseEvent) => {
		e.preventDefault();
		e.stopPropagation();
		if (!confirm($i18n.t('Delete this scenario?'))) return;
		try {
			await deleteConversationAgentById(localStorage.token, id);
			dispatch('refresh');
		} catch (err) {
			console.error(err);
			toast.error(typeof err === 'string' ? err : $i18n.t('Failed to delete scenario'));
		}
	};
</script>

{#if agents.length === 0}
	<div
		class="flex flex-col items-center justify-center py-16 text-center text-gray-500 dark:text-gray-400"
	>
		<div class="text-lg mb-1">{$i18n.t('No scenarios yet')}</div>
		<div class="text-sm max-w-md">
			{$i18n.t(
				'Rehearse a real conversation before it happens. Set up the counterpart, paste their background, and practice out loud.'
			)}
		</div>
	</div>
{:else}
	<div class="grid grid-cols-1 md:grid-cols-2 gap-3">
		{#each agents as agent (agent.id)}
			{@const sc = scenarioOf(agent)}
			{@const initials = initialsOf(agent)}
			<a
				class="group block rounded-xl border border-gray-200 dark:border-gray-800 p-4 hover:bg-gray-50 dark:hover:bg-gray-900 transition"
				href="/conversation/{agent.id}"
			>
				<div class="flex items-start gap-3">
					<div
						class="size-9 rounded-full bg-gray-100 dark:bg-gray-800 flex items-center justify-center text-xs font-semibold shrink-0"
						aria-hidden="true"
					>
						{initials}
					</div>
					<div class="min-w-0 flex-1">
						<div class="text-sm font-semibold truncate">{agent.name}</div>
						{#if agent.description}
							<div class="text-xs text-gray-500 dark:text-gray-400 mt-0.5 line-clamp-2">
								{agent.description}
							</div>
						{:else if sc.goal}
							<div class="text-xs text-gray-500 dark:text-gray-400 mt-0.5 line-clamp-2">
								{sc.goal}
							</div>
						{/if}
					</div>
					<button
						type="button"
						class="opacity-0 group-hover:opacity-100 text-xs text-red-500 hover:text-red-600 transition shrink-0"
						on:click={(e) => handleDelete(agent.id, e)}
						aria-label={$i18n.t('Delete')}
					>
						{$i18n.t('Delete')}
					</button>
				</div>
			</a>
		{/each}
	</div>
{/if}
