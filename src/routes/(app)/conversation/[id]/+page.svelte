<script lang="ts">
	import { getContext, onMount } from 'svelte';
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import { toast } from 'svelte-sonner';

	const i18n = getContext('i18n');

	import { mobile, showArchivedChats, showSidebar, user, models } from '$lib/stores';
	import { WEBUI_API_BASE_URL } from '$lib/constants';

	import UserMenu from '$lib/components/layout/Sidebar/UserMenu.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Sidebar from '$lib/components/icons/Sidebar.svelte';

	import AgentEditor from '$lib/components/conversation-agent/AgentEditor.svelte';
	import VoiceChat from '$lib/components/conversation-agent/VoiceChat.svelte';

	import {
		deleteConversationAgentById,
		getConversationAgentById,
		updateConversationAgentById,
		type ConversationAgent
	} from '$lib/apis/conversation-agents';

	let loaded = false;
	let agent: ConversationAgent | null = null;
	let showChat = false;

	$: id = $page.params.id;

	const loadAgent = async () => {
		try {
			agent = await getConversationAgentById(localStorage.token, id);
		} catch (e) {
			console.error(e);
			toast.error(typeof e === 'string' ? e : $i18n.t('Failed to load scenario'));
			await goto('/conversation');
		}
	};

	const handleSave = async (detail: Partial<ConversationAgent>) => {
		if (!agent) return;
		try {
			const updated = await updateConversationAgentById(localStorage.token, agent.id, {
				name: detail.name ?? agent.name,
				description: detail.description ?? agent.description ?? '',
				system_prompt: detail.system_prompt ?? agent.system_prompt ?? '',
				model_id: detail.model_id ?? agent.model_id ?? null,
				voice_config: detail.voice_config ?? agent.voice_config ?? {},
				meta: detail.meta ?? agent.meta ?? {}
			});
			agent = updated;
			toast.success($i18n.t('Scenario saved'));
		} catch (e) {
			console.error(e);
			toast.error(typeof e === 'string' ? e : $i18n.t('Failed to save scenario'));
		}
	};

	const handleDelete = async () => {
		if (!agent) return;
		if (!confirm($i18n.t('Delete this scenario?'))) return;
		try {
			await deleteConversationAgentById(localStorage.token, agent.id);
			await goto('/conversation');
		} catch (e) {
			console.error(e);
			toast.error(typeof e === 'string' ? e : $i18n.t('Failed to delete scenario'));
		}
	};

	onMount(async () => {
		await loadAgent();
		loaded = true;
	});
</script>

{#if loaded && agent}
	<div
		class=" flex flex-col w-full h-screen max-h-[100dvh] transition-width duration-200 ease-in-out {$showSidebar
			? 'md:max-w-[calc(100%-260px)]'
			: ''} max-w-full"
	>
		<nav class="px-2 pt-1.5 backdrop-blur-xl w-full drag-region">
			<div class=" flex items-center">
				{#if $mobile}
					<div class="{$showSidebar ? 'md:hidden' : ''} flex flex-none items-center">
						<Tooltip
							content={$showSidebar ? $i18n.t('Close Sidebar') : $i18n.t('Open Sidebar')}
							interactive={true}
						>
							<button
								id="sidebar-toggle-button"
								class=" cursor-pointer flex rounded-lg hover:bg-gray-100 dark:hover:bg-gray-850 transition"
								on:click={() => {
									showSidebar.set(!$showSidebar);
								}}
							>
								<div class=" self-center p-1.5">
									<Sidebar />
								</div>
							</button>
						</Tooltip>
					</div>
				{/if}

				<div class="ml-2 py-0.5 self-center flex items-center justify-between w-full">
					<div class="flex gap-1 text-sm font-medium py-1">
						<a class="min-w-fit transition opacity-60 hover:opacity-100" href="/conversation">
							{$i18n.t('Conversation Rehearsal')}
						</a>
						<span class="opacity-40">/</span>
						<span class="min-w-fit">{agent.name}</span>
					</div>

					<div class=" self-center flex items-center gap-1">
						{#if $user !== undefined && $user !== null}
							<UserMenu
								className="max-w-[240px]"
								role={$user?.role}
								help={true}
								on:show={(e) => {
									if (e.detail === 'archived-chat') {
										showArchivedChats.set(true);
									}
								}}
							>
								<button
									class="select-none flex rounded-xl p-1.5 w-full hover:bg-gray-50 dark:hover:bg-gray-850 transition"
									aria-label="User Menu"
								>
									<div class=" self-center">
										<img
											src={`${WEBUI_API_BASE_URL}/users/${$user?.id}/profile/image`}
											class="size-6 object-cover rounded-full"
											alt="User profile"
											draggable="false"
										/>
									</div>
								</button>
							</UserMenu>
						{/if}
					</div>
				</div>
			</div>
		</nav>

		<div class="pb-1 flex-1 max-h-full overflow-y-auto @container">
			<div class="mx-auto max-w-3xl px-4 md:px-8 py-6">
				<AgentEditor
					{agent}
					models={$models ?? []}
					onSave={handleSave}
					on:delete={handleDelete}
					on:start={() => (showChat = true)}
				/>
			</div>
		</div>
	</div>

	{#if showChat}
		<VoiceChat {agent} on:close={() => (showChat = false)} />
	{/if}
{/if}
