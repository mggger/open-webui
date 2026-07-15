<script lang="ts">
	import { onMount } from 'svelte';
	import { toast } from 'svelte-sonner';

	import Calendar from '$lib/components/icons/Calendar.svelte';
	import ChevronLeft from '$lib/components/icons/ChevronLeft.svelte';
	import ChevronRight from '$lib/components/icons/ChevronRight.svelte';
	import GlobeAlt from '$lib/components/icons/GlobeAlt.svelte';
	import Link from '$lib/components/icons/Link.svelte';
	import Plus from '$lib/components/icons/Plus.svelte';
	import Refresh from '$lib/components/icons/Refresh.svelte';
	import Search from '$lib/components/icons/Search.svelte';
	import Settings from '$lib/components/icons/Settings.svelte';
	import UserGroup from '$lib/components/icons/UserGroup.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';
	import { WEBUI_API_BASE_URL } from '$lib/constants';
	import { showSidebar } from '$lib/stores';
	import {
		DEFAULT_EVENT_SOURCES,
		loadEventSources,
		readStoredEventSources,
		writeStoredEventSources
	} from '$lib/events/event-sources';
	import type { BigEvent, EventCategory, EventSource, EventSourceResult } from '$lib/events/types';

	const today = new Date();
	const todayKey = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`;
	const weekdays = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
	const categoryMeta: Record<EventCategory, { label: string; classes: string; dot: string }> = {
		technology: {
			label: 'Technology',
			classes: 'bg-violet-50 text-violet-700 dark:bg-violet-950/50 dark:text-violet-300',
			dot: 'bg-violet-500'
		},
		business: {
			label: 'Business',
			classes: 'bg-blue-50 text-blue-700 dark:bg-blue-950/50 dark:text-blue-300',
			dot: 'bg-blue-500'
		},
		science: {
			label: 'Science',
			classes: 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950/50 dark:text-emerald-300',
			dot: 'bg-emerald-500'
		},
		culture: {
			label: 'Culture',
			classes: 'bg-amber-50 text-amber-700 dark:bg-amber-950/50 dark:text-amber-300',
			dot: 'bg-amber-500'
		},
		sports: {
			label: 'Sports',
			classes: 'bg-rose-50 text-rose-700 dark:bg-rose-950/50 dark:text-rose-300',
			dot: 'bg-rose-500'
		},
		other: {
			label: 'Other',
			classes: 'bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300',
			dot: 'bg-gray-400'
		}
	};

	type CalendarDay = { key: string; day: number; inMonth: boolean; isToday: boolean };

	let viewDate = new Date(today.getFullYear(), today.getMonth(), 1);
	let sources: EventSource[] = DEFAULT_EVENT_SOURCES;
	let results: EventSourceResult[] = [];
	let loading = true;
	let loadedYear = viewDate.getFullYear();
	let query = '';
	let selectedCategories = new Set<EventCategory>();
	let selectedEvent: BigEvent | null = null;
	let selectedDate = todayKey;
	let showSources = false;
	let showAddSource = false;
	let newSourceName = '';
	let newSourceUrl = '';
	let newSourceKind: 'json' | 'ics' = 'json';
	let sourceFormError = '';
	let requestController: AbortController | null = null;
	let refreshingSources = false;

	$: viewYear = viewDate.getFullYear();
	$: viewMonth = viewDate.getMonth();
	$: monthLabel = new Intl.DateTimeFormat(undefined, { month: 'long', year: 'numeric' }).format(
		viewDate
	);
	$: calendarDays = makeCalendarDays(viewYear, viewMonth);
	$: allEvents = results
		.flatMap((result) => result.events)
		.sort((a, b) => a.start.localeCompare(b.start));
	$: filteredEvents = allEvents.filter((event) => {
		const matchesCategory = selectedCategories.size === 0 || selectedCategories.has(event.category);
		const haystack =
			`${event.title} ${event.description ?? ''} ${event.location ?? ''} ${event.organiser ?? ''} ${event.targetAudience ?? ''}`.toLowerCase();
		return matchesCategory && haystack.includes(query.trim().toLowerCase());
	});
	$: eventsByDate = filteredEvents.reduce<Record<string, BigEvent[]>>((groups, event) => {
		(groups[event.start] ??= []).push(event);
		return groups;
	}, {});
	$: selectedDateEvents = eventsByDate[selectedDate] ?? [];
	$: upcomingEvents = filteredEvents.filter((event) => event.start >= todayKey).slice(0, 12);
	$: activeSourceCount = sources.filter((source) => source.enabled).length;

	function makeCalendarDays(year: number, month: number): CalendarDay[] {
		const first = new Date(year, month, 1);
		const mondayOffset = (first.getDay() + 6) % 7;
		const start = new Date(year, month, 1 - mondayOffset);
		return Array.from({ length: 42 }, (_, index) => {
			const date = new Date(start.getFullYear(), start.getMonth(), start.getDate() + index);
			const key = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
			return {
				key,
				day: date.getDate(),
				inMonth: date.getMonth() === month,
				isToday: key === todayKey
			};
		});
	}

	const formatEventDate = (date: string, options?: Intl.DateTimeFormatOptions) => {
		const [year, month, day] = date.split('-').map(Number);
		return new Intl.DateTimeFormat(
			undefined,
			options ?? { month: 'short', day: 'numeric', weekday: 'short' }
		).format(new Date(year, month - 1, day));
	};

	const sourceFor = (event: BigEvent) => sources.find((source) => source.id === event.sourceId);
	const registrationLinkFor = (event: BigEvent) => event.registrationUrl ?? event.url;

	const formatEventRange = (event: BigEvent) => {
		const start = formatEventDate(event.start, {
			month: 'long',
			day: 'numeric',
			year: 'numeric'
		});
		if (!event.end || event.end === event.start) return start;
		return `${start} – ${formatEventDate(event.end, {
			month: 'long',
			day: 'numeric',
			year: 'numeric'
		})}`;
	};

	function selectEvent(event: BigEvent) {
		selectedDate = event.start;
		selectedEvent = event;
	}

	async function refreshEvents(year = viewYear) {
		requestController?.abort();
		const controller = new AbortController();
		requestController = controller;
		loading = true;
		const nextResults = await loadEventSources(sources, {
			year,
			signal: controller.signal,
			token: localStorage.token
		});
		if (requestController !== controller) return;
		results = nextResults;
		loadedYear = year;
		loading = false;
	}

	async function refreshAllSources() {
		if (refreshingSources) return;
		refreshingSources = true;
		loading = true;
		try {
			const response = await fetch(`${WEBUI_API_BASE_URL}/big-events/refresh`, {
				method: 'POST',
				headers: localStorage.token ? { Authorization: `Bearer ${localStorage.token}` } : undefined
			});
			if (!response.ok) {
				const payload = await response.json().catch(() => null);
				throw new Error(payload?.detail ?? `Crawler request failed (HTTP ${response.status})`);
			}
			await refreshEvents();
			toast.success('Event source crawl completed and recommendations refreshed.');
		} catch (error) {
			loading = false;
			toast.error(error instanceof Error ? error.message : 'Could not refresh event sources.');
		} finally {
			refreshingSources = false;
		}
	}

	async function moveMonth(offset: number) {
		const next = new Date(viewYear, viewMonth + offset, 1);
		viewDate = next;
		selectedDate = `${next.getFullYear()}-${String(next.getMonth() + 1).padStart(2, '0')}-01`;
		if (next.getFullYear() !== loadedYear) await refreshEvents(next.getFullYear());
	}

	async function goToday() {
		viewDate = new Date(today.getFullYear(), today.getMonth(), 1);
		selectedDate = todayKey;
		if (today.getFullYear() !== loadedYear) await refreshEvents(today.getFullYear());
	}

	function toggleCategory(category: EventCategory) {
		const next = new Set(selectedCategories);
		if (next.has(category)) {
			next.delete(category);
		} else {
			next.add(category);
		}
		selectedCategories = next;
	}

	async function toggleSource(sourceId: string) {
		sources = sources.map((source) =>
			source.id === sourceId ? { ...source, enabled: !source.enabled } : source
		);
		writeStoredEventSources(localStorage, sources);
		await refreshEvents();
	}

	async function removeSource(sourceId: string) {
		sources = sources.filter((source) => source.id !== sourceId);
		writeStoredEventSources(localStorage, sources);
		await refreshEvents();
	}

	async function addSource() {
		sourceFormError = '';
		if (!newSourceName.trim()) {
			sourceFormError = 'Give this source a name.';
			return;
		}
		try {
			const url = new URL(newSourceUrl);
			if (!['http:', 'https:'].includes(url.protocol)) throw new Error();
		} catch {
			sourceFormError = 'Enter a valid HTTP or HTTPS URL.';
			return;
		}

		const colors = ['#0ea5e9', '#10b981', '#f59e0b', '#f43f5e', '#8b5cf6'];
		sources = [
			...sources,
			{
				id: crypto.randomUUID(),
				name: newSourceName.trim(),
				url: newSourceUrl.trim(),
				kind: newSourceKind,
				enabled: true,
				color: colors[(sources.length - 1) % colors.length]
			}
		];
		writeStoredEventSources(localStorage, sources);
		newSourceName = '';
		newSourceUrl = '';
		showAddSource = false;
		await refreshEvents();
	}

	onMount(() => {
		sources = readStoredEventSources(localStorage);
		void refreshEvents();
		return () => requestController?.abort();
	});
</script>

<svelte:head>
	<title>Sydney executive events</title>
</svelte:head>

<div
	class="h-screen max-h-[100dvh] w-full overflow-y-auto bg-white text-gray-900 transition-width duration-200 ease-in-out dark:bg-gray-950 dark:text-gray-100 {$showSidebar
		? 'md:max-w-[calc(100%-260px)]'
		: ''}"
>
	<div class="mx-auto flex min-h-full w-full max-w-[1600px] flex-col px-4 py-5 sm:px-6 lg:px-8">
		<header class="mb-5 flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
			<div>
				<div
					class="mb-1 flex items-center gap-2 text-xs font-medium uppercase tracking-[0.18em] text-indigo-600 dark:text-indigo-400"
				>
					<span class="inline-block size-1.5 rounded-full bg-indigo-500"></span>
					Sydney executive intelligence
				</div>
				<h1 class="text-2xl font-semibold tracking-tight sm:text-3xl">Executive events</h1>
				<p class="mt-1 max-w-2xl text-sm text-gray-500 dark:text-gray-400">
					Curated Sydney opportunities to meet CEOs, directors, founders and technology leaders.
				</p>
			</div>

			<div class="flex flex-wrap items-center gap-2">
				<label class="relative min-w-[220px] flex-1 lg:flex-none">
					<Search className="pointer-events-none absolute left-3 top-2.5 size-4 text-gray-400" />
					<input
						bind:value={query}
						class="h-9 w-full rounded-xl border border-gray-200 bg-white pl-9 pr-3 text-sm outline-none transition focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100 dark:border-gray-800 dark:bg-gray-900 dark:focus:ring-indigo-950"
						placeholder="Search events, audiences or organisers"
					/>
				</label>
				<button
					class="flex h-9 items-center gap-2 rounded-xl border border-gray-200 px-3 text-sm font-medium transition hover:bg-gray-50 dark:border-gray-800 dark:hover:bg-gray-900"
					on:click={() => (showSources = true)}
				>
					<Settings className="size-4" />
					Sources
					<span class="rounded-md bg-gray-100 px-1.5 py-0.5 text-xs dark:bg-gray-800"
						>{activeSourceCount}</span
					>
				</button>
			</div>
		</header>

		<div class="mb-4 flex gap-2 overflow-x-auto pb-1 scrollbar-hidden">
			{#each Object.entries(categoryMeta) as [category, meta]}
				<button
					class="flex shrink-0 items-center gap-2 rounded-full border px-3 py-1.5 text-xs font-medium transition {selectedCategories.has(
						category as EventCategory
					)
						? 'border-gray-700 bg-gray-900 text-white dark:border-gray-200 dark:bg-white dark:text-gray-900'
						: 'border-gray-200 bg-white text-gray-600 hover:border-gray-300 dark:border-gray-800 dark:bg-gray-950 dark:text-gray-300'}"
					on:click={() => toggleCategory(category as EventCategory)}
				>
					<span class="size-1.5 rounded-full {meta.dot}"></span>{meta.label}
				</button>
			{/each}
		</div>

		<div class="grid flex-1 gap-4 xl:grid-cols-[minmax(0,1fr)_340px]">
			<section
				class="min-w-0 overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-sm dark:border-gray-800 dark:bg-gray-900/40"
			>
				<div
					class="flex flex-wrap items-center justify-between gap-3 border-b border-gray-100 px-4 py-3 dark:border-gray-800 sm:px-5"
				>
					<div class="flex items-center gap-2">
						<button
							class="rounded-lg p-2 hover:bg-gray-100 dark:hover:bg-gray-800"
							aria-label="Previous month"
							on:click={() => moveMonth(-1)}
						>
							<ChevronLeft className="size-4" />
						</button>
						<h2 class="min-w-[150px] text-center text-base font-semibold sm:text-lg">
							{monthLabel}
						</h2>
						<button
							class="rounded-lg p-2 hover:bg-gray-100 dark:hover:bg-gray-800"
							aria-label="Next month"
							on:click={() => moveMonth(1)}
						>
							<ChevronRight className="size-4" />
						</button>
					</div>
					<div class="flex items-center gap-2">
						{#if loading}<span class="text-xs text-gray-400">Updating…</span>{/if}
						<button
							class="rounded-lg border border-gray-200 px-3 py-1.5 text-xs font-medium hover:bg-gray-50 dark:border-gray-700 dark:hover:bg-gray-800"
							on:click={goToday}>Today</button
						>
					</div>
				</div>

				<div
					class="grid grid-cols-7 border-b border-gray-100 bg-gray-50/70 dark:border-gray-800 dark:bg-gray-900"
				>
					{#each weekdays as weekday}
						<div
							class="px-1 py-2 text-center text-[10px] font-semibold uppercase tracking-wider text-gray-400 sm:text-xs"
						>
							{weekday}
						</div>
					{/each}
				</div>

				<div class="grid grid-cols-7">
					{#each calendarDays as day}
						{@const dayEvents = eventsByDate[day.key] ?? []}
						<div
							class="group relative min-h-[78px] border-b border-r border-gray-100 p-1.5 text-left transition hover:bg-indigo-50/50 dark:border-gray-800 dark:hover:bg-indigo-950/20 sm:min-h-[116px] sm:p-2 {day.inMonth
								? ''
								: 'bg-gray-50/50 dark:bg-gray-950/40'} {selectedDate === day.key
								? 'ring-1 ring-inset ring-indigo-400'
								: ''}"
						>
							<button
								class="absolute inset-0 z-0 cursor-pointer"
								aria-label={`Show events for ${formatEventDate(day.key, {
									month: 'long',
									day: 'numeric',
									year: 'numeric'
								})}`}
								on:click={() => (selectedDate = day.key)}
							></button>
							<span
								class="pointer-events-none relative z-[1] inline-flex size-6 items-center justify-center rounded-full text-xs font-medium {day.isToday
									? 'bg-indigo-600 text-white'
									: day.inMonth
										? 'text-gray-700 dark:text-gray-200'
										: 'text-gray-300 dark:text-gray-600'}">{day.day}</span
							>
							<div class="mt-1 hidden space-y-1 sm:block">
								{#each dayEvents.slice(0, 3) as event}
									<button
										class="relative z-[2] flex w-full min-w-0 items-center gap-1.5 rounded-md bg-gray-50 px-1.5 py-1 text-left transition hover:bg-indigo-100 focus:outline-none focus:ring-2 focus:ring-indigo-400 dark:bg-gray-800/70 dark:hover:bg-indigo-950"
										title={event.title}
										aria-label={`Open ${event.title}`}
										on:click={() => selectEvent(event)}
									>
										<span
											class="size-1.5 shrink-0 rounded-full"
											style={`background:${sourceFor(event)?.color ?? '#9ca3af'}`}
										></span>
										<span
											class="truncate text-[10px] font-medium text-gray-700 dark:text-gray-200 lg:text-xs"
											>{event.title}</span
										>
									</button>
								{/each}
								{#if dayEvents.length > 3}<div class="pl-1 text-[10px] font-medium text-gray-400">
										+{dayEvents.length - 3} more
									</div>{/if}
							</div>
							{#if dayEvents.length > 0}<div
									class="pointer-events-none mt-1 flex gap-0.5 sm:hidden"
								>
									{#each dayEvents.slice(0, 3) as event}<span
											class="size-1.5 rounded-full"
											style={`background:${sourceFor(event)?.color ?? '#9ca3af'}`}
										></span>{/each}
								</div>{/if}
						</div>
					{/each}
				</div>
			</section>

			<aside
				class="flex min-h-[420px] flex-col overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-sm dark:border-gray-800 dark:bg-gray-900/40"
			>
				<div class="border-b border-gray-100 px-5 py-4 dark:border-gray-800">
					<p class="text-xs font-medium uppercase tracking-wider text-gray-400">Selected day</p>
					<h2 class="mt-1 text-lg font-semibold">
						{formatEventDate(selectedDate, { month: 'long', day: 'numeric', year: 'numeric' })}
					</h2>
					<p class="mt-0.5 text-xs text-gray-400">
						{selectedDateEvents.length}
						{selectedDateEvents.length === 1 ? 'event' : 'events'}
					</p>
				</div>
				<div class="flex-1 overflow-y-auto p-3">
					{#if selectedDateEvents.length}
						<div class="space-y-2">
							{#each selectedDateEvents as event}
								<button
									class="w-full rounded-xl border border-gray-100 p-3 text-left transition hover:border-indigo-200 hover:bg-indigo-50/40 dark:border-gray-800 dark:hover:border-indigo-900 dark:hover:bg-indigo-950/20"
									on:click={() => selectEvent(event)}
								>
									<div class="mb-2 flex items-center justify-between gap-2">
										<span
											class="rounded-md px-2 py-1 text-[10px] font-semibold {categoryMeta[
												event.category
											].classes}">{categoryMeta[event.category].label}</span
										>
										<span class="truncate text-[10px] text-gray-400">{sourceFor(event)?.name}</span>
									</div>
									<h3 class="text-sm font-semibold leading-snug">{event.title}</h3>
									{#if event.organiser}<p class="mt-1 text-[11px] font-medium text-gray-400">
											{event.organiser}
										</p>{/if}
									{#if event.description}<p
											class="mt-1 line-clamp-2 text-xs leading-relaxed text-gray-500 dark:text-gray-400"
										>
											{event.description}
										</p>{/if}
								</button>
							{/each}
						</div>
					{:else}
						<div class="flex h-44 flex-col items-center justify-center text-center">
							<div class="mb-3 rounded-2xl bg-gray-100 p-3 text-gray-400 dark:bg-gray-800">
								<Calendar className="size-5" />
							</div>
							<p class="text-sm font-medium">A clear day</p>
							<p class="mt-1 max-w-[210px] text-xs leading-relaxed text-gray-400">
								Choose a date with a marker, or add another event source.
							</p>
						</div>
					{/if}

					{#if selectedDateEvents.length === 0 && upcomingEvents.length > 0}
						<div class="border-t border-gray-100 pt-4 dark:border-gray-800">
							<p class="mb-2 px-1 text-xs font-semibold text-gray-400">Coming up</p>
							{#each upcomingEvents.slice(0, 4) as event}
								<button
									class="flex w-full gap-3 rounded-xl px-2 py-2 text-left hover:bg-gray-50 dark:hover:bg-gray-800"
									on:click={() => {
										selectEvent(event);
									}}
								>
									<span
										class="mt-1 size-2 shrink-0 rounded-full"
										style={`background:${sourceFor(event)?.color ?? '#9ca3af'}`}
									></span>
									<span class="min-w-0"
										><span class="block truncate text-xs font-medium">{event.title}</span><span
											class="text-[10px] text-gray-400">{formatEventDate(event.start)}</span
										></span
									>
								</button>
							{/each}
						</div>
					{/if}
				</div>
			</aside>
		</div>
	</div>
</div>

{#if showSources}
	<div
		class="fixed inset-0 z-[100] flex justify-end bg-black/40"
		role="presentation"
		on:click={() => (showSources = false)}
	>
		<div
			class="h-full w-full max-w-md overflow-y-auto bg-white p-5 shadow-2xl dark:bg-gray-950"
			role="dialog"
			aria-modal="true"
			aria-label="Event sources"
			tabindex="-1"
			on:click|stopPropagation
			on:keydown|stopPropagation
		>
			<div class="mb-6 flex items-start justify-between">
				<div>
					<h2 class="text-xl font-semibold">Event sources</h2>
					<p class="mt-1 text-sm text-gray-500">
						Built-in executive event sources are crawled daily; you can also add JSON and ICS
						calendars.
					</p>
				</div>
				<button
					class="rounded-lg p-2 hover:bg-gray-100 dark:hover:bg-gray-800"
					aria-label="Close"
					on:click={() => (showSources = false)}><XMark className="size-5" /></button
				>
			</div>

			<div class="space-y-3">
				{#each sources as source}
					{@const result = results.find((item) => item.source.id === source.id)}
					<div class="rounded-2xl border border-gray-200 p-4 dark:border-gray-800">
						<div class="flex items-start gap-3">
							<span class="mt-1 size-2.5 shrink-0 rounded-full" style={`background:${source.color}`}
							></span>
							<div class="min-w-0 flex-1">
								<div class="flex items-center gap-2">
									<h3 class="truncate text-sm font-semibold">{source.name}</h3>
									<span
										class="rounded-md bg-gray-100 px-1.5 py-0.5 text-[10px] font-medium uppercase text-gray-500 dark:bg-gray-800"
										>{source.kind}</span
									>
								</div>
								{#if source.homepageUrl}
									<a
										href={source.homepageUrl}
										target="_blank"
										rel="noreferrer"
										class="mt-1 block truncate text-xs text-blue-500 hover:underline"
										on:click|stopPropagation>Open source directory</a
									>
								{:else if source.url}<p class="mt-1 truncate text-xs text-gray-400">
										{source.url}
									</p>{/if}
								{#if result?.error}<p
										class="mt-2 rounded-lg bg-red-50 px-2 py-1.5 text-xs text-red-600 dark:bg-red-950/40 dark:text-red-300"
									>
										Could not load: {result.error}
									</p>{:else if source.enabled}<p class="mt-1 text-xs text-gray-400">
										{result?.events.length ?? 0} events loaded
									</p>{/if}
							</div>
							<button
								class="relative h-6 w-11 shrink-0 rounded-full transition {source.enabled
									? 'bg-indigo-600'
									: 'bg-gray-200 dark:bg-gray-700'}"
								aria-label={`Toggle ${source.name}`}
								on:click={() => toggleSource(source.id)}
								><span
									class="absolute top-0.5 size-5 rounded-full bg-white shadow transition-all {source.enabled
										? 'left-[22px]'
										: 'left-0.5'}"
								></span></button
							>
						</div>
						{#if !source.readonly}<div class="mt-3 flex justify-end">
								<button
									class="text-xs font-medium text-red-500 hover:text-red-600"
									on:click={() => removeSource(source.id)}>Remove source</button
								>
							</div>{/if}
					</div>
				{/each}
			</div>

			{#if showAddSource}
				<div
					class="mt-4 rounded-2xl border border-indigo-200 bg-indigo-50/40 p-4 dark:border-indigo-900 dark:bg-indigo-950/20"
				>
					<h3 class="mb-3 text-sm font-semibold">Add a source</h3>
					<div class="space-y-3">
						<label class="block text-xs font-medium text-gray-600 dark:text-gray-300"
							>Name<input
								bind:value={newSourceName}
								class="mt-1.5 h-10 w-full rounded-xl border border-gray-200 bg-white px-3 text-sm outline-none focus:border-indigo-400 dark:border-gray-700 dark:bg-gray-900"
								placeholder="Company launch calendar"
							/></label
						>
						<label class="block text-xs font-medium text-gray-600 dark:text-gray-300"
							>Format<select
								bind:value={newSourceKind}
								class="mt-1.5 h-10 w-full rounded-xl border border-gray-200 bg-white px-3 text-sm outline-none dark:border-gray-700 dark:bg-gray-900"
								><option value="json">JSON feed</option><option value="ics">ICS / iCalendar</option
								></select
							></label
						>
						<label class="block text-xs font-medium text-gray-600 dark:text-gray-300"
							>Feed URL<input
								bind:value={newSourceUrl}
								class="mt-1.5 h-10 w-full rounded-xl border border-gray-200 bg-white px-3 text-sm outline-none focus:border-indigo-400 dark:border-gray-700 dark:bg-gray-900"
								placeholder="https://example.com/events.json"
							/></label
						>
						<p class="text-[11px] leading-relaxed text-gray-400">
							The feed must allow browser access (CORS). JSON can be an array or <code
								>{`{ events: [...] }`}</code
							> with title and date/start fields.
						</p>
						{#if sourceFormError}<p class="text-xs text-red-500">{sourceFormError}</p>{/if}
						<div class="flex justify-end gap-2">
							<button
								class="rounded-lg px-3 py-2 text-xs font-medium hover:bg-white dark:hover:bg-gray-900"
								on:click={() => (showAddSource = false)}>Cancel</button
							><button
								class="rounded-lg bg-indigo-600 px-3 py-2 text-xs font-medium text-white hover:bg-indigo-500"
								on:click={addSource}>Add source</button
							>
						</div>
					</div>
				</div>
			{:else}
				<button
					class="mt-4 flex w-full items-center justify-center gap-2 rounded-xl border border-dashed border-gray-300 py-3 text-sm font-medium text-gray-600 transition hover:border-indigo-400 hover:text-indigo-600 dark:border-gray-700 dark:text-gray-300"
					on:click={() => (showAddSource = true)}
					><Plus className="size-4" /> Add event source</button
				>
			{/if}

			<div class="mt-6 rounded-2xl bg-gray-50 p-4 dark:bg-gray-900">
				<div class="flex gap-3">
					<GlobeAlt className="mt-0.5 size-5 shrink-0 text-indigo-500" />
					<div>
						<h3 class="text-sm font-semibold">Plugin-ready by design</h3>
						<p class="mt-1 text-xs leading-relaxed text-gray-500 dark:text-gray-400">
							JSON and ICS are built in. Rich JSON feeds may include organiser, targetAudience,
							cost, participation, registrationUrl and lastVerified fields.
						</p>
					</div>
				</div>
				<button
					class="mt-3 flex items-center gap-2 text-xs font-medium text-indigo-600 disabled:cursor-wait disabled:opacity-60 dark:text-indigo-400"
					disabled={refreshingSources}
					on:click={refreshAllSources}
					><Refresh className={`size-3.5 ${refreshingSources ? 'animate-spin' : ''}`} />
					{refreshingSources ? 'Crawling event sources…' : 'Refresh all sources'}</button
				>
			</div>
		</div>
	</div>
{/if}

{#if selectedEvent}
	<div
		class="fixed inset-0 z-[110] flex items-end justify-center bg-black/40 p-3 sm:items-center"
		role="presentation"
		on:click={() => (selectedEvent = null)}
	>
		<div
			class="max-h-[92vh] w-full max-w-2xl overflow-y-auto rounded-3xl bg-white p-5 shadow-2xl dark:bg-gray-900 sm:p-7"
			role="dialog"
			aria-modal="true"
			aria-label={selectedEvent.title}
			tabindex="-1"
			on:click|stopPropagation
			on:keydown|stopPropagation
		>
			<div class="mb-4 flex items-start justify-between gap-4">
				<span
					class="rounded-lg px-2.5 py-1 text-xs font-semibold {categoryMeta[selectedEvent.category]
						.classes}">{categoryMeta[selectedEvent.category].label}</span
				><button
					class="rounded-lg p-1.5 hover:bg-gray-100 dark:hover:bg-gray-800"
					aria-label="Close event"
					on:click={() => (selectedEvent = null)}><XMark className="size-5" /></button
				>
			</div>
			<h2 class="text-xl font-semibold leading-tight sm:text-2xl">{selectedEvent.title}</h2>
			{#if selectedEvent.organiser}<p
					class="mt-2 text-sm font-medium text-gray-500 dark:text-gray-400"
				>
					Organised by {selectedEvent.organiser}
				</p>{/if}
			<div class="mt-3 flex flex-wrap gap-x-4 gap-y-2 text-sm text-gray-500 dark:text-gray-400">
				<span class="flex items-center gap-1.5"
					><Calendar className="size-4" />
					{formatEventRange(selectedEvent)}</span
				>{#if selectedEvent.location}<span class="flex items-center gap-1.5"
						><GlobeAlt className="size-4" /> {selectedEvent.location}</span
					>{/if}
			</div>
			{#if selectedEvent.description}<p
					class="mt-5 text-sm leading-6 text-gray-600 dark:text-gray-300"
				>
					{selectedEvent.description}
				</p>{/if}

			<div class="mt-5 grid gap-3 sm:grid-cols-2">
				{#if selectedEvent.targetAudience}<section
						class="rounded-2xl border border-gray-100 bg-gray-50/70 p-4 dark:border-gray-800 dark:bg-gray-950/50"
					>
						<div
							class="mb-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-gray-400"
						>
							<UserGroup className="size-4" /> Target audience
						</div>
						<p class="text-sm leading-6 text-gray-700 dark:text-gray-200">
							{selectedEvent.targetAudience}
						</p>
					</section>{/if}
				{#if selectedEvent.cost}<section
						class="rounded-2xl border border-gray-100 bg-gray-50/70 p-4 dark:border-gray-800 dark:bg-gray-950/50"
					>
						<div
							class="mb-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-gray-400"
						>
							<span
								class="flex size-4 items-center justify-center rounded-full border border-current text-[10px]"
								>$</span
							>
							Cost
						</div>
						<p class="text-sm leading-6 text-gray-700 dark:text-gray-200">{selectedEvent.cost}</p>
					</section>{/if}
			</div>

			{#if selectedEvent.participation}<section class="mt-4">
					<h3 class="flex items-center gap-2 text-sm font-semibold">
						<Link className="size-4 text-gray-400" /> How to participate
					</h3>
					<p class="mt-1.5 text-sm leading-6 text-gray-600 dark:text-gray-300">
						{selectedEvent.participation}
					</p>
				</section>{/if}

			<div
				class="mt-6 flex flex-col-reverse gap-3 border-t border-gray-100 pt-4 sm:flex-row sm:items-center sm:justify-between dark:border-gray-800"
			>
				<div class="text-[11px] leading-5 text-gray-400">
					<div>Source: {sourceFor(selectedEvent)?.name}</div>
					{#if selectedEvent.lastVerified}<div>
							Last verified {formatEventDate(selectedEvent.lastVerified, {
								month: 'short',
								day: 'numeric',
								year: 'numeric'
							})}
						</div>{/if}
				</div>
				{#if registrationLinkFor(selectedEvent)}<a
						class="inline-flex min-h-11 items-center justify-center rounded-xl bg-indigo-600 px-5 text-sm font-semibold text-white transition hover:bg-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-400 focus:ring-offset-2 dark:focus:ring-offset-gray-900"
						href={registrationLinkFor(selectedEvent)}
						target="_blank"
						rel="noopener noreferrer"
						aria-label={`Register for ${selectedEvent.title} (opens in a new tab)`}
						>View registration ↗</a
					>{:else}<span
						class="rounded-xl bg-gray-100 px-4 py-3 text-xs text-gray-500 dark:bg-gray-800"
					>
						Registration link not published
					</span>{/if}
			</div>
		</div>
	</div>
{/if}
