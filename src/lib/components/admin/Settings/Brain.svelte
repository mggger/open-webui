<script lang="ts">
	import { onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import SensitiveInput from '$lib/components/common/SensitiveInput.svelte';
	import {
		getBrainMCPTools,
		getBrainSettings,
		updateBrainSettings,
		type BrainSettings,
		type BrainMCPServerSettings,
		type MCPToolInfo
	} from '$lib/apis/brain';
	import { models } from '$lib/stores';

	let settings: BrainSettings | null = null;
	let saving = false;
	let mcpTools: Record<string, MCPToolInfo[]> = {};
	let fetchingTools: Record<string, boolean> = {};
	let mcpToolsError: Record<string, string> = {};
	let fetchedMCPKeys: Record<string, string> = {};
	const fetchTimers: Record<string, ReturnType<typeof setTimeout>> = {};

	const serverById = (id: string) => settings?.MCP_SERVERS.find((server) => server.ID === id);

	const selectedTools = (id: string) =>
		new Set(
			(serverById(id)?.ALLOWED_TOOLS ?? '')
				.split(',')
				.map((tool) => tool.trim())
				.filter(Boolean)
		);

	const updateServer = (id: string, patch: Partial<BrainMCPServerSettings>) => {
		if (!settings) return;
		settings = {
			...settings,
			MCP_SERVERS: settings.MCP_SERVERS.map((server) =>
				server.ID === id ? { ...server, ...patch } : server
			)
		};
	};

	const setSelectedTools = (id: string, tools: string[]) => {
		updateServer(id, { ALLOWED_TOOLS: tools.join(',') });
	};

	const toggleTool = (id: string, name: string) => {
		const selected = selectedTools(id);
		if (selected.has(name)) selected.delete(name);
		else selected.add(name);
		setSelectedTools(
			id,
			(mcpTools[id] ?? []).map((tool) => tool.name).filter((tool) => selected.has(tool))
		);
	};

	const fetchMCPTools = async (id: string) => {
		const server = serverById(id);
		if (!server?.URL.trim()) {
			mcpTools = { ...mcpTools, [id]: [] };
			mcpToolsError = { ...mcpToolsError, [id]: '' };
			delete fetchedMCPKeys[id];
			return;
		}
		const key = `${server.URL.trim()}\n${server.HEADERS}`;
		fetchingTools = { ...fetchingTools, [id]: true };
		mcpToolsError = { ...mcpToolsError, [id]: '' };
		try {
			const tools = await getBrainMCPTools(
				localStorage.token,
				server.URL.trim(),
				server.HEADERS
			);
			mcpTools = { ...mcpTools, [id]: tools };
			if (key !== fetchedMCPKeys[id] || !server.ALLOWED_TOOLS.trim()) {
				setSelectedTools(id, tools.map((tool) => tool.name));
			}
			fetchedMCPKeys = { ...fetchedMCPKeys, [id]: key };
		} catch (error) {
			mcpTools = { ...mcpTools, [id]: [] };
			mcpToolsError = {
				...mcpToolsError,
				[id]: error instanceof Error ? error.message : 'Unable to fetch MCP tools'
			};
		} finally {
			fetchingTools = { ...fetchingTools, [id]: false };
		}
	};

	const scheduleMCPFetch = (id: string) => {
		if (fetchTimers[id]) clearTimeout(fetchTimers[id]);
		fetchTimers[id] = setTimeout(() => fetchMCPTools(id), 600);
	};

	const addMCPServer = () => {
		if (!settings) return;
		const id = crypto.randomUUID();
		settings = {
			...settings,
			MCP_SERVERS: [
				...settings.MCP_SERVERS,
				{ ID: id, NAME: `MCP Server ${settings.MCP_SERVERS.length + 1}`, URL: '', ALLOWED_TOOLS: '', HEADERS: '{}' }
			]
		};
	};

	const removeMCPServer = (id: string) => {
		if (!settings) return;
		if (fetchTimers[id]) clearTimeout(fetchTimers[id]);
		settings = { ...settings, MCP_SERVERS: settings.MCP_SERVERS.filter((server) => server.ID !== id) };
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
		for (const server of settings?.MCP_SERVERS ?? []) {
			if (!server.URL.trim()) continue;
			if (server.ALLOWED_TOOLS.trim()) {
				fetchedMCPKeys[server.ID] = `${server.URL.trim()}\n${server.HEADERS}`;
			}
			await fetchMCPTools(server.ID);
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
				<div class="mb-2.5 flex items-center justify-between">
					<div class="text-base font-medium">MCP capabilities</div>
					<button type="button" class="rounded-full border border-gray-200 px-3 py-1 text-xs font-medium hover:bg-gray-50 dark:border-gray-800 dark:hover:bg-gray-900" on:click={addMCPServer}>Add MCP server</button>
				</div>
				<hr class="my-3 border-gray-100/30 dark:border-gray-850/30" />
				<div class="space-y-4">
					{#if settings.MCP_SERVERS.length === 0}
						<div class="rounded-xl border border-dashed border-gray-300 px-4 py-6 text-center text-xs text-gray-500 dark:border-gray-700">
							No MCP servers configured. Add one to give Brain access to internal tools.
						</div>
					{/if}
					{#each settings.MCP_SERVERS as server, index (server.ID)}
						<div class="space-y-4 rounded-2xl border border-gray-200/70 p-4 dark:border-gray-800">
							<div class="flex items-center justify-between gap-3">
								<div class="text-sm font-medium">{server.NAME || `MCP Server ${index + 1}`}</div>
								<button type="button" class="text-xs text-red-500 hover:underline" on:click={() => removeMCPServer(server.ID)}>Remove</button>
							</div>
							<div class="grid gap-4 md:grid-cols-2">
								<label class="block"><span class="label">Name</span><input class="field" bind:value={server.NAME} placeholder={`MCP Server ${index + 1}`} /></label>
								<label class="block"><span class="label">Streamable HTTP URL</span><input class="field" bind:value={server.URL} on:input={() => scheduleMCPFetch(server.ID)} placeholder="http://mcp.internal/mcp" /></label>
							</div>
							<label class="block"><span class="label">Headers JSON</span><textarea class="field min-h-20 resize-y font-mono text-xs" bind:value={server.HEADERS} on:input={() => scheduleMCPFetch(server.ID)} spellcheck="false" placeholder={'{"Authorization":"Bearer ..."}'}></textarea></label>
							<div class="rounded-xl border border-gray-200/70 dark:border-gray-800">
								<div class="flex items-center justify-between border-b border-gray-200/70 px-3 py-2.5 dark:border-gray-800">
									<div>
										<div class="text-xs font-medium">Available tools</div>
										<div class="mt-0.5 text-[11px] text-gray-500">{fetchingTools[server.ID] ? 'Fetching tools…' : `${selectedTools(server.ID).size} of ${(mcpTools[server.ID] ?? []).length} selected`}</div>
									</div>
									<div class="flex gap-3 text-xs">
										<button type="button" class="underline underline-offset-4" on:click={() => setSelectedTools(server.ID, (mcpTools[server.ID] ?? []).map((tool) => tool.name))}>Select all</button>
										<button type="button" class="underline underline-offset-4" on:click={() => fetchMCPTools(server.ID)}>Refresh</button>
									</div>
								</div>
								{#if mcpToolsError[server.ID]}
									<div class="px-3 py-3 text-xs text-red-600 dark:text-red-400">{mcpToolsError[server.ID]}</div>
								{:else if !server.URL.trim()}
									<div class="px-3 py-3 text-xs text-gray-500">Enter an MCP URL to discover its tools automatically.</div>
								{:else if !fetchingTools[server.ID] && (mcpTools[server.ID] ?? []).length === 0}
									<div class="px-3 py-3 text-xs text-gray-500">No tools were returned by this server.</div>
								{:else}
									<div class="max-h-72 divide-y divide-gray-200/70 overflow-y-auto dark:divide-gray-800">
										{#each mcpTools[server.ID] ?? [] as tool}
											<label class="flex cursor-pointer gap-3 px-3 py-2.5 hover:bg-gray-50 dark:hover:bg-gray-900/50">
												<input type="checkbox" class="mt-0.5" checked={selectedTools(server.ID).has(tool.name)} on:change={() => toggleTool(server.ID, tool.name)} />
												<span class="min-w-0"><span class="block text-xs font-medium">{tool.name}</span>{#if tool.description}<span class="mt-0.5 block text-[11px] leading-4 text-gray-500">{tool.description}</span>{/if}</span>
											</label>
										{/each}
									</div>
								{/if}
							</div>
						</div>
					{/each}
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
