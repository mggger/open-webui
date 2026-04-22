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

	const handleDelete = async (id: string, e: MouseEvent) => {
		e.preventDefault();
		e.stopPropagation();
		if (!confirm($i18n.t('Delete this agent?'))) return;
		try {
			await deleteConversationAgentById(localStorage.token, id);
			dispatch('refresh');
		} catch (err) {
			console.error(err);
			toast.error(typeof err === 'string' ? err : $i18n.t('Failed to delete agent'));
		}
	};
</script>

{#if agents.length === 0}
	<div
		class="flex flex-col items-center justify-center py-16 text-center text-gray-500 dark:text-gray-400"
	>
		<div class="text-lg mb-1">{$i18n.t('No agents yet')}</div>
		<div class="text-sm">
			{$i18n.t('Create your first conversation agent to get started.')}
		</div>
	</div>
{:else}
	<div class="grid grid-cols-1 md:grid-cols-2 gap-3">
		{#each agents as agent (agent.id)}
			<a
				class="group block rounded-xl border border-gray-200 dark:border-gray-800 p-4 hover:bg-gray-50 dark:hover:bg-gray-900 transition"
				href="/conversation/{agent.id}"
			>
				<div class="flex items-start justify-between gap-2">
					<div class="min-w-0 flex-1">
						<div class="text-sm font-semibold truncate">{agent.name}</div>
						{#if agent.description}
							<div class="text-xs text-gray-500 dark:text-gray-400 mt-1 line-clamp-2">
								{agent.description}
							</div>
						{/if}
					</div>
					<button
						type="button"
						class="opacity-0 group-hover:opacity-100 text-xs text-red-500 hover:text-red-600 transition"
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
