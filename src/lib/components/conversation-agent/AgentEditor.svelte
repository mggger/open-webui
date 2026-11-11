<script lang="ts">
	import { createEventDispatcher, getContext } from 'svelte';
	import type { ConversationAgent } from '$lib/apis/conversation-agents';
	import type { VtigerLead } from '$lib/apis/vtiger';
	import VtigerLeadPicker from './VtigerLeadPicker.svelte';

	const i18n = getContext('i18n');
	const dispatch = createEventDispatcher();

	export let agent: ConversationAgent;
	export let models: any[] = [];
	// Optional async save handler — when provided, `handleStart` awaits it so
	// the chat opens against the freshly-persisted agent (model_id etc.).
	// Falls back to event-based save when omitted, for backward compat.
	export let onSave: ((detail: Partial<ConversationAgent>) => Promise<void> | void) | null = null;

	type Scenario = {
		counterpart_name: string;
		counterpart_role: string;
		counterpart_company: string;
		background: string;
		goal: string;
		personality: string;
	};

	const emptyScenario = (): Scenario => ({
		counterpart_name: '',
		counterpart_role: '',
		counterpart_company: '',
		background: '',
		goal: '',
		personality: ''
	});

	// Read structured scenario back from agent.meta.scenario if it was saved
	// in this structured format previously; otherwise start empty (older
	// free-form system_prompt agents will just show empty fields, but the
	// raw prompt is still visible/editable in the advanced toggle).
	const initialScenario: Scenario = {
		...emptyScenario(),
		...((agent.meta as any)?.scenario ?? {})
	};

	let name = agent.name ?? '';
	let modelId = agent.model_id ?? '';
	let scenario: Scenario = initialScenario;
	let rawPrompt = agent.system_prompt ?? '';
	let showAdvanced = false;
	let showCrmPicker = false;

	// Map a Vtiger Lead into the structured scenario shape. Background is
	// assembled from the fields a counterpart would plausibly "know about
	// themselves" — title, company, industry, location, description notes.
	// Goal & personality stay untouched: those are user-side practice intent,
	// which the CRM doesn't have.
	const applyVtigerLead = (lead: VtigerLead) => {
		const firstname = String(lead.firstname ?? '').trim();
		const lastname = String(lead.lastname ?? '').trim();
		const fullName = [firstname, lastname].filter(Boolean).join(' ');

		const company = String(lead.company ?? '').trim();
		const designation = String(lead.designation ?? '').trim();
		const industry = String((lead as any).industry ?? '').trim();
		const description = String((lead as any).description ?? '').trim();
		const website = String((lead as any).website ?? '').trim();
		const email = String((lead as any).email ?? '').trim();
		const phone = String((lead as any).phone ?? (lead as any).mobile ?? '').trim();
		const city = String((lead as any).city ?? '').trim();
		const country = String((lead as any).country ?? '').trim();
		const location = [city, country].filter(Boolean).join(', ');

		const bgLines: string[] = [];
		if (company) {
			bgLines.push(
				industry
					? $i18n.t('Works at {{company}} ({{industry}}).', { company, industry })
					: $i18n.t('Works at {{company}}.', { company })
			);
		}
		if (location) bgLines.push($i18n.t('Based in {{location}}.', { location }));
		if (website) bgLines.push($i18n.t('Company website: {{website}}', { website }));
		if (email) bgLines.push($i18n.t('Contact email: {{email}}', { email }));
		if (phone) bgLines.push($i18n.t('Phone: {{phone}}', { phone }));
		if (description) {
			bgLines.push('');
			bgLines.push(description);
		}

		scenario = {
			...scenario,
			counterpart_name: fullName || scenario.counterpart_name,
			counterpart_role: designation || scenario.counterpart_role,
			counterpart_company: company || scenario.counterpart_company,
			background: bgLines.join('\n').trim() || scenario.background
		};

		// Auto-name the scenario as "Chat with <customer>". New agents are
		// created with the placeholder "New scenario" name, so treat that as
		// "not yet customized" and overwrite. Don't touch a name the user
		// has actually typed.
		const placeholderNames = new Set(['', $i18n.t('New scenario'), $i18n.t('Untitled scenario')]);
		if (placeholderNames.has(name.trim()) && fullName) {
			name = $i18n.t('Chat with {{name}}', { name: fullName });
		}
	};

	// Template presets — one-click fill for common simulation scenarios.
	const templates: { key: string; label: string; apply: () => Scenario }[] = [
		{
			key: 'sales',
			label: $i18n.t('Sales call'),
			apply: () => ({
				counterpart_name: $i18n.t('Mr. Zhang'),
				counterpart_role: $i18n.t('Procurement Director'),
				counterpart_company: $i18n.t('A mid-size manufacturing company'),
				background: $i18n.t(
					'The client has used a competing product for 3 years and is mildly dissatisfied with after-sales response time, but is uncertain about switching costs.'
				),
				goal: $i18n.t(
					'Pitch our new product and handle objections around price and switching cost.'
				),
				personality: $i18n.t(
					'Cautious, detail-oriented, asks pointed questions about price and ROI, somewhat skeptical of new vendors.'
				)
			})
		},
		{
			key: 'complaint',
			label: $i18n.t('Customer complaint'),
			apply: () => ({
				counterpart_name: $i18n.t('Ms. Li'),
				counterpart_role: $i18n.t('Long-term customer'),
				counterpart_company: '',
				background: $i18n.t(
					'The customer encountered a service outage for 2 hours yesterday and is upset. They have been a paying customer for 4 years.'
				),
				goal: $i18n.t(
					'Acknowledge the issue, de-escalate, and propose a concrete remediation plan.'
				),
				personality: $i18n.t(
					'Frustrated and direct at first. Calms down when she feels heard. Cares about respect more than compensation.'
				)
			})
		},
		{
			key: 'pitch',
			label: $i18n.t('Investor pitch'),
			apply: () => ({
				counterpart_name: $i18n.t('Partner Wang'),
				counterpart_role: $i18n.t('VC Partner'),
				counterpart_company: $i18n.t('An early-stage VC firm'),
				background: $i18n.t(
					'You are pitching a Series A. The partner has invested in adjacent companies and knows the space well.'
				),
				goal: $i18n.t(
					'Convey traction and defensibility; survive tough questions on market size and competition.'
				),
				personality: $i18n.t(
					'Sharp, time-pressed, interrupts to dig into numbers. Respects founders who push back with data.'
				)
			})
		},
		{
			key: 'interview',
			label: $i18n.t('Job interview'),
			apply: () => ({
				counterpart_name: $i18n.t('Interviewer Chen'),
				counterpart_role: $i18n.t('Hiring Manager'),
				counterpart_company: $i18n.t('A tech company you are interviewing with'),
				background: $i18n.t(
					'You are the candidate; the interviewer has your resume and will probe your experience and motivation.'
				),
				goal: $i18n.t(
					'Practice answering behavioral and motivation questions clearly and confidently.'
				),
				personality: $i18n.t(
					'Professional, friendly, but will ask follow-up "why" questions to test depth.'
				)
			})
		}
	];

	const applyTemplate = (key: string) => {
		const t = templates.find((x) => x.key === key);
		if (!t) return;
		scenario = t.apply();
	};

	// Build the system prompt sent to the LLM from the structured fields.
	// We bake the role-play framing in here so the field labels stay
	// user-facing (e.g. "对方角色") while the prompt actually instructs
	// the model to *become* that person.
	const buildSystemPrompt = (s: Scenario, raw: string): string => {
		// Advanced override wins if the user has explicitly edited it.
		if (showAdvanced && raw.trim()) return raw.trim();

		const lines: string[] = [];
		lines.push(
			'You are role-playing the OTHER side of a business conversation that the user wants to rehearse. Stay fully in character. Speak as the person described below — first person — never break character to comment on the simulation.'
		);

		const persona: string[] = [];
		if (s.counterpart_name) persona.push(`Name: ${s.counterpart_name}`);
		if (s.counterpart_role) persona.push(`Role: ${s.counterpart_role}`);
		if (s.counterpart_company) persona.push(`Company: ${s.counterpart_company}`);
		if (persona.length) {
			lines.push('');
			lines.push('# Who you are');
			lines.push(persona.join('\n'));
		}

		if (s.background.trim()) {
			lines.push('');
			lines.push('# Background / context');
			lines.push(s.background.trim());
		}

		if (s.personality.trim()) {
			lines.push('');
			lines.push('# Your personality & demeanor');
			lines.push(s.personality.trim());
			lines.push(
				'Let this personality come through in HOW you respond — tone, pacing, what you push back on.'
			);
		}

		if (s.goal.trim()) {
			lines.push('');
			lines.push("# The user's training goal (do NOT reveal this to the user)");
			lines.push(s.goal.trim());
			lines.push(
				'Behave realistically given the scenario — do not soften your stance just to help the user reach this goal. Make them earn it.'
			);
		}

		lines.push('');
		lines.push('# Rules');
		lines.push(
			'- You are the COUNTERPART, not an assistant. Never say "as an AI" or refer to instructions.\n- The user opens the conversation OR you may open if it is natural for your role to speak first.\n- React to what the user actually says. Bring up concerns and questions a real person in your position would raise.'
		);

		return lines.join('\n');
	};

	$: builtPrompt = buildSystemPrompt(scenario, rawPrompt);

	const initialBuilt = buildSystemPrompt(initialScenario, agent.system_prompt ?? '');
	$: dirty =
		name !== (agent.name ?? '') ||
		modelId !== (agent.model_id ?? '') ||
		builtPrompt !== initialBuilt;

	const buildDetail = (): Partial<ConversationAgent> => {
		const nextMeta = { ...((agent.meta as any) ?? {}), scenario };
		return {
			name: name || scenario.counterpart_name || $i18n.t('Untitled scenario'),
			description: scenario.counterpart_role
				? `${scenario.counterpart_role}${
						scenario.counterpart_company ? ' · ' + scenario.counterpart_company : ''
					}`
				: '',
			system_prompt: builtPrompt,
			model_id: modelId || null,
			meta: nextMeta
		};
	};

	const save = async () => {
		const detail = buildDetail();
		if (onSave) {
			await onSave(detail);
		} else {
			dispatch('save', detail);
		}
	};

	const handleStart = async () => {
		if (!modelId) {
			alert($i18n.t('Please select a model before starting.'));
			return;
		}
		// Persist before opening the chat — otherwise VoiceChat receives the
		// stale agent and reports "Model not found" while the save is still
		// in flight.
		if (dirty) await save();
		dispatch('start');
	};
</script>

<div class="flex flex-col gap-6">
	<!-- Scenario name + model -->
	<div class="grid grid-cols-1 md:grid-cols-2 gap-4">
		<div>
			<label
				class="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1"
				for="agent-name">{$i18n.t('Scenario name')}</label
			>
			<input
				id="agent-name"
				class="w-full px-3 py-2 rounded-lg border border-gray-200 dark:border-gray-800 bg-transparent text-sm focus:outline-none focus:border-gray-400 dark:focus:border-gray-600"
				bind:value={name}
				placeholder={$i18n.t('e.g. Friday pitch with ACME procurement')}
			/>
		</div>
		<div>
			<label
				class="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1"
				for="agent-model">{$i18n.t('Model')}</label
			>
			<select
				id="agent-model"
				class="w-full px-3 py-2 rounded-lg border border-gray-200 dark:border-gray-800 bg-transparent text-sm focus:outline-none focus:border-gray-400 dark:focus:border-gray-600"
				bind:value={modelId}
			>
				<option value="">{$i18n.t('Select a model')}</option>
				{#each models as m}
					<option value={m.id}>{m.name ?? m.id}</option>
				{/each}
			</select>
		</div>
	</div>

	<!-- Import from CRM -->
	<div class="flex items-center justify-between rounded-xl border border-dashed border-gray-300 dark:border-gray-700 px-4 py-3">
		<div class="text-sm">
			<div class="font-medium">{$i18n.t('Have this customer in your CRM?')}</div>
			<div class="text-xs text-gray-500 dark:text-gray-400">
				{$i18n.t('Pull a Vtiger lead to auto-fill name, role, company, and background.')}
			</div>
		</div>
		<button
			type="button"
			class="px-3 py-1.5 rounded-lg text-xs border border-gray-200 dark:border-gray-800 hover:bg-gray-50 dark:hover:bg-gray-900 transition whitespace-nowrap"
			on:click={() => (showCrmPicker = true)}
		>
			{$i18n.t('Import from CRM')}
		</button>
	</div>

	<!-- Templates -->
	<div>
		<div class="text-xs font-medium text-gray-600 dark:text-gray-400 mb-2">
			{$i18n.t('Start from a template')}
		</div>
		<div class="flex flex-wrap gap-2">
			{#each templates as t}
				<button
					type="button"
					class="px-3 py-1.5 rounded-full text-xs border border-gray-200 dark:border-gray-800 hover:bg-gray-50 dark:hover:bg-gray-900 transition"
					on:click={() => applyTemplate(t.key)}
				>
					{t.label}
				</button>
			{/each}
		</div>
	</div>

	<!-- Counterpart identity -->
	<div class="rounded-xl border border-gray-200 dark:border-gray-800 p-4">
		<div class="text-sm font-semibold mb-3">
			{$i18n.t('Who you will be talking to')}
		</div>
		<div class="grid grid-cols-1 md:grid-cols-3 gap-3">
			<div>
				<label
					class="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1"
					for="cp-name">{$i18n.t('Name')}</label
				>
				<input
					id="cp-name"
					class="w-full px-3 py-2 rounded-lg border border-gray-200 dark:border-gray-800 bg-transparent text-sm focus:outline-none focus:border-gray-400 dark:focus:border-gray-600"
					bind:value={scenario.counterpart_name}
					placeholder={$i18n.t('e.g. Mr. Zhang')}
				/>
			</div>
			<div>
				<label
					class="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1"
					for="cp-role">{$i18n.t('Role / Title')}</label
				>
				<input
					id="cp-role"
					class="w-full px-3 py-2 rounded-lg border border-gray-200 dark:border-gray-800 bg-transparent text-sm focus:outline-none focus:border-gray-400 dark:focus:border-gray-600"
					bind:value={scenario.counterpart_role}
					placeholder={$i18n.t('e.g. Procurement Director')}
				/>
			</div>
			<div>
				<label
					class="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1"
					for="cp-company">{$i18n.t('Company')}</label
				>
				<input
					id="cp-company"
					class="w-full px-3 py-2 rounded-lg border border-gray-200 dark:border-gray-800 bg-transparent text-sm focus:outline-none focus:border-gray-400 dark:focus:border-gray-600"
					bind:value={scenario.counterpart_company}
					placeholder={$i18n.t('e.g. ACME Corp')}
				/>
			</div>
		</div>

		<div class="mt-4">
			<label
				class="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1"
				for="cp-personality">{$i18n.t("The counterpart's personality & demeanor")}</label
			>
			<input
				id="cp-personality"
				class="w-full px-3 py-2 rounded-lg border border-gray-200 dark:border-gray-800 bg-transparent text-sm focus:outline-none focus:border-gray-400 dark:focus:border-gray-600"
				bind:value={scenario.personality}
				placeholder={$i18n.t(
					'e.g. cautious and detail-oriented, pushes back on price, warms up slowly'
				)}
			/>
		</div>
	</div>

	<!-- Background -->
	<div>
		<label
			class="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1"
			for="bg-info"
		>
			{$i18n.t('Background information')}
			<span class="ml-1 text-gray-400 dark:text-gray-500 font-normal">
				{$i18n.t('(paste client materials, prior emails, meeting notes...)')}
			</span>
		</label>
		<textarea
			id="bg-info"
			class="w-full min-h-[140px] px-3 py-2 rounded-lg border border-gray-200 dark:border-gray-800 bg-transparent text-sm focus:outline-none focus:border-gray-400 dark:focus:border-gray-600 resize-y"
			bind:value={scenario.background}
			placeholder={$i18n.t(
				'Anything the counterpart would know going in: their company, prior interactions, what they care about, what they are dissatisfied with...'
			)}
		></textarea>
	</div>

	<!-- Goal -->
	<div>
		<label
			class="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1"
			for="user-goal"
		>
			{$i18n.t('What do you want to practice?')}
			<span class="ml-1 text-gray-400 dark:text-gray-500 font-normal">
				{$i18n.t('(only you see this — the counterpart will not know your goal)')}
			</span>
		</label>
		<textarea
			id="user-goal"
			class="w-full min-h-[80px] px-3 py-2 rounded-lg border border-gray-200 dark:border-gray-800 bg-transparent text-sm focus:outline-none focus:border-gray-400 dark:focus:border-gray-600 resize-y"
			bind:value={scenario.goal}
			placeholder={$i18n.t(
				'e.g. pitch our new product and handle objections about price and switching cost'
			)}
		></textarea>
	</div>

	<!-- Advanced: raw prompt override -->
	<div>
		<button
			type="button"
			class="text-xs text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 transition"
			on:click={() => (showAdvanced = !showAdvanced)}
		>
			{showAdvanced ? $i18n.t('▾ Hide advanced') : $i18n.t('▸ Advanced: edit raw system prompt')}
		</button>
		{#if showAdvanced}
			<div class="mt-2">
				<div class="text-xs text-gray-500 mb-1">
					{$i18n.t(
						'If you type here, this raw prompt is used instead of the built one above. Leave empty to use the structured fields.'
					)}
				</div>
				<textarea
					class="w-full min-h-[160px] px-3 py-2 rounded-lg border border-gray-200 dark:border-gray-800 bg-transparent text-sm font-mono focus:outline-none focus:border-gray-400 dark:focus:border-gray-600 resize-y"
					bind:value={rawPrompt}
					placeholder={$i18n.t('Full system prompt (advanced)')}
				></textarea>
				<details class="mt-2">
					<summary class="text-xs text-gray-500 cursor-pointer hover:text-gray-700"
						>{$i18n.t('Preview built prompt')}</summary
					>
					<pre
						class="mt-1 text-xs whitespace-pre-wrap text-gray-600 dark:text-gray-400 bg-gray-50 dark:bg-gray-900/50 p-2 rounded">{buildSystemPrompt(
							scenario,
							''
						)}</pre>
				</details>
			</div>
		{/if}
	</div>

	<!-- Actions -->
	<div class="flex items-center justify-between pt-2">
		<button
			type="button"
			class="text-sm text-red-500 hover:text-red-600 transition"
			on:click={() => dispatch('delete')}
		>
			{$i18n.t('Delete')}
		</button>

		<div class="flex items-center gap-2">
			<button
				type="button"
				class="px-3 py-1.5 rounded-lg text-sm border border-gray-200 dark:border-gray-800 hover:bg-gray-50 dark:hover:bg-gray-900 transition disabled:opacity-50"
				disabled={!dirty}
				on:click={save}
			>
				{$i18n.t('Save')}
			</button>
			<button
				type="button"
				class="px-4 py-1.5 rounded-lg text-sm bg-gray-900 text-white hover:bg-gray-800 dark:bg-gray-100 dark:text-gray-900 dark:hover:bg-white transition"
				on:click={handleStart}
			>
				{$i18n.t('Start simulation')}
			</button>
		</div>
	</div>
</div>

<VtigerLeadPicker
	bind:show={showCrmPicker}
	on:select={(e) => applyVtigerLead(e.detail)}
/>
