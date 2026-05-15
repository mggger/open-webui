<script lang="ts">
	import { createEventDispatcher, getContext, onDestroy, onMount } from 'svelte';
	import { toast } from 'svelte-sonner';

	import { synthesizeOpenAISpeech } from '$lib/apis/audio';
	import { settings } from '$lib/stores';
	import { WEBUI_BASE_URL } from '$lib/constants';
	import type { ConversationAgent } from '$lib/apis/conversation-agents';

	const i18n = getContext('i18n');
	const dispatch = createEventDispatcher();

	export let agent: ConversationAgent;

	type Msg = { role: 'user' | 'assistant' | 'system'; content: string };

	const MIN_WORDS_PER_CHUNK = 8;

	const COMPACT_TRIGGER_TURNS = 10;
	const COMPACT_KEEP_RECENT = 6;

	const VOICE_DIALOG_STYLE_PROMPT = `

# Voice conversation style (must follow)
You are speaking to the user through a voice interface. Your reply will be read aloud by TTS.
- Ask AT MOST ONE question per reply. Wait for the user's answer before asking the next one.
- Keep each reply to 1-2 short spoken sentences. Never produce a list of questions.
- Use plain spoken language. No markdown, no bullet points, no code blocks, no emojis.
- If the persona above instructs you to collect multiple pieces of information, gather them ONE AT A TIME across multiple turns, not all at once.
- Acknowledge the user's previous answer briefly before asking the next question, so the conversation feels natural.`;

	const LOG_TAG = '[VoiceChat]';
	const log = (...args: any[]) => console.log(LOG_TAG, ...args);
	const warn = (...args: any[]) => console.warn(LOG_TAG, ...args);
	const err = (...args: any[]) => console.error(LOG_TAG, ...args);

	// Scenario metadata pulled from agent.meta.scenario (set by AgentEditor).
	// Used purely for UI — the LLM gets the full built system_prompt.
	const scenario = ((agent.meta as any)?.scenario ?? {}) as {
		counterpart_name?: string;
		counterpart_role?: string;
		counterpart_company?: string;
	};
	const counterpartName = scenario.counterpart_name || agent.name || $i18n.t('Counterpart');
	const counterpartSubline = [scenario.counterpart_role, scenario.counterpart_company]
		.filter(Boolean)
		.join(' · ');
	const initials = (counterpartName || '?')
		.replace(/[^\p{L}\p{N}\s]/gu, '')
		.split(/\s+/)
		.filter(Boolean)
		.slice(0, 2)
		.map((s: string) => s[0]?.toUpperCase() ?? '')
		.join('') || '?';

	let status:
		| 'idle'
		| 'connecting'
		| 'listening'
		| 'transcribing'
		| 'thinking'
		| 'speaking'
		| 'compacting'
		| 'error' = 'idle';
	let history: Msg[] = [];
	let memorySummary = '';
	let compacting = false;

	let audioStream: MediaStream | null = null;
	let audioContext: AudioContext | null = null;
	let workletNode: AudioWorkletNode | null = null;
	let sourceNode: MediaStreamAudioSourceNode | null = null;
	let ws: WebSocket | null = null;

	let currentAudio: HTMLAudioElement | null = null;
	let currentAudioUrl: string | null = null;
	let speakToken = 0;

	let active = true;
	let hasStartedSpeaking = false;
	let assistantSpeaking = false;
	let suppressMic = false;

	// Debrief state.
	let showDebrief = false;
	let debriefLoading = false;
	let debriefText = '';

	const EMOJI_REGEX = /(\p{Extended_Pictographic}(?:️|‍)?)+/gu;

	const stripForTTS = (raw: string): string => {
		if (!raw) return '';
		let text = raw;
		text = text.replace(/```[\s\S]*?```/g, ' ');
		text = text.replace(/`[^`]*`/g, ' ');
		text = text.replace(/!\[[^\]]*\]\([^)]*\)/g, ' ');
		text = text.replace(/\[([^\]]+)\]\([^)]*\)/g, '$1');
		text = text.replace(/^\s{0,3}#{1,6}\s+/gm, '');
		text = text.replace(/(\*\*|__)(.*?)\1/g, '$2');
		text = text.replace(/(\*|_)(.*?)\1/g, '$2');
		text = text.replace(/~~(.*?)~~/g, '$1');
		text = text.replace(/^\s*>\s?/gm, '');
		text = text.replace(/^\s*[-*+]\s+/gm, '');
		text = text.replace(/^\s*\d+\.\s+/gm, '');
		text = text.replace(/^\s*\|.*\|\s*$/gm, ' ');
		text = text.replace(/^-{3,}$/gm, ' ');
		text = text.replace(EMOJI_REGEX, ' ');
		text = text.replace(/[ \t]+/g, ' ').replace(/\n{2,}/g, '\n').trim();
		return text;
	};

	const cleanupAudio = () => {
		if (currentAudio) {
			currentAudio.pause();
			currentAudio.src = '';
			currentAudio = null;
		}
		if (currentAudioUrl) {
			URL.revokeObjectURL(currentAudioUrl);
			currentAudioUrl = null;
		}
	};

	const stopStream = () => {
		if (ws) {
			try {
				ws.close();
			} catch (e) {
				err('stopStream: ws.close failed', e);
			}
			ws = null;
		}
		if (workletNode) {
			try {
				workletNode.port.onmessage = null;
				workletNode.disconnect();
			} catch (e) {
				err('stopStream: worklet disconnect failed', e);
			}
			workletNode = null;
		}
		if (sourceNode) {
			try {
				sourceNode.disconnect();
			} catch {}
			sourceNode = null;
		}
		if (audioStream) {
			audioStream.getTracks().forEach((t) => t.stop());
			audioStream = null;
		}
		if (audioContext) {
			try {
				audioContext.close();
			} catch (e) {
				err('stopStream: audioContext.close() failed', e);
			}
			audioContext = null;
		}
	};

	const finalClose = () => {
		log('finalClose — leaving session');
		active = false;
		speakToken++;
		cleanupAudio();
		stopStream();
		dispatch('close');
	};

	// "End" button: if there's a real conversation, show debrief first;
	// otherwise leave immediately.
	const endSession = () => {
		log('endSession clicked', { historyLen: history.length });
		// Stop the live audio loop right away — debrief uses its own LLM call.
		speakToken++;
		assistantSpeaking = false;
		suppressMic = false;
		cleanupAudio();
		stopStream();
		active = false; // prevent any pending startListening from reconnecting

		const hasMeaningfulConvo = history.some((m) => m.role === 'user') && history.length >= 2;
		if (hasMeaningfulConvo) {
			runDebrief();
		} else {
			finalClose();
		}
	};

	const runDebrief = async () => {
		showDebrief = true;
		debriefLoading = true;
		debriefText = '';

		const transcript = history
			.map((m) => `${m.role === 'user' ? 'You' : counterpartName}: ${m.content}`)
			.join('\n');

		const memoryBlock = memorySummary
			? `\n\nEarlier conversation summary:\n${memorySummary}`
			: '';

		const prompt = `You are a communication coach reviewing a role-play conversation the user just completed. The user was practicing for a real business interaction. The "counterpart" (${counterpartName}) was being played by an AI based on a scenario the user set up.

Give the user a debrief in plain spoken language. Use these sections, in this exact order, each with a short heading:

**What went well**
2-3 specific things the user did effectively. Quote a brief phrase if useful.

**What to work on**
2-3 specific, actionable suggestions. Focus on what the user said or didn't say.

**${counterpartName}'s real concerns**
What did the counterpart actually care about under the surface? What objections or worries did they signal?

**One thing to try next time**
A single concrete tactic for next rehearsal.

Keep it concise — under 250 words total. Be direct and honest, not flattering. No emojis.${memoryBlock}

--- Transcript ---
${transcript}`;

		try {
			const res = await fetch(`${WEBUI_BASE_URL}/api/chat/completions`, {
				method: 'POST',
				headers: {
					Authorization: `Bearer ${localStorage.token}`,
					'Content-Type': 'application/json'
				},
				credentials: 'include',
				body: JSON.stringify({
					model: agent.model_id,
					messages: [{ role: 'user', content: prompt }],
					stream: false
				})
			});
			if (!res.ok) {
				let detail = `HTTP ${res.status}`;
				try {
					const j = await res.json();
					detail = j?.detail ?? detail;
				} catch {}
				err('debrief failed', detail);
				debriefText = $i18n.t('Could not generate debrief: {{err}}', {
					err: typeof detail === 'string' ? detail : 'unknown error'
				});
				return;
			}
			const j = await res.json();
			debriefText = (j?.choices?.[0]?.message?.content ?? '').trim() ||
				$i18n.t('No debrief returned.');
		} catch (e) {
			err('debrief threw', e);
			debriefText = $i18n.t('Could not generate debrief.');
		} finally {
			debriefLoading = false;
		}
	};

	const wsUrl = (): string => {
		const httpBase = WEBUI_BASE_URL || window.location.origin;
		const wsBase = httpBase.replace(/^http/, 'ws');
		const token = encodeURIComponent(localStorage.token || '');
		const lang = $settings?.audio?.stt?.language;
		const qs = new URLSearchParams({ token });
		if (lang) qs.set('language', lang);
		return `${wsBase}/api/v1/audio/stream?${qs.toString()}`;
	};

	const startListening = async () => {
		if (!active) return;
		if (status === 'listening' || status === 'thinking' || status === 'transcribing') {
			return;
		}

		status = 'connecting';
		hasStartedSpeaking = false;

		try {
			audioStream = await navigator.mediaDevices.getUserMedia({
				audio: {
					echoCancellation: true,
					noiseSuppression: true,
					autoGainControl: true
				}
			});
		} catch (e) {
			err('getUserMedia failed', e);
			toast.error($i18n.t('Microphone access denied'));
			status = 'error';
			return;
		}

		try {
			audioContext = new AudioContext();
			await audioContext.audioWorklet.addModule('/static/audio/pcm-worklet.js');
			workletNode = new AudioWorkletNode(audioContext, 'pcm-worklet');
			sourceNode = audioContext.createMediaStreamSource(audioStream);
			sourceNode.connect(workletNode);
		} catch (e) {
			err('AudioWorklet setup failed', e);
			toast.error($i18n.t('Audio setup failed'));
			status = 'error';
			stopStream();
			return;
		}

		try {
			ws = new WebSocket(wsUrl());
			ws.binaryType = 'arraybuffer';
		} catch (e) {
			err('WebSocket construction failed', e);
			status = 'error';
			stopStream();
			return;
		}

		ws.onopen = () => log('WS open');

		ws.onerror = (e) => err('WS error', e);

		ws.onclose = (ev) => {
			log('WS close', { code: ev.code, reason: ev.reason });
			if (active && !assistantSpeaking && status !== 'thinking' && status !== 'transcribing') {
				setTimeout(() => {
					if (active && !ws) startListening();
				}, 500);
			}
		};

		ws.onmessage = async (ev) => {
			let msg: any;
			try {
				msg = JSON.parse(ev.data);
			} catch {
				return;
			}
			if (msg.type === 'ready') {
				status = 'listening';
			} else if (msg.type === 'speech_start') {
				hasStartedSpeaking = true;
			} else if (msg.type === 'speech_end') {
				status = 'transcribing';
			} else if (msg.type === 'final') {
				const userText = (msg.text || '').trim();
				hasStartedSpeaking = false;
				if (!userText) {
					status = 'listening';
					return;
				}
				stopStream();
				history = [...history, { role: 'user', content: userText }];
				await runLLM();
			} else if (msg.type === 'error') {
				err('WS backend error', msg.error);
				toast.error(msg.error || $i18n.t('Transcription failed'));
			}
		};

		workletNode.port.onmessage = (ev) => {
			if (!ws || ws.readyState !== WebSocket.OPEN) return;
			if (suppressMic) return;
			ws.send(ev.data as ArrayBuffer);
		};
	};

	const countWords = (s: string): number => {
		const trimmed = s.trim();
		if (!trimmed) return 0;
		const cjk = (trimmed.match(/[一-鿿぀-ヿ가-힯]/g) || []).length;
		const latin = trimmed
			.replace(/[一-鿿぀-ヿ가-힯]/g, ' ')
			.trim()
			.split(/\s+/)
			.filter(Boolean).length;
		return cjk + latin;
	};

	const extractChunks = (buf: string, flushAll: boolean): [string[], string] => {
		const TERMS = new Set(['.', '!', '?', '。', '!', '?', '\n']);
		const chunks: string[] = [];
		let start = 0;
		let cursor = 0;
		while (cursor < buf.length) {
			const ch = buf[cursor];
			cursor++;
			if (TERMS.has(ch)) {
				const candidate = buf.slice(start, cursor).trim();
				if (candidate && countWords(candidate) >= MIN_WORDS_PER_CHUNK) {
					chunks.push(candidate);
					start = cursor;
				}
			}
		}
		const remainder = buf.slice(start);
		if (flushAll) {
			const tail = remainder.trim();
			if (tail) chunks.push(tail);
			return [chunks, ''];
		}
		return [chunks, remainder];
	};

	const compactHistory = async () => {
		if (compacting) return;
		if (history.length <= COMPACT_TRIGGER_TURNS) return;

		const snapshotLen = history.length - COMPACT_KEEP_RECENT;
		if (snapshotLen <= 0) return;
		const toCompact = history.slice(0, snapshotLen);

		compacting = true;

		const transcript = toCompact
			.map((m) => `${m.role === 'user' ? 'User' : 'Assistant'}: ${m.content}`)
			.join('\n');

		const instruction = memorySummary
			? `You are maintaining a running memory of a voice conversation. Below is the previous memory summary, followed by newer turns that need to be folded in. Produce an UPDATED summary that preserves every concrete fact the user shared (names, numbers, preferences, decisions, open questions) and the assistant's promises or pending actions. Drop greetings and filler. Keep it under 200 words. Output ONLY the summary text, no preamble.\n\n--- Previous summary ---\n${memorySummary}\n\n--- New turns ---\n${transcript}`
			: `Summarize this voice conversation so far. Preserve every concrete fact the user shared (names, numbers, preferences, decisions, open questions) and any promises or pending actions from the assistant. Drop greetings and filler. Keep it under 200 words. Output ONLY the summary text, no preamble.\n\n${transcript}`;

		try {
			const res = await fetch(`${WEBUI_BASE_URL}/api/chat/completions`, {
				method: 'POST',
				headers: {
					Authorization: `Bearer ${localStorage.token}`,
					'Content-Type': 'application/json'
				},
				credentials: 'include',
				body: JSON.stringify({
					model: agent.model_id,
					messages: [{ role: 'user', content: instruction }],
					stream: false
				})
			});
			if (!res.ok) {
				warn('compactHistory: LLM call failed', { status: res.status });
				return;
			}
			const j = await res.json();
			const summary: string = (j?.choices?.[0]?.message?.content ?? '').trim();
			if (!summary) return;

			memorySummary = summary;
			history = history.slice(snapshotLen);
		} catch (e) {
			err('compactHistory: failed', e);
		} finally {
			compacting = false;
		}
	};

	// runLLM has two modes:
	// - normal: user just spoke; messages already contains the new user turn
	// - opener: no user turn yet, we ask the AI to start the conversation in character
	const runLLM = async (opener = false) => {
		status = 'thinking';

		const messages: Msg[] = [];
		const baseSystem = (agent.system_prompt || '').trim();
		const memoryBlock = memorySummary
			? `\n\n# Conversation memory so far\n${memorySummary}`
			: '';
		const systemContent =
			(baseSystem || '') + memoryBlock + VOICE_DIALOG_STYLE_PROMPT;
		messages.push({ role: 'system', content: systemContent.trim() });
		messages.push(...history);

		if (opener) {
			// Nudge the model to break the ice as the counterpart. This is
			// phrased as a user-role instruction so it sits *after* the
			// system prompt and is harder for the model to ignore than a
			// pure system instruction would be.
			messages.push({
				role: 'user',
				content:
					'[Stage direction, not part of the conversation: the call has just connected and no one has spoken yet. You speak first, in character, with one short natural opening line — a greeting and an opening prompt. One spoken sentence, maybe two. Do not narrate the stage direction.]'
			});
		}

		const body = {
			model: agent.model_id,
			messages,
			stream: true
		};

		const token = ++speakToken;
		assistantSpeaking = true;
		suppressMic = true;
		status = 'speaking';
		const synthQueue: Promise<string | null>[] = [];
		let consumerStarted = false;
		const consumerDone = { resolve: () => {}, promise: Promise.resolve() as Promise<void> };
		consumerDone.promise = new Promise<void>((r) => (consumerDone.resolve = r));

		const playConsumer = async () => {
			try {
				let i = 0;
				while (true) {
					if (token !== speakToken || !active) return;
					if (i >= synthQueue.length) {
						if (producerDone) return;
						await new Promise((r) => setTimeout(r, 50));
						continue;
					}
					const url = await synthQueue[i];
					i++;
					if (token !== speakToken || !active) {
						if (url) URL.revokeObjectURL(url);
						return;
					}
					if (!url) continue;
					await playUrl(url);
				}
			} finally {
				consumerDone.resolve();
			}
		};

		let producerDone = false;
		const enqueueChunk = (chunk: string) => {
			if (!chunk) return;
			synthQueue.push(synthesizeFull(chunk));
			if (!consumerStarted) {
				consumerStarted = true;
				playConsumer();
			}
		};

		let assistantFull = '';
		let pendingBuf = '';

		try {
			const res = await fetch(`${WEBUI_BASE_URL}/api/chat/completions`, {
				method: 'POST',
				headers: {
					Authorization: `Bearer ${localStorage.token}`,
					'Content-Type': 'application/json'
				},
				credentials: 'include',
				body: JSON.stringify(body)
			});

			if (!res.ok || !res.body) {
				let detail = `HTTP ${res.status}`;
				try {
					const j = await res.json();
					detail = j?.detail ?? detail;
				} catch {}
				err('LLM request failed', detail);
				toast.error(typeof detail === 'string' ? detail : $i18n.t('LLM request failed'));
				producerDone = true;
				assistantSpeaking = false;
				suppressMic = false;
				if (active) startListening();
				return;
			}

			const reader = res.body.getReader();
			const decoder = new TextDecoder('utf-8');
			let sseBuf = '';

			while (true) {
				if (token !== speakToken || !active) {
					try {
						reader.cancel();
					} catch {}
					break;
				}
				const { value, done } = await reader.read();
				if (done) break;
				sseBuf += decoder.decode(value, { stream: true });

				let nl: number;
				while ((nl = sseBuf.indexOf('\n')) !== -1) {
					const line = sseBuf.slice(0, nl).trim();
					sseBuf = sseBuf.slice(nl + 1);
					if (!line || !line.startsWith('data:')) continue;
					const payload = line.slice(5).trim();
					if (payload === '[DONE]') continue;
					try {
						const j = JSON.parse(payload);
						const delta: string = j?.choices?.[0]?.delta?.content ?? '';
						if (delta) {
							assistantFull += delta;
							pendingBuf += delta;
							const [chunks, remainder] = extractChunks(pendingBuf, false);
							pendingBuf = remainder;
							for (const c of chunks) enqueueChunk(c);
						}
					} catch (e) {
						warn('SSE JSON parse failed', { payload, e });
					}
				}
			}

			const [tail] = extractChunks(pendingBuf, true);
			pendingBuf = '';
			for (const c of tail) enqueueChunk(c);
		} catch (e) {
			err('LLM stream failed', e);
			toast.error($i18n.t('LLM request failed'));
		} finally {
			producerDone = true;
		}

		if (assistantFull.trim()) {
			history = [...history, { role: 'assistant', content: assistantFull }];
		}

		if (history.length > COMPACT_TRIGGER_TURNS && !compacting) {
			compactHistory();
		}

		if (!consumerStarted) {
			consumerStarted = true;
			playConsumer();
		}

		await consumerDone.promise;
		if (token === speakToken) {
			assistantSpeaking = false;
			suppressMic = false;
		}

		if (active && token === speakToken) startListening();
	};

	const synthesizeFull = async (text: string): Promise<string | null> => {
		const cleaned = stripForTTS(text);
		if (!cleaned) return null;
		try {
			const res = await synthesizeOpenAISpeech(localStorage.token, '', cleaned);
			if (!res) return null;
			const blob = await res.blob();
			return URL.createObjectURL(blob);
		} catch (e) {
			err('TTS synth failed', e);
			return null;
		}
	};

	const playUrl = (url: string): Promise<void> =>
		new Promise((resolve) => {
			const a = new Audio(url);
			currentAudio = a;
			currentAudioUrl = url;
			a.onended = () => {
				URL.revokeObjectURL(url);
				if (currentAudio === a) {
					currentAudio = null;
					currentAudioUrl = null;
				}
				resolve();
			};
			a.onerror = (ev) => {
				err('audio playback error', ev);
				URL.revokeObjectURL(url);
				if (currentAudio === a) {
					currentAudio = null;
					currentAudioUrl = null;
				}
				resolve();
			};
			a.play().catch((e) => {
				err('audio.play() rejected', e);
				resolve();
			});
		});

	const interrupt = () => {
		speakToken++;
		assistantSpeaking = false;
		suppressMic = false;
		cleanupAudio();
		if (active) startListening();
	};

	$: statusLabel = (() => {
		let base: string;
		switch (status) {
			case 'connecting':
				base = $i18n.t('Connecting...');
				break;
			case 'listening':
				base = hasStartedSpeaking
					? $i18n.t('Listening...')
					: $i18n.t('Your turn — speak when ready');
				break;
			case 'transcribing':
				base = $i18n.t('Transcribing...');
				break;
			case 'thinking':
				base = $i18n.t('{{name}} is thinking...', { name: counterpartName });
				break;
			case 'speaking':
				base = $i18n.t('{{name}} is speaking...', { name: counterpartName });
				break;
			case 'compacting':
				base = $i18n.t('Updating memory...');
				break;
			case 'error':
				base = $i18n.t('Microphone unavailable');
				break;
			default:
				base = $i18n.t('Starting...');
		}
		if (compacting && status !== 'compacting' && status !== 'error') {
			return `${base} · ${$i18n.t('updating memory')}`;
		}
		return base;
	})();

	onMount(() => {
		log('onMount', { agent: { id: agent.id, name: agent.name, model_id: agent.model_id } });
		// AI opens the conversation in character, then we start listening.
		runLLM(true);
	});

	onDestroy(() => {
		active = false;
		speakToken++;
		cleanupAudio();
		stopStream();
	});

	const copyDebrief = async () => {
		try {
			await navigator.clipboard.writeText(debriefText);
			toast.success($i18n.t('Debrief copied'));
		} catch {
			toast.error($i18n.t('Copy failed'));
		}
	};
</script>

<div class="fixed inset-0 z-50 bg-gradient-to-b from-gray-900 to-black flex flex-col">
	<!-- Header: counterpart card -->
	<div class="px-4 py-3 flex items-center justify-between text-white border-b border-white/10">
		<div class="flex items-center gap-3 min-w-0">
			<div
				class="size-10 rounded-full bg-white/15 flex items-center justify-center text-sm font-semibold shrink-0"
				aria-hidden="true"
			>
				{initials}
			</div>
			<div class="min-w-0">
				<div class="text-sm font-medium truncate">{counterpartName}</div>
				{#if counterpartSubline}
					<div class="text-xs text-white/60 truncate">{counterpartSubline}</div>
				{/if}
			</div>
		</div>
		<button
			type="button"
			class="text-sm px-3 py-1.5 rounded-lg bg-white/10 hover:bg-white/20 transition"
			on:click={endSession}
		>
			{$i18n.t('End')}
		</button>
	</div>

	<!-- Transcript -->
	<div class="flex-1 overflow-y-auto px-4 py-6">
		<div class="mx-auto max-w-2xl flex flex-col gap-3">
			{#each history as m}
				<div class="flex {m.role === 'user' ? 'justify-end' : 'justify-start'}">
					<div
						class="px-3 py-2 rounded-2xl max-w-[80%] text-sm leading-relaxed {m.role === 'user'
							? 'bg-blue-500 text-white'
							: 'bg-white/10 text-white'}"
					>
						{#if m.role === 'assistant'}
							<div class="text-[10px] uppercase tracking-wide text-white/50 mb-0.5">
								{counterpartName}
							</div>
						{/if}
						{m.content}
					</div>
				</div>
			{/each}

			{#if history.length === 0}
				<div class="text-center text-white/60 text-sm py-10">
					{$i18n.t('Connecting — {{name}} is about to speak first...', {
						name: counterpartName
					})}
				</div>
			{/if}
		</div>
	</div>

	<!-- Controls -->
	<div class="px-4 pb-6 pt-2 flex flex-col items-center gap-3">
		<div class="text-white/80 text-sm h-5">{statusLabel}</div>

		<div class="flex items-center gap-4">
			<div
				class="size-16 rounded-full flex items-center justify-center shadow-lg transition
					{status === 'listening' && hasStartedSpeaking
					? 'bg-red-500 animate-pulse'
					: status === 'listening'
					? 'bg-green-500'
					: status === 'speaking'
					? 'bg-blue-500 animate-pulse'
					: status === 'thinking' || status === 'transcribing'
					? 'bg-yellow-500 animate-pulse'
					: 'bg-white/30'}"
				aria-label={statusLabel}
			>
				<svg
					xmlns="http://www.w3.org/2000/svg"
					viewBox="0 0 24 24"
					fill="currentColor"
					class="size-7 text-white"
				>
					<path
						d="M12 14.25a3.75 3.75 0 0 0 3.75-3.75V6a3.75 3.75 0 1 0-7.5 0v4.5a3.75 3.75 0 0 0 3.75 3.75Z"
					/>
					<path
						d="M6 10.5a.75.75 0 0 1 1.5 0 4.5 4.5 0 0 0 9 0 .75.75 0 0 1 1.5 0 6 6 0 0 1-5.25 5.955V18.75h2.25a.75.75 0 0 1 0 1.5h-6a.75.75 0 0 1 0-1.5h2.25v-2.295A6 6 0 0 1 6 10.5Z"
					/>
				</svg>
			</div>

			{#if status === 'speaking'}
				<button
					type="button"
					class="px-3 py-2 rounded-lg text-sm bg-white/10 text-white hover:bg-white/20 transition"
					on:click={interrupt}
				>
					{$i18n.t('Interrupt')}
				</button>
			{/if}
		</div>
	</div>
</div>

<!-- Debrief modal -->
{#if showDebrief}
	<div class="fixed inset-0 z-[60] bg-black/80 flex items-center justify-center p-4">
		<div
			class="w-full max-w-2xl max-h-[85vh] bg-white dark:bg-gray-900 rounded-2xl shadow-2xl flex flex-col overflow-hidden"
		>
			<div
				class="px-5 py-4 border-b border-gray-200 dark:border-gray-800 flex items-center justify-between"
			>
				<div>
					<div class="text-base font-semibold">{$i18n.t('Meeting debrief')}</div>
					<div class="text-xs text-gray-500 mt-0.5">
						{$i18n.t('Coaching feedback on your conversation with {{name}}', {
							name: counterpartName
						})}
					</div>
				</div>
			</div>
			<div class="flex-1 overflow-y-auto px-5 py-4">
				{#if debriefLoading}
					<div class="flex items-center gap-2 text-sm text-gray-500">
						<div
							class="size-4 rounded-full border-2 border-gray-300 border-t-gray-700 animate-spin"
						></div>
						{$i18n.t('Reviewing the conversation...')}
					</div>
				{:else}
					<div
						class="text-sm whitespace-pre-wrap leading-relaxed text-gray-800 dark:text-gray-200"
					>
						{debriefText}
					</div>
				{/if}
			</div>
			<div
				class="px-5 py-3 border-t border-gray-200 dark:border-gray-800 flex items-center justify-end gap-2"
			>
				<button
					type="button"
					class="px-3 py-1.5 rounded-lg text-sm border border-gray-200 dark:border-gray-800 hover:bg-gray-50 dark:hover:bg-gray-900 transition disabled:opacity-50"
					disabled={debriefLoading || !debriefText}
					on:click={copyDebrief}
				>
					{$i18n.t('Copy')}
				</button>
				<button
					type="button"
					class="px-4 py-1.5 rounded-lg text-sm bg-gray-900 text-white hover:bg-gray-800 dark:bg-gray-100 dark:text-gray-900 dark:hover:bg-white transition"
					on:click={finalClose}
				>
					{$i18n.t('Close')}
				</button>
			</div>
		</div>
	</div>
{/if}
