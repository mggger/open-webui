<script lang="ts">
	import { getContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';
	import { toast } from 'svelte-sonner';
	import { user } from '$lib/stores';
	import { WEBUI_BASE_URL } from '$lib/constants';
	import { downloadArcherLetter, type ArcherLetterForm } from '$lib/apis/conversation-agents';
	import Modal from '$lib/components/common/Modal.svelte';
	import { getUserSettings, updateUserSettings } from '$lib/apis/users';

	const i18n = getContext<Writable<i18nType>>('i18n');

	export let show = false;
	export let messages: { role: string; content: string }[] = [];
	export let model = '';

	let extracting = false;
	let downloading = false;
	let savingProfile = false;
	let profileLoadedFor = '';
	let extractedFor = '';
	let savedSender = { name: '', title: '' };
	let currentUserSettings: Record<string, unknown> = {};
	let fields: ArcherLetterForm = emptyFields();

	const fieldDefinitions: { key: keyof ArcherLetterForm; label: string; multiline?: boolean }[] = [
		{ key: 'date', label: 'Date' },
		{ key: 'recipient_name', label: 'Recipient name' },
		{ key: 'recipient_title_company', label: 'Title / company' },
		{ key: 'street_address', label: 'Street address' },
		{ key: 'city_state_postcode', label: 'City, state, postcode' },
		{ key: 'opening_paragraph', label: 'Opening paragraph', multiline: true },
		{ key: 'body_paragraph', label: 'Body paragraph', multiline: true },
		{ key: 'closing_paragraph', label: 'Closing paragraph', multiline: true },
		{ key: 'sender_name', label: 'Your name' },
		{ key: 'sender_title', label: 'Your title' }
	];

	function emptyFields(): ArcherLetterForm {
		return {
			date: '',
			recipient_name: '',
			recipient_title_company: '',
			street_address: '',
			city_state_postcode: '',
			opening_paragraph: '',
			body_paragraph: '',
			closing_paragraph: '',
			sender_name: '',
			sender_title: ''
		};
	}

	const parseJson = (raw: string): Record<string, unknown> => {
		const cleaned = raw
			.replace(/^```(?:json)?\s*/i, '')
			.replace(/\s*```$/i, '')
			.trim();
		const start = cleaned.indexOf('{');
		const end = cleaned.lastIndexOf('}');
		if (start < 0 || end < start) throw new Error('No JSON object returned');
		return JSON.parse(cleaned.slice(start, end + 1));
	};

	const complete = async (prompt: string): Promise<string> => {
		const response = await fetch(`${WEBUI_BASE_URL}/api/chat/completions`, {
			method: 'POST',
			headers: {
				Authorization: `Bearer ${localStorage.token}`,
				'Content-Type': 'application/json'
			},
			credentials: 'include',
			body: JSON.stringify({
				model,
				messages: [{ role: 'user', content: prompt }],
				stream: false,
				max_tokens: 6000
			})
		});
		if (!response.ok) throw new Error(`HTTP ${response.status}`);
		const result = await response.json();
		return String(result?.choices?.[0]?.message?.content ?? '').trim();
	};

	const buildTranscriptChunks = (source: { role: string; content: string }[], limit = 12000) => {
		const parts: string[] = [];
		for (const message of source) {
			if (!['user', 'assistant'].includes(message.role) || !message.content) continue;
			const prefix = `${message.role === 'user' ? 'User' : 'Assistant'}: `;
			for (let start = 0; start < message.content.length; start += limit - prefix.length) {
				parts.push(prefix + message.content.slice(start, start + limit - prefix.length));
			}
		}

		const chunks: string[] = [];
		let current = '';
		for (const part of parts) {
			if (current && current.length + part.length + 2 > limit) {
				chunks.push(current);
				current = part;
			} else {
				current += `${current ? '\n\n' : ''}${part}`;
			}
		}
		if (current) chunks.push(current);
		return chunks;
	};

	const consolidateEvidence = async (items: string[]): Promise<string> => {
		let level = items;
		while (level.join('\n\n').length > 30000) {
			const previousLength = level.join('\n\n').length;
			const next: string[] = [];
			let batch: string[] = [];
			let batchLength = 0;
			for (const item of level) {
				if (batch.length && batchLength + item.length > 24000) {
					next.push(
						await complete(
							`Merge these evidence ledgers without dropping any concrete fact, concern, request, decision, commitment, amount, date, rationale, unresolved issue, attribution, or next action. Remove only exact duplicates. Output concise plain text bullets.\n\n${batch.join('\n\n')}`
						)
					);
					batch = [];
					batchLength = 0;
				}
				batch.push(item);
				batchLength += item.length;
			}
			if (batch.length) {
				next.push(
					await complete(
						`Merge these evidence ledgers without dropping any concrete fact, concern, request, decision, commitment, amount, date, rationale, unresolved issue, attribution, or next action. Remove only exact duplicates. Output concise plain text bullets.\n\n${batch.join('\n\n')}`
					)
				);
			}
			level = next;
			if (level.join('\n\n').length >= previousLength) break;
		}
		return level.join('\n\n');
	};

	const extract = async () => {
		if (extracting || !model || messages.length === 0) return;
		extracting = true;
		fields = emptyFields();
		const today = new Intl.DateTimeFormat(undefined, {
			year: 'numeric',
			month: 'long',
			day: 'numeric'
		}).format(new Date());
		const chunks = buildTranscriptChunks(messages);
		const evidence: string[] = [];
		const extractionPrompt = (
			chunk: string,
			index: number
		) => `You are processing part ${index + 1} of ${chunks.length} of one conversation thread.
Extract an exhaustive evidence ledger from ONLY this part. Preserve every concrete member/customer fact, concern, request, decision, commitment, amount, date, rationale, unresolved issue, and next action. Attribute who said or agreed to each item. Do not summarize away details and do not invent anything. Output concise plain text bullets.

Thread part ${index + 1}:
${chunk}`;

		const prompt = (
			ledger: string
		) => `Create data for a professional follow-up business letter from the complete evidence ledger below.
Return ONLY a valid JSON object with exactly these string keys: date, recipient_name, recipient_title_company, street_address, city_state_postcode, opening_paragraph, body_paragraph, closing_paragraph, sender_name, sender_title.

Rules:
- The ledger was built from every part of the selected thread. Use all relevant ledger entries; never silently omit a concrete concern, decision, commitment, amount, date, unresolved issue, or next action.
- Never invent identity, address, title, company, promises, amounts, dates, or outcomes; use an empty string if unknown.
- opening_paragraph briefly states purpose and context.
- body_paragraph must be comprehensive, with enough prose to cover ALL relevant details from the entire ledger. It may contain multiple paragraphs separated by newlines. Do not impose a word limit or shorten it merely for concision.
- closing_paragraph contains the complete next step and call to action.
- Do not include greetings, sign-offs, markdown, or placeholders in paragraph fields.
- Use ${JSON.stringify(today)} for date.
- The sender is the logged-in user ${JSON.stringify($user?.name ?? '')}, not the assistant.

Complete evidence ledger:
${ledger}`;
		try {
			for (let index = 0; index < chunks.length; index++) {
				evidence.push(await complete(extractionPrompt(chunks[index], index)));
			}
			const ledger = await consolidateEvidence(evidence);
			const extracted = parseJson(await complete(prompt(ledger)));
			fields = Object.fromEntries(
				fieldDefinitions.map(({ key }) => [key, String(extracted[key] ?? '').trim()])
			) as ArcherLetterForm;
			fields.date ||= today;
			fields.sender_name = savedSender.name || fields.sender_name || $user?.name || '';
			fields.sender_title = savedSender.title || fields.sender_title;
			extractedFor = `${model}:${messages.length}:${messages.at(-1)?.content ?? ''}`;
		} catch (error) {
			console.error(error);
			fields.date = today;
			fields.sender_name = savedSender.name || $user?.name || '';
			fields.sender_title = savedSender.title;
			toast.error($i18n.t('Could not extract letter details. Please fill them manually.'));
		} finally {
			extracting = false;
		}
	};

	const loadSavedSender = async () => {
		const userId = $user?.id ?? '';
		if (!userId || profileLoadedFor === userId) return;
		profileLoadedFor = userId;
		try {
			const settings = await getUserSettings(localStorage.token);
			currentUserSettings = settings ?? {};
			const profile = settings?.letter_profile ?? {};
			savedSender = {
				name: String(profile?.sender_name ?? '').trim(),
				title: String(profile?.sender_title ?? '').trim()
			};
		} catch (error) {
			console.error('Could not load saved letter information', error);
		}
	};

	const rememberSender = async () => {
		if (!fields.sender_name.trim() || !fields.sender_title.trim()) return;
		savingProfile = true;
		try {
			await updateUserSettings(localStorage.token, {
				...currentUserSettings,
				letter_profile: {
					sender_name: fields.sender_name.trim(),
					sender_title: fields.sender_title.trim()
				}
			});
			savedSender = { name: fields.sender_name.trim(), title: fields.sender_title.trim() };
			currentUserSettings = {
				...currentUserSettings,
				letter_profile: {
					sender_name: savedSender.name,
					sender_title: savedSender.title
				}
			};
			toast.success($i18n.t('Your letter information has been remembered'));
		} catch (error) {
			console.error(error);
			toast.error($i18n.t('Could not remember your information'));
		} finally {
			savingProfile = false;
		}
	};

	$: if (show) {
		const signature = `${model}:${messages.length}:${messages.at(-1)?.content ?? ''}`;
		(async () => {
			await loadSavedSender();
			if (signature !== extractedFor && !extracting) await extract();
		})();
	}

	const download = async () => {
		downloading = true;
		try {
			const blob = await downloadArcherLetter(localStorage.token, fields);
			const url = URL.createObjectURL(blob);
			const anchor = document.createElement('a');
			anchor.href = url;
			anchor.download = `Archer-Letter-${fields.recipient_name || 'recipient'}.docx`;
			anchor.click();
			URL.revokeObjectURL(url);
			show = false;
		} catch (error) {
			console.error(error);
			toast.error($i18n.t('Could not generate letter'));
		} finally {
			downloading = false;
		}
	};
</script>

<Modal bind:show size="lg">
	<div class="px-5 py-4 border-b border-gray-200 dark:border-gray-800">
		<div class="text-base font-semibold">{$i18n.t('Download letter')}</div>
		<div class="text-xs text-gray-500 mt-1">
			{$i18n.t('Details are extracted from this conversation only. Complete any missing fields.')}
		</div>
	</div>
	<div class="max-h-[70vh] overflow-y-auto px-5 py-4">
		{#if extracting}
			<div class="py-8 text-sm text-gray-500 text-center">
				{$i18n.t('Extracting details from the conversation...')}
			</div>
		{:else}
			<div class="grid grid-cols-1 md:grid-cols-2 gap-3">
				{#each fieldDefinitions.filter((field) => !field.multiline) as field}
					<div>
						<label
							class="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1"
							for={`chat-letter-${field.key}`}
						>
							{$i18n.t(field.label)}{#if !fields[field.key].trim()}<span class="text-red-500 ml-1"
									>*</span
								>{/if}
						</label>
						<input
							id={`chat-letter-${field.key}`}
							class="w-full px-3 py-2 rounded-lg border border-gray-200 dark:border-gray-800 bg-transparent text-sm"
							bind:value={fields[field.key]}
						/>
					</div>
				{/each}
			</div>
			<div
				class="mt-3 flex items-center justify-between gap-3 rounded-lg bg-gray-50 dark:bg-gray-850 px-3 py-2"
			>
				<div class="text-xs text-gray-500">
					{$i18n.t('Save your name and title for your next letter.')}
				</div>
				<button
					type="button"
					class="shrink-0 px-3 py-1.5 rounded-lg text-xs border border-gray-200 dark:border-gray-700 hover:bg-white dark:hover:bg-gray-800 disabled:opacity-50"
					disabled={savingProfile || !fields.sender_name.trim() || !fields.sender_title.trim()}
					on:click={rememberSender}
				>
					{savingProfile ? $i18n.t('Saving...') : $i18n.t('Remember my information')}
				</button>
			</div>
		{/if}
	</div>
	<div class="px-5 py-3 border-t border-gray-200 dark:border-gray-800 flex justify-end gap-2">
		<button
			class="px-3 py-1.5 rounded-lg text-sm border border-gray-200 dark:border-gray-800"
			on:click={() => (show = false)}>{$i18n.t('Cancel')}</button
		>
		<button
			class="px-4 py-1.5 rounded-lg text-sm bg-gray-900 text-white dark:bg-gray-100 dark:text-gray-900 disabled:opacity-50"
			disabled={extracting ||
				downloading ||
				fieldDefinitions.some(({ key }) => !fields[key].trim())}
			on:click={download}
		>
			{downloading ? $i18n.t('Generating...') : $i18n.t('Download letter')}
		</button>
	</div>
</Modal>
