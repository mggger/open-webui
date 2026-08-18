<script lang="ts">
	import { onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import SensitiveInput from '$lib/components/common/SensitiveInput.svelte';
	import {
		getBrainMCPTools,
		getBrainSettings,
		updateBrainSettings,
		type BrainSettings,
		type MCPToolInfo
	} from '$lib/apis/brain';
	import { models } from '$lib/stores';

	let settings: BrainSettings | null = null;
	let saving = false;
	let mcpTools: MCPToolInfo[] = [];
	let fetchingTools = false;
	let mcpToolsError = '';
	let fetchedMCPKey = '';
	let fetchTimer: ReturnType<typeof setTimeout> | undefined;

	const selectedTools = () =>
		new Set(
			(settings?.MCP_ALLOWED_TOOLS ?? '')
				.split(',')
				.map((tool) => tool.trim())
				.filter(Boolean)
		);

	const setSelectedTools = (tools: string[]) => {
		if (settings) settings = { ...settings, MCP_ALLOWED_TOOLS: tools.join(',') };
	};

	const toggleTool = (name: string) => {
		const selected = selectedTools();
		if (selected.has(name)) selected.delete(name);
		else selected.add(name);
		setSelectedTools(mcpTools.map((tool) => tool.name).filter((name) => selected.has(name)));
	};

	const fetchMCPTools = async () => {
		if (!settings?.MCP_URL.trim()) {
			mcpTools = [];
			mcpToolsError = '';
			fetchedMCPKey = '';
			return;
		}
		const key = `${settings.MCP_URL.trim()}\n${settings.MCP_HEADERS}`;
		fetchingTools = true;
		mcpToolsError = '';
		try {
			const tools = await getBrainMCPTools(
				localStorage.token,
				settings.MCP_URL.trim(),
				settings.MCP_HEADERS
			);
			mcpTools = tools;
			if (key !== fetchedMCPKey || !settings.MCP_ALLOWED_TOOLS.trim()) {
				setSelectedTools(tools.map((tool) => tool.name));
			}
			fetchedMCPKey = key;
		} catch (error) {
			mcpTools = [];
			mcpToolsError = error instanceof Error ? error.message : 'Unable to fetch MCP tools';
		} finally {
			fetchingTools = false;
		}
	};

	const scheduleMCPFetch = () => {
		if (fetchTimer) clearTimeout(fetchTimer);
		fetchTimer = setTimeout(fetchMCPTools, 600);
	};

	const save = async () => {
		if (!settings) return;
		saving = true;
		try {
			settings = await updateBrainSettings(localStorage.token, settings);
			toast.success('Brain settings saved. New sessions will use the updated configuration.');
		} catch (error) {
			toast.error(error instanceof Error ? error.message : 'Unable to save settings');
		} finally {
			saving = false;
		}
	};

	onMount(async () => {
		settings = await getBrainSettings(localStorage.token).catch((error) => {
			toast.error(error.message);
			return null;
		});
		if (settings?.MCP_URL.trim()) {
			if (settings.MCP_ALLOWED_TOOLS.trim()) {
				fetchedMCPKey = `${settings.MCP_URL.trim()}\n${settings.MCP_HEADERS}`;
			}
			await fetchMCPTools();
		}
	});

</script>

<form class="flex h-full flex-col justify-between gap-4 text-sm" on:submit|preventDefault={save}>
	<div class="h-full space-y-6 overflow-y-auto scrollbar-hidden">
		<div>
			<div class="mb-2.5 text-base font-medium">Brain Runtime</div>
			<hr class="my-3 border-gray-100/30 dark:border-gray-850/30" />
			{#if settings}
				<div>
					<label class="block"
						><span class="text-xs font-medium">Display name</span><input
							class="field"
							bind:value={settings.NAME}
						/></label
					>
				</div>
			{/if}
		</div>

		{#if settings}
			<div>
				<div class="mb-2.5 text-base font-medium">LiveKit</div>
				<hr class="my-3 border-gray-100/30 dark:border-gray-850/30" />
				<div class="grid gap-4 md:grid-cols-2">
					<label class="block md:col-span-2"
						><span class="label">WebSocket URL</span><input
							class="field"
							bind:value={settings.LIVEKIT_URL}
							placeholder="wss://livekit.internal"
						/></label
					>
					<label class="block"
						><span class="label">API Key</span><SensitiveInput
							bind:value={settings.LIVEKIT_API_KEY}
						/></label
					>
					<label class="block"
						><span class="label">API Secret</span><SensitiveInput
							bind:value={settings.LIVEKIT_API_SECRET}
						/></label
					>
				</div>
			</div>

			<div>
				<div class="mb-2.5 text-base font-medium">Speech models</div>
				<hr class="my-3 border-gray-100/30 dark:border-gray-850/30" />
				<div class="space-y-4 rounded-xl border border-gray-200/70 bg-gray-50/60 p-4 dark:border-gray-800 dark:bg-gray-900/30">
					<div class="text-sm font-medium">Inherited from Audio settings</div>
					<p class="mt-1 text-xs leading-5 text-gray-500 dark:text-gray-400">
						Brain connects directly to the STT and TTS model endpoints configured there. It does not use Open WebUI's audio proxy APIs.
					</p>
					<a class="mt-3 inline-flex text-xs font-medium underline underline-offset-4" href="/admin/settings/audio">Open Audio settings</a>
					<label class="block border-t border-gray-200/70 pt-4 dark:border-gray-800">
						<span class="label">Speech recognition language</span>
						<select class="field" bind:value={settings.STT_LANGUAGE}>
							<option value="en">English only</option>
							<option value="auto">Automatic detection</option>
							<option value="zh">Chinese</option>
						</select>
						<p class="mt-1.5 text-xs text-gray-500 dark:text-gray-400">
							English only is recommended for the current STT model.
						</p>
					</label>
				</div>
			</div>

			<div>
				<div class="mb-2.5 text-base font-medium">Language model</div>
				<hr class="my-3 border-gray-100/30 dark:border-gray-850/30" />
				<label class="block">
					<span class="label">Model</span>
					<select class="field" bind:value={settings.LLM_MODEL}>
						<option value="">Select a model</option>
						{#each $models.filter((model) => model.owned_by === 'openai') as model}
							<option value={model.id}>{model.name ?? model.id}</option>
						{/each}
					</select>
					<p class="mt-1.5 text-xs text-gray-500 dark:text-gray-400">
						Connection URL and credentials are inherited from Connections.
					</p>
				</label>
			</div>

			<div>
				<div class="mb-2.5 text-base font-medium">MCP capabilities</div>
				<hr class="my-3 border-gray-100/30 dark:border-gray-850/30" />
				<div class="space-y-4">
					<label class="block"
						><span class="label">Streamable HTTP URL</span><input
							class="field"
							bind:value={settings.MCP_URL}
							on:input={scheduleMCPFetch}
							placeholder="http://mcp.internal/mcp"
						/></label
					>
					<label class="block"
						><span class="label">Headers JSON</span><textarea
							class="field min-h-20 resize-y font-mono text-xs"
							bind:value={settings.MCP_HEADERS}
							on:input={scheduleMCPFetch}
							spellcheck="false"
							placeholder={'{"Authorization":"Bearer ..."}'}
						></textarea></label
					>
					<div class="rounded-xl border border-gray-200/70 dark:border-gray-800">
						<div class="flex items-center justify-between border-b border-gray-200/70 px-3 py-2.5 dark:border-gray-800">
							<div>
								<div class="text-xs font-medium">Available tools</div>
								<div class="mt-0.5 text-[11px] text-gray-500">
									{fetchingTools ? 'Fetching tools…' : `${selectedTools().size} of ${mcpTools.length} selected`}
								</div>
							</div>
							<div class="flex gap-3 text-xs">
								<button type="button" class="underline underline-offset-4" on:click={() => setSelectedTools(mcpTools.map((tool) => tool.name))}>Select all</button>
								<button type="button" class="underline underline-offset-4" on:click={fetchMCPTools}>Refresh</button>
							</div>
						</div>
						{#if mcpToolsError}
							<div class="px-3 py-3 text-xs text-red-600 dark:text-red-400">{mcpToolsError}</div>
						{:else if !settings.MCP_URL.trim()}
							<div class="px-3 py-3 text-xs text-gray-500">Enter an MCP URL to discover its tools automatically.</div>
						{:else if !fetchingTools && mcpTools.length === 0}
							<div class="px-3 py-3 text-xs text-gray-500">No tools were returned by this server.</div>
						{:else}
							<div class="max-h-72 divide-y divide-gray-200/70 overflow-y-auto dark:divide-gray-800">
								{#each mcpTools as tool}
									<label class="flex cursor-pointer gap-3 px-3 py-2.5 hover:bg-gray-50 dark:hover:bg-gray-900/50">
										<input type="checkbox" class="mt-0.5" checked={selectedTools().has(tool.name)} on:change={() => toggleTool(tool.name)} />
										<span class="min-w-0">
											<span class="block text-xs font-medium">{tool.name}</span>
											{#if tool.description}<span class="mt-0.5 block text-[11px] leading-4 text-gray-500">{tool.description}</span>{/if}
										</span>
									</label>
								{/each}
							</div>
						{/if}
					</div>
					<label class="block"
						><span class="label">Brain system instructions</span><textarea
							class="field min-h-28 resize-y"
							bind:value={settings.INSTRUCTIONS}
						></textarea></label
					>
				</div>
			</div>
		{/if}
	</div>

	<div class="flex justify-end pt-3">
		<button
			disabled={saving || !settings}
			class="rounded-full bg-black px-4 py-1.5 text-white disabled:opacity-50 dark:bg-white dark:text-black"
			>{saving ? 'Saving…' : 'Save'}</button
		>
	</div>
</form>

<style>
	:global(.field) {
		width: 100%;
		margin-top: 0.4rem;
		border-radius: 0.6rem;
		border: 1px solid rgb(229 231 235);
		background: transparent;
		padding: 0.55rem 0.7rem;
		outline: none;
	}
	:global(.dark .field) {
		border-color: rgb(55 65 81);
	}
	:global(.label) {
		font-size: 0.75rem;
		font-weight: 500;
	}
</style>
