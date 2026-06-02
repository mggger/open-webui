<script lang="ts">
	import { getContext, onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { toast } from 'svelte-sonner';

	const i18n = getContext('i18n');

	import { mobile, showArchivedChats, showSidebar, user } from '$lib/stores';
	import { WEBUI_API_BASE_URL } from '$lib/constants';

	import UserMenu from '$lib/components/layout/Sidebar/UserMenu.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Sidebar from '$lib/components/icons/Sidebar.svelte';

	import AgentList from '$lib/components/conversation-agent/AgentList.svelte';
	import {
		createConversationAgent,
		getConversationAgents,
		type ConversationAgent
	} from '$lib/apis/conversation-agents';

	let loaded = false;
	let agents: ConversationAgent[] = [];

	const loadAgents = async () => {
		try {
			agents = await getConversationAgents(localStorage.token);
		} catch (e) {
			console.error(e);
			toast.error(typeof e === 'string' ? e : $i18n.t('Failed to load scenarios'));
		}
	};

	const handleCreate = async () => {
		try {
			const agent = await createConversationAgent(localStorage.token, {
				name: $i18n.t('New scenario'),
				description: '',
				system_prompt: '',
				model_id: null,
				voice_config: {},
				meta: {}
			});
			await goto(`/conversation/${agent.id}`);
		} catch (e) {
			console.error(e);
			toast.error(typeof e === 'string' ? e : $i18n.t('Failed to create scenario'));
		}
	};

	onMount(async () => {
		if ($user?.role !== 'admin') {
			await goto('/');
			return;
		}
		await loadAgents();
		loaded = true;
	});
</script>

{#if loaded}
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
						<a class="min-w-fit transition" href="/conversation">
							{$i18n.t('Conversation Rehearsal')}
						</a>
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
				<div class="flex items-start justify-between mb-6 gap-4">
					<div>
						<h1 class="text-xl font-semibold">{$i18n.t('Rehearsal scenarios')}</h1>
						<p class="text-sm text-gray-500 dark:text-gray-400 mt-1 max-w-xl">
							{$i18n.t(
								'Practice your next conversation out loud. Set up the other side — a client, an interviewer, a tough customer — and run a live voice rehearsal before the real thing.'
							)}
						</p>
					</div>
					<button
						class="px-3 py-1.5 rounded-lg text-sm bg-gray-900 text-white hover:bg-gray-800 dark:bg-gray-100 dark:text-gray-900 dark:hover:bg-white transition whitespace-nowrap"
						on:click={handleCreate}
					>
						{$i18n.t('New scenario')}
					</button>
				</div>

				<AgentList {agents} on:refresh={loadAgents} />
			</div>
		</div>
	</div>
{/if}
