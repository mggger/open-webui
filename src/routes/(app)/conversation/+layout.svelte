<script lang="ts">
	import { onMount, getContext } from 'svelte';
	import { WEBUI_NAME } from '$lib/stores';

	const i18n = getContext('i18n');

	let loaded = false;

	onMount(() => {
		loaded = true;
	});
</script>

<svelte:head>
	<title>
		{$i18n.t('Conversation Agent')} • {$WEBUI_NAME}
	</title>
</svelte:head>

{#if loaded}
	<slot />
{/if}
