<script lang="ts">
	import { getContext, onMount } from 'svelte';
	import { toast } from 'svelte-sonner';

	import SensitiveInput from '$lib/components/common/SensitiveInput.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import {
		deleteFileSearchConfig,
		getFileSearchConfig,
		testFileSearchConnection,
		updateFileSearchConfig,
		type FileSearchConfig
	} from '$lib/apis/file-search';

	const i18n = getContext('i18n');

	let config: FileSearchConfig | null = null;
	let username = '';
	let password = '';
	let loading = true;
	let saving = false;
	let testing = false;

	const loadConfig = async () => {
		loading = true;
		try {
			config = await getFileSearchConfig(localStorage.token);
			username = config.username;
		} catch (error) {
			toast.error(String(error));
		} finally {
			loading = false;
		}
	};

	const save = async () => {
		saving = true;
		try {
			config = await updateFileSearchConfig(localStorage.token, {
				username: username.trim(),
				...(password ? { password } : {}),
				default_directory: config?.default_directory ?? ''
			});
			password = '';
			toast.success($i18n.t('File Search Agent connected'));
		} catch (error) {
			toast.error(String(error));
		} finally {
			saving = false;
		}
	};

	const testConnection = async () => {
		testing = true;
		try {
			await testFileSearchConnection(localStorage.token, {
				username: username.trim(),
				...(password ? { password } : {})
			});
			toast.success($i18n.t('Connection successful'));
		} catch (error) {
			toast.error(String(error));
		} finally {
			testing = false;
		}
	};

	const remove = async () => {
		if (!confirm($i18n.t('Remove the saved File Search Agent credentials?'))) return;
		try {
			await deleteFileSearchConfig(localStorage.token);
			config = await getFileSearchConfig(localStorage.token);
			username = '';
			password = '';
			toast.success($i18n.t('File Search Agent credentials removed'));
		} catch (error) {
			toast.error(String(error));
		}
	};

	onMount(loadConfig);
</script>

<form
	id="tab-file-search"
	class="flex h-full flex-col justify-between text-sm"
	on:submit|preventDefault={save}
>
	<div class="h-full overflow-y-auto pr-1.5 scrollbar-hidden">
		{#if loading}
			<div class="flex h-full items-center justify-center"><Spinner className="size-6" /></div>
		{:else if config}
			<div class="space-y-5">
				<div>
					<div class="font-medium">{$i18n.t('File Search Agent')}</div>
					<div class="mt-1 text-xs text-gray-500 dark:text-gray-400">
						{$i18n.t(
							'Connect your SMB account. Your password is encrypted on the server and is never returned to the browser.'
						)}
					</div>
				</div>

				<div class="grid grid-cols-1 gap-3">
					<label>
						<span class="mb-1 block text-xs text-gray-500">{$i18n.t('Username')}</span>
						<input
							class="w-full rounded-xl bg-gray-100 px-3 py-2 outline-hidden dark:bg-gray-850"
							bind:value={username}
							autocomplete="username"
							required
						/>
					</label>
					<label>
						<span class="mb-1 block text-xs text-gray-500">{$i18n.t('Password')}</span>
						<div class="rounded-xl bg-gray-100 px-3 py-2 dark:bg-gray-850">
							<SensitiveInput
								id="file-search-password"
								bind:value={password}
								type="password"
								required={!config.password_configured}
								placeholder={config.password_configured
									? $i18n.t('Leave blank to keep the saved password')
									: $i18n.t('Enter SMB password')}
							/>
						</div>
					</label>
				</div>
			</div>
		{/if}
	</div>

	<div class="flex justify-between gap-2 pt-3">
		{#if config?.configured}
			<button
				type="button"
				class="rounded-full px-3.5 py-1.5 text-sm text-red-600 hover:bg-red-50 dark:hover:bg-red-950/30"
				on:click={remove}
			>
				{$i18n.t('Remove')}
			</button>
		{:else}
			<div></div>
		{/if}
		<div class="flex gap-2">
			<button
				type="button"
				disabled={testing || saving || !username.trim()}
				class="rounded-full border border-gray-200 px-3.5 py-1.5 text-sm font-medium transition hover:bg-gray-50 disabled:opacity-50 dark:border-gray-700 dark:hover:bg-gray-800"
				on:click={testConnection}
			>
				{testing ? $i18n.t('Testing…') : $i18n.t('Test connection')}
			</button>
			<button
				type="submit"
				disabled={saving || testing || !username.trim()}
				class="rounded-full bg-black px-3.5 py-1.5 text-sm font-medium text-white transition disabled:opacity-50 dark:bg-white dark:text-black"
			>
				{saving ? $i18n.t('Connecting…') : $i18n.t('Save and connect')}
			</button>
		</div>
	</div>
</form>
