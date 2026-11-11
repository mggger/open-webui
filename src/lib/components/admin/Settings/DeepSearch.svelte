<script lang="ts">
	import { onMount, getContext } from 'svelte';
	import { getDeepSearchConfig, updateDeepSearchConfig } from '$lib/apis/deep-search';
	import Switch from '$lib/components/common/Switch.svelte';
	import SensitiveInput from '$lib/components/common/SensitiveInput.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';

	const i18n = getContext('i18n');

	export let saveHandler: Function;

	let config = null;

	const submitHandler = async () => {
		await updateDeepSearchConfig(localStorage.token, config);
	};

	onMount(async () => {
		const res = await getDeepSearchConfig(localStorage.token);
		if (res) {
			config = res;
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
	<div class=" space-y-3 overflow-y-scroll scrollbar-hidden h-full">
		{#if config}
			<div>
				<div class="mb-3.5">
					<div class=" mt-0.5 mb-2.5 text-base font-medium">{$i18n.t('General')}</div>

					<hr class=" border-gray-100/30 dark:border-gray-850/30 my-2" />

					<div class="mb-2.5">
						<div class=" flex w-full justify-between">
							<div class=" self-center text-xs font-medium">
								{$i18n.t('Enable Deep Search')}
							</div>

							<Switch bind:state={config.ENABLE_DEEP_SEARCH} />
						</div>
					</div>

					<div class="mb-2.5 flex flex-col gap-1.5 w-full">
						<div class="text-xs font-medium">
							{$i18n.t('SerpAPI Engine')}
						</div>

						<div class="flex w-full">
							<div class="flex-1">
								<input
									class="w-full text-sm py-0.5 placeholder:text-gray-300 dark:placeholder:text-gray-700 bg-transparent outline-hidden"
									type="text"
									placeholder={$i18n.t('Enter SerpAPI Engine (e.g. google)')}
									bind:value={config.SERPAPI_ENGINE}
									autocomplete="off"
								/>
							</div>
						</div>
					</div>

					<div class="mb-2.5 flex flex-col gap-1.5 w-full">
						<div class="text-xs font-medium">
							{$i18n.t('SerpAPI API Key')}
						</div>

						<div class="flex w-full">
							<div class="flex-1">
								<SensitiveInput
									type="text"
									placeholder={$i18n.t('Enter SerpAPI API Key')}
									bind:value={config.SERPAPI_API_KEY}
									autocomplete="off"
								/>
							</div>
						</div>
					</div>
				</div>

				<div class="mb-3.5">
					<div class=" mt-0.5 mb-2.5 text-base font-medium">{$i18n.t('Deep Search')}</div>

					<hr class=" border-gray-100/30 dark:border-gray-850/30 my-2" />

					<div class="flex gap-2 w-full items-center justify-between mb-2.5">
						<div class="text-xs font-medium">
							{$i18n.t('Deep Search Max Iterations')}
						</div>
						<Tooltip content={$i18n.t('Enter max iterative rounds (e.g. 3)')}>
							<input
								class="dark:bg-gray-900 w-fit rounded-sm px-2 p-1 text-xs bg-transparent outline-hidden text-right"
								type="number"
								min="1"
								bind:value={config.DEEP_SEARCH_MAX_ITERATIONS}
								placeholder={$i18n.t('e.g. 3')}
								autocomplete="off"
							/>
						</Tooltip>
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
