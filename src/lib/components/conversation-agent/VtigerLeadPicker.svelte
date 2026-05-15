<script lang="ts">
	import { createEventDispatcher, getContext, onMount } from 'svelte';
	import Modal from '$lib/components/common/Modal.svelte';
	import {
		searchVtigerLeads,
		getVtigerLead,
		type VtigerLeadSummary,
		type VtigerLead
	} from '$lib/apis/vtiger';

	const i18n = getContext('i18n');
	const dispatch = createEventDispatcher<{ select: VtigerLead; close: void }>();

	export let show = false;

	const PAGE_SIZE = 20;

	let query = '';
	let leads: VtigerLeadSummary[] = [];
	let offset = 0;
	let hasMore = false;
	let loading = false;
	let error = '';
	let searchTimer: ReturnType<typeof setTimeout> | null = null;
	let selectingId = '';

	const load = async (reset: boolean) => {
		loading = true;
		error = '';
		const nextOffset = reset ? 0 : offset;
		try {
			const res = await searchVtigerLeads(localStorage.token, query, PAGE_SIZE, nextOffset);
			leads = reset ? res.leads : [...leads, ...res.leads];
			offset = nextOffset + res.leads.length;
			hasMore = res.has_more;
		} catch (e: any) {
			error = e?.detail ?? e?.message ?? String(e);
		} finally {
			loading = false;
		}
	};

	const onQueryInput = () => {
		if (searchTimer) clearTimeout(searchTimer);
		searchTimer = setTimeout(() => load(true), 250);
	};

	const choose = async (lead: VtigerLeadSummary) => {
		if (selectingId) return;
		selectingId = lead.id;
		try {
			// Fetch full lead so we have description/website/etc. for background.
			const full = await getVtigerLead(localStorage.token, lead.id);
			dispatch('select', full);
			show = false;
		} catch (e: any) {
			error = e?.detail ?? e?.message ?? String(e);
		} finally {
			selectingId = '';
		}
	};

	const displayName = (l: VtigerLeadSummary) => {
		const n = `${l.firstname} ${l.lastname}`.trim();
		return n || l.email || l.id;
	};

	const subline = (l: VtigerLeadSummary) => {
		const parts = [l.designation, l.company].filter(Boolean);
		return parts.join(' · ');
	};

	$: if (show && leads.length === 0 && !loading && !error) {
		load(true);
	}

	$: if (!show) {
		// Reset state when the modal closes so reopening starts clean.
		query = '';
		leads = [];
		offset = 0;
		hasMore = false;
		error = '';
	}
</script>

<Modal bind:show size="md">
	<div class="px-5 pt-5 pb-3">
		<div class="flex items-center justify-between mb-3">
			<div class="text-base font-semibold">
				{$i18n.t('Import customer from CRM')}
			</div>
			<button
				type="button"
				class="text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition"
				on:click={() => (show = false)}
				aria-label={$i18n.t('Close')}
			>
				<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
			</button>
		</div>

		<input
			type="text"
			class="w-full px-3 py-2 rounded-lg border border-gray-200 dark:border-gray-800 bg-transparent text-sm focus:outline-none focus:border-gray-400 dark:focus:border-gray-600"
			bind:value={query}
			on:input={onQueryInput}
			placeholder={$i18n.t('Search by name, company, or email')}
		/>
	</div>

	<div class="px-5 pb-5">
		{#if error}
			<div class="text-xs text-red-500 py-3">
				{error}
			</div>
		{/if}

		<div class="max-h-[55vh] overflow-y-auto -mx-1">
			{#if leads.length === 0 && !loading && !error}
				<div class="text-xs text-gray-400 py-6 text-center">
					{$i18n.t('No leads found.')}
				</div>
			{/if}

			{#each leads as lead (lead.id)}
				<button
					type="button"
					class="w-full text-left px-3 py-2.5 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-900 transition flex flex-col gap-0.5 disabled:opacity-50"
					on:click={() => choose(lead)}
					disabled={!!selectingId}
				>
					<div class="text-sm font-medium flex items-center gap-2">
						<span>{displayName(lead)}</span>
						{#if selectingId === lead.id}
							<span class="text-xs text-gray-400">{$i18n.t('Loading...')}</span>
						{/if}
					</div>
					{#if subline(lead)}
						<div class="text-xs text-gray-500 dark:text-gray-400">
							{subline(lead)}
						</div>
					{/if}
					{#if lead.email || lead.phone}
						<div class="text-xs text-gray-400 dark:text-gray-500">
							{[lead.email, lead.phone].filter(Boolean).join(' · ')}
						</div>
					{/if}
				</button>
			{/each}

			{#if loading}
				<div class="text-xs text-gray-400 py-3 text-center">
					{$i18n.t('Loading...')}
				</div>
			{/if}
		</div>

		{#if hasMore && !loading}
			<div class="flex justify-center pt-3">
				<button
					type="button"
					class="px-3 py-1.5 rounded-lg text-xs border border-gray-200 dark:border-gray-800 hover:bg-gray-50 dark:hover:bg-gray-900 transition"
					on:click={() => load(false)}
				>
					{$i18n.t('Load more')}
				</button>
			</div>
		{/if}
	</div>
</Modal>
