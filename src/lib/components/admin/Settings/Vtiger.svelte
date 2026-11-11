<script lang="ts">
	import { onMount, getContext } from 'svelte';
	import { toast } from 'svelte-sonner';
	import {
		getVtigerConfig,
		updateVtigerConfig,
		testVtigerConfig,
		type VtigerConfig
	} from '$lib/apis/vtiger';
	import Switch from '$lib/components/common/Switch.svelte';
	import SensitiveInput from '$lib/components/common/SensitiveInput.svelte';

	const i18n = getContext('i18n') as any;

	export let saveHandler: Function;

	let config: VtigerConfig | null = null;

	// Access key is write-only from the API's perspective: GET returns only
	// VTIGER_ACCESS_KEY_SET (boolean). We send VTIGER_ACCESS_KEY in the update
	// payload only if the admin actually typed a new value here.
	let accessKeyInput = '';
	let testing = false;

	const submitHandler = async () => {
		if (!config) return;
		const payload: Record<string, any> = {
			ENABLE_VTIGER_CRM: config.ENABLE_VTIGER_CRM,
			VTIGER_BASE_URL: config.VTIGER_BASE_URL,
			VTIGER_USERNAME: config.VTIGER_USERNAME,
			VTIGER_VERIFY_SSL: config.VTIGER_VERIFY_SSL
		};
		if (accessKeyInput.trim()) {
			payload.VTIGER_ACCESS_KEY = accessKeyInput.trim();
		}
		const updated = await updateVtigerConfig(localStorage.token, payload);
		config = updated;
		accessKeyInput = '';
	};

	const runTest = async () => {
		if (testing) return;
		testing = true;
		try {
			// Save current edits first so the test uses the latest values.
			await submitHandler();
			const res = await testVtigerConfig(localStorage.token);
			if (res.ok) {
				toast.success($i18n.t('Vtiger connection successful.'));
			} else {
				toast.error(res.error || $i18n.t('Vtiger connection failed.'));
			}
		} catch (e: any) {
			toast.error(e?.detail ?? e?.message ?? String(e));
		} finally {
			testing = false;
		}
	};

	onMount(async () => {
		try {
			const res = await getVtigerConfig(localStorage.token);
			if (res) config = res;
		} catch (e: any) {
			toast.error(e?.detail ?? e?.message ?? String(e));
		}
	});
</script>

<form
	class="flex flex-col h-full justify-between space-y-3 text-sm"
	on:submit|preventDefault={async () => {
		await submitHandler();
		saveHandler();
	}}
>
	<div class="space-y-3 overflow-y-scroll scrollbar-hidden h-full">
		{#if config}
			<div>
				<div class="mb-3.5">
					<div class="mt-0.5 mb-2.5 text-base font-medium">{$i18n.t('Vtiger CRM')}</div>

					<hr class="border-gray-100/30 dark:border-gray-850/30 my-2" />

					<div class="mb-2.5">
						<div class="flex w-full justify-between">
							<div class="self-center text-xs font-medium">
								{$i18n.t('Enable Vtiger CRM Integration')}
							</div>
							<Switch bind:state={config.ENABLE_VTIGER_CRM} />
						</div>
					</div>

					<div class="mb-2.5 flex flex-col gap-1.5 w-full">
						<div class="text-xs font-medium">
							{$i18n.t('Vtiger Base URL')}
						</div>
						<div class="flex w-full">
							<input
								class="w-full text-sm py-0.5 placeholder:text-gray-300 dark:placeholder:text-gray-700 bg-transparent outline-hidden"
								type="text"
								placeholder={$i18n.t('e.g. https://crm.example.com')}
								bind:value={config.VTIGER_BASE_URL}
								autocomplete="off"
							/>
						</div>
					</div>

					<div class="mb-2.5 flex flex-col gap-1.5 w-full">
						<div class="text-xs font-medium">
							{$i18n.t('Vtiger Username')}
						</div>
						<div class="flex w-full">
							<input
								class="w-full text-sm py-0.5 placeholder:text-gray-300 dark:placeholder:text-gray-700 bg-transparent outline-hidden"
								type="text"
								placeholder={$i18n.t('e.g. admin')}
								bind:value={config.VTIGER_USERNAME}
								autocomplete="off"
							/>
						</div>
					</div>

					<div class="mb-2.5 flex flex-col gap-1.5 w-full">
						<div class="text-xs font-medium flex items-center gap-2">
							{$i18n.t('Vtiger Access Key')}
							{#if config.VTIGER_ACCESS_KEY_SET}
								<span class="text-[10px] text-gray-400 font-normal">
									{$i18n.t('(saved — leave blank to keep)')}
								</span>
							{/if}
						</div>
						<div class="flex w-full">
							<div class="flex-1">
								<SensitiveInput
									type="text"
									placeholder={config.VTIGER_ACCESS_KEY_SET
										? $i18n.t('Enter a new key to replace the saved one')
										: $i18n.t('Enter Vtiger Access Key')}
									bind:value={accessKeyInput}
								/>
							</div>
						</div>
					</div>

					<div class="mb-2.5">
						<div class="flex w-full justify-between">
							<div class="self-center text-xs font-medium">
								{$i18n.t('Verify SSL Certificate')}
							</div>
							<Switch bind:state={config.VTIGER_VERIFY_SSL} />
						</div>
					</div>

					<div class="mb-2.5 pt-2">
						<button
							type="button"
							class="px-3 py-1.5 rounded-lg text-xs border border-gray-200 dark:border-gray-800 hover:bg-gray-50 dark:hover:bg-gray-900 transition disabled:opacity-50"
							on:click={runTest}
							disabled={testing}
						>
							{testing ? $i18n.t('Testing...') : $i18n.t('Save & Test Connection')}
						</button>
					</div>
				</div>
			</div>
		{/if}
	</div>
	<div class="flex justify-end pt-3 text-sm font-medium">
		<button
			class="px-3.5 py-1.5 text-sm font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full"
			type="submit"
		>
			{$i18n.t('Save')}
		</button>
	</div>
</form>
