<script lang="ts">
	import { onMount, getContext } from 'svelte';
	import { toast } from 'svelte-sonner';
	import {
		getManageEngineConfig,
		updateManageEngineConfig,
		testManageEngineConfig,
		type ManageEngineConfig
	} from '$lib/apis/manage-engine';
	import Switch from '$lib/components/common/Switch.svelte';
	import SensitiveInput from '$lib/components/common/SensitiveInput.svelte';
	const i18n = getContext('i18n') as any;
	export let saveHandler: Function;
	let config: ManageEngineConfig | null = null;
	let apiKey = '';
	let busy = false;
	onMount(async () => {
		try {
			config = await getManageEngineConfig(localStorage.token);
		} catch (e) {
			toast.error(String(e));
		}
	});
	async function save() {
		if (!config || busy) return;
		busy = true;
		try {
			const { MANAGE_ENGINE_API_KEY_SET, ...values } = config;
			config = await updateManageEngineConfig(localStorage.token, {
				...values,
				...(apiKey.trim() ? { MANAGE_ENGINE_API_KEY: apiKey.trim() } : {})
			});
			apiKey = '';
			await saveHandler();
		} catch (e) {
			toast.error(String(e));
		} finally {
			busy = false;
		}
	}
	async function testConnection() {
		if (!config || busy) return;
		busy = true;
		try {
			const result = await testManageEngineConfig(localStorage.token, {
				MANAGE_ENGINE_ENDPOINT: config.MANAGE_ENGINE_ENDPOINT,
				MANAGE_ENGINE_VERIFY_SSL: config.MANAGE_ENGINE_VERIFY_SSL,
				...(apiKey.trim() ? { MANAGE_ENGINE_API_KEY: apiKey.trim() } : {})
			});
			if (result.ok) toast.success('Connection successful');
			else toast.error(result.error || 'Connection failed');
		} catch (e) {
			toast.error(String(e));
		} finally {
			busy = false;
		}
	}
</script>

<form
	class="flex flex-col h-full justify-between gap-4 text-sm"
	on:submit|preventDefault={() => save()}
>
	<div class="space-y-4 overflow-y-auto">
		<h2 class="font-medium text-base">Manage Engine</h2>
		{#if config}
			<label class="block"
				>{$i18n.t('API Endpoint')}<input
					class="w-full p-2 bg-transparent border rounded-lg"
					type="url"
					placeholder="https://sdp.example.com:8080"
					bind:value={config.MANAGE_ENGINE_ENDPOINT}
				/></label
			>
			<label class="block"
				>{$i18n.t('API Key')}<SensitiveInput
					type="password"
					bind:value={apiKey}
					placeholder={config.MANAGE_ENGINE_API_KEY_SET
						? $i18n.t('(saved — leave blank to keep)')
						: 'authtoken'}
				/></label
			>
			<label class="block"
				>{$i18n.t('Timezone')}<input
					class="w-full p-2 bg-transparent border rounded-lg"
					required
					bind:value={config.MANAGE_ENGINE_TIMEZONE}
					placeholder="Asia/Shanghai"
				/></label
			>
			<div class="flex justify-between">
				{$i18n.t('Verify SSL Certificate')}<Switch bind:state={config.MANAGE_ENGINE_VERIFY_SSL} />
			</div>
			<div class="flex justify-between">
				{$i18n.t('Admins only')}<Switch bind:state={config.MANAGE_ENGINE_ADMIN_ONLY} />
			</div>
			<p class="text-xs text-gray-500">
				{$i18n.t(
					'Enable Manage Engine in the chat + menu to query tickets using your configured model.'
				)}
			</p>
			<button
				type="button"
				class="border rounded-lg px-3 py-2 disabled:opacity-50"
				disabled={busy}
				on:click={testConnection}>{$i18n.t('Test Connection')}</button
			>
		{/if}
	</div>
	<div class="flex justify-end">
		<button
			type="submit"
			disabled={busy || !config}
			class="px-4 py-2 rounded-full bg-black text-white dark:bg-white dark:text-black disabled:opacity-50"
			>{$i18n.t('Save')}</button
		>
	</div>
</form>
