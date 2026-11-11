<script lang="ts">
	import { getContext } from 'svelte';

	import Modal from '$lib/components/common/Modal.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import ChevronRight from '$lib/components/icons/ChevronRight.svelte';
	import FolderOpen from '$lib/components/icons/FolderOpen.svelte';
	import Refresh from '$lib/components/icons/Refresh.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';
	import {
		getFileSearchDirectories,
		type FileSearchConfig,
		type FileSearchDirectory
	} from '$lib/apis/file-search';

	const i18n = getContext('i18n');

	export let show = false;
	export let config: FileSearchConfig;
	export let initialDirectory = '';
	export let onSelect: (directory: string) => void;

	let currentDirectory = '';
	let parentDirectory = '';
	let directories: FileSearchDirectory[] = [];
	let loading = false;
	let error = '';
	let requestId = 0;

	$: breadcrumbs = currentDirectory
		? currentDirectory.split('\\').map((name, index, parts) => ({
				name,
				path: parts.slice(0, index + 1).join('\\')
			}))
		: [];

	const loadDirectories = async (path: string) => {
		const currentRequest = ++requestId;
		loading = true;
		error = '';
		try {
			const result = await getFileSearchDirectories(localStorage.token, path);
			if (currentRequest !== requestId) return;
			currentDirectory = result.current;
			parentDirectory = result.parent;
			directories = result.directories;
		} catch (exception) {
			if (currentRequest !== requestId) return;
			error = String(exception);
			directories = [];
		} finally {
			if (currentRequest === requestId) loading = false;
		}
	};

	const openPicker = () => {
		currentDirectory = initialDirectory ?? '';
		parentDirectory = '';
		directories = [];
		error = '';
		loadDirectories(currentDirectory);
	};

	$: if (show) {
		openPicker();
	}
</script>

<Modal
	bind:show
	size="md"
	containerClassName="p-3"
	className="bg-white dark:bg-gray-900 rounded-3xl"
>
	<div class="flex min-h-[32rem] max-h-[80dvh] flex-col">
		<header
			class="flex items-start justify-between border-b border-gray-100 px-5 py-4 dark:border-gray-800"
		>
			<div class="min-w-0">
				<h1 class="text-lg font-medium">{$i18n.t('Choose a search folder')}</h1>
				<p class="mt-0.5 text-xs text-gray-500">
					{$i18n.t('Only folders accessible to your SMB account are shown.')}
				</p>
			</div>
			<button
				type="button"
				class="rounded-full p-1.5 hover:bg-gray-100 dark:hover:bg-gray-800"
				aria-label={$i18n.t('Close modal')}
				on:click={() => {
					show = false;
				}}
			>
				<XMark className="size-5" />
			</button>
		</header>

		<div class="border-b border-gray-100 px-5 py-3 dark:border-gray-800">
			<div class="mb-1 text-[11px] font-medium uppercase tracking-wide text-gray-400">
				{$i18n.t('SMB location')}
			</div>
			<div class="truncate text-sm" title={config.root}>{config.root}</div>

			<nav class="mt-2 flex min-h-8 items-center gap-1 overflow-x-auto text-sm">
				<button
					type="button"
					disabled={loading}
					class="shrink-0 rounded-lg px-2 py-1 font-medium hover:bg-gray-100 disabled:opacity-50 dark:hover:bg-gray-800"
					on:click={() => loadDirectories('')}
				>
					{config.share}
				</button>
				{#each breadcrumbs as breadcrumb}
					<ChevronRight className="size-3.5 shrink-0 text-gray-400" />
					<button
						type="button"
						disabled={loading}
						class="shrink-0 rounded-lg px-2 py-1 hover:bg-gray-100 disabled:opacity-50 dark:hover:bg-gray-800"
						on:click={() => loadDirectories(breadcrumb.path)}
					>
						{breadcrumb.name}
					</button>
				{/each}
			</nav>
		</div>

		<div class="relative min-h-0 flex-1 overflow-y-auto px-3 py-3">
			{#if loading}
				<div
					class="absolute inset-0 z-10 flex flex-col items-center justify-center gap-3 bg-white/90 dark:bg-gray-900/90"
				>
					<Spinner className="size-7" />
					<div class="text-center">
						<div class="text-sm font-medium">{$i18n.t('Loading accessible folders…')}</div>
						<div class="mt-1 text-xs text-gray-500">
							{$i18n.t('Network folders can take a moment to respond.')}
						</div>
					</div>
				</div>
			{:else if error}
				<div class="flex h-full flex-col items-center justify-center px-6 text-center">
					<div class="text-sm font-medium text-red-600">
						{$i18n.t('Unable to load folders')}
					</div>
					<div class="mt-1 max-w-md text-xs text-gray-500">{error}</div>
					<button
						type="button"
						class="mt-4 flex items-center gap-2 rounded-full border border-gray-200 px-3 py-1.5 text-sm hover:bg-gray-50 dark:border-gray-700 dark:hover:bg-gray-800"
						on:click={() => loadDirectories(currentDirectory)}
					>
						<Refresh className="size-4" />
						{$i18n.t('Retry')}
					</button>
				</div>
			{:else if directories.length === 0}
				<div class="flex h-full flex-col items-center justify-center text-center text-gray-500">
					<FolderOpen className="size-8" />
					<div class="mt-2 text-sm">{$i18n.t('No accessible subfolders')}</div>
					<div class="mt-1 text-xs">
						{$i18n.t('You can select the current folder.')}
					</div>
				</div>
			{:else}
				<div class="grid grid-cols-1 gap-1 sm:grid-cols-2">
					{#each directories as directory}
						<button
							type="button"
							class="group flex min-w-0 items-center gap-3 rounded-xl px-3 py-2.5 text-left hover:bg-gray-100 dark:hover:bg-gray-800"
							on:click={() => loadDirectories(directory.path)}
						>
							<div
								class="rounded-lg bg-amber-50 p-2 text-amber-600 dark:bg-amber-900/20 dark:text-amber-300"
							>
								<FolderOpen className="size-4" />
							</div>
							<span class="min-w-0 flex-1 truncate text-sm">{directory.name}</span>
							<ChevronRight className="size-4 shrink-0 text-gray-300 group-hover:text-gray-500" />
						</button>
					{/each}
				</div>
			{/if}
		</div>

		<footer
			class="flex items-center justify-between gap-3 border-t border-gray-100 px-5 py-4 dark:border-gray-800"
		>
			<div class="min-w-0 text-xs text-gray-500">
				<div>{$i18n.t('Selected folder')}</div>
				<div class="truncate font-medium text-gray-700 dark:text-gray-200">
					{currentDirectory || $i18n.t('Share root')}
				</div>
			</div>
			<div class="flex shrink-0 gap-2">
				<button
					type="button"
					class="rounded-full px-3.5 py-1.5 text-sm hover:bg-gray-100 dark:hover:bg-gray-800"
					on:click={() => {
						show = false;
					}}
				>
					{$i18n.t('Cancel')}
				</button>
				{#if currentDirectory}
					<button
						type="button"
						disabled={loading}
						class="rounded-full border border-gray-200 px-3.5 py-1.5 text-sm hover:bg-gray-50 disabled:opacity-50 dark:border-gray-700 dark:hover:bg-gray-800"
						on:click={() => loadDirectories(parentDirectory)}
					>
						{$i18n.t('Up')}
					</button>
				{/if}
				<button
					type="button"
					disabled={loading || !!error}
					class="rounded-full bg-black px-4 py-1.5 text-sm font-medium text-white disabled:opacity-50 dark:bg-white dark:text-black"
					on:click={() => {
						onSelect(currentDirectory);
						show = false;
					}}
				>
					{$i18n.t('Use this folder')}
				</button>
			</div>
		</footer>
	</div>
</Modal>
