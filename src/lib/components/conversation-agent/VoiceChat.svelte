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

	const LOG_TAG = '[VoiceChat]';
	const log = (...args: any[]) => console.log(LOG_TAG, ...args);
	const warn = (...args: any[]) => console.warn(LOG_TAG, ...args);
	const err = (...args: any[]) => console.error(LOG_TAG, ...args);

	let status: 'idle' | 'connecting' | 'listening' | 'transcribing' | 'thinking' | 'speaking' | 'error' =
		'idle';
	let history: Msg[] = [];

	let audioStream: MediaStream | null = null;
	let audioContext: AudioContext | null = null;
	let workletNode: AudioWorkletNode | null = null;
	let sourceNode: MediaStreamAudioSourceNode | null = null;
	let ws: WebSocket | null = null;

	let currentAudio: HTMLAudioElement | null = null;
	let currentAudioUrl: string | null = null;
	let speakToken = 0;

	let active = true;
	let hasStartedSpeaking = false; // flips true on server speech_start, false on final
	let assistantSpeaking = false;
	let suppressMic = false; // true while TTS plays -> drop PCM frames

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
			log('cleanupAudio: pausing current audio');
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
		log('stopStream called', {
			hasWs: !!ws,
			hasWorklet: !!workletNode,
			hasStream: !!audioStream,
			hasCtx: !!audioContext
		});
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

	const close = () => {
		log('close() — user ended session');
		active = false;
		speakToken++;
		cleanupAudio();
		stopStream();
		dispatch('close');
	};

	const wsUrl = (): string => {
		// WEBUI_BASE_URL is `http://<host>:8080` in dev and `""` in prod.
		// We derive the WS scheme (ws/wss) from the resolved origin so it
		// matches the backend location, not the vite dev server.
		const httpBase = WEBUI_BASE_URL || window.location.origin;
		const wsBase = httpBase.replace(/^http/, 'ws');
		const token = encodeURIComponent(localStorage.token || '');
		const lang = $settings?.audio?.stt?.language;
		const qs = new URLSearchParams({ token });
		if (lang) qs.set('language', lang);
		return `${wsBase}/api/v1/audio/stream?${qs.toString()}`;
	};

	const startListening = async () => {
		log('startListening() called', { active, status });
		if (!active) return;
		if (status === 'listening' || status === 'thinking' || status === 'transcribing') {
			log('startListening: already busy, skipping', { status });
			return;
		}

		status = 'connecting';
		hasStartedSpeaking = false;

		// 1) Mic
		try {
			log('requesting getUserMedia');
			audioStream = await navigator.mediaDevices.getUserMedia({
				audio: {
					echoCancellation: true,
					noiseSuppression: true,
					autoGainControl: true
				}
			});
			log('getUserMedia success');
		} catch (e) {
			err('getUserMedia failed', e);
			toast.error($i18n.t('Microphone access denied'));
			status = 'error';
			return;
		}

		// 2) AudioContext + PCM worklet
		try {
			audioContext = new AudioContext();
			log('AudioContext created', { sampleRate: audioContext.sampleRate });
			await audioContext.audioWorklet.addModule('/static/audio/pcm-worklet.js');
			workletNode = new AudioWorkletNode(audioContext, 'pcm-worklet');
			sourceNode = audioContext.createMediaStreamSource(audioStream);
			sourceNode.connect(workletNode);
			// Do NOT connect worklet to destination — we don't want to play back mic.
		} catch (e) {
			err('AudioWorklet setup failed', e);
			toast.error($i18n.t('Audio setup failed'));
			status = 'error';
			stopStream();
			return;
		}

		// 3) WebSocket to backend
		try {
			ws = new WebSocket(wsUrl());
			ws.binaryType = 'arraybuffer';
		} catch (e) {
			err('WebSocket construction failed', e);
			status = 'error';
			stopStream();
			return;
		}

		ws.onopen = () => {
			log('WS open');
		};

		ws.onerror = (e) => {
			err('WS error', e);
		};

		ws.onclose = (ev) => {
			log('WS close', { code: ev.code, reason: ev.reason });
			// Reconnect if closed unexpectedly while still active & idle.
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
			log('WS msg', msg);
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
					// back to listening on the same socket
					status = 'listening';
					return;
				}
				// Stop the mic before LLM/TTS round-trip.
				stopStream();
				history = [...history, { role: 'user', content: userText }];
				await runLLM();
			} else if (msg.type === 'error') {
				err('WS backend error', msg.error);
				toast.error(msg.error || $i18n.t('Transcription failed'));
			}
		};

		// 4) Wire worklet PCM frames -> WebSocket
		workletNode.port.onmessage = (ev) => {
			if (!ws || ws.readyState !== WebSocket.OPEN) return;
			if (suppressMic) return; // skip while TTS plays
			ws.send(ev.data as ArrayBuffer);
		};
	};

	const countWords = (s: string): number => {
		const trimmed = s.trim();
		if (!trimmed) return 0;
		// Rough count: CJK chars each count as 1 "word", plus whitespace-separated latin tokens.
		const cjk = (trimmed.match(/[一-鿿぀-ヿ가-힯]/g) || []).length;
		const latin = trimmed
			.replace(/[一-鿿぀-ヿ가-힯]/g, ' ')
			.trim()
			.split(/\s+/)
			.filter(Boolean).length;
		return cjk + latin;
	};

	// Pull sentence-ish chunks from a growing buffer. Returns [chunks, remainder].
	// A chunk is emitted only when it ends in terminal punctuation AND has >= MIN_WORDS_PER_CHUNK words.
	// If `flushAll` is true, returns whatever is left even if short.
	const extractChunks = (buf: string, flushAll: boolean): [string[], string] => {
		const TERMS = new Set(['.', '!', '?', '。', '！', '？', '\n']);
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
				// else: keep accumulating — candidate too short, roll into next sentence
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

	const runLLM = async () => {
		status = 'thinking';

		const messages: Msg[] = [];
		if (agent.system_prompt) {
			messages.push({ role: 'system', content: agent.system_prompt });
		}
		messages.push(...history);

		const body = {
			model: agent.model_id,
			messages,
			stream: true
		};
		log('runLLM: sending (stream)', { model: agent.model_id, msgCount: messages.length });
		const t0 = performance.now();

		// TTS producer/consumer state
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
					if (token !== speakToken || !active) {
						log('consumer: cancelled', { i });
						return;
					}
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
			log('enqueue TTS chunk', { words: countWords(chunk), preview: chunk.slice(0, 80) });
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
					log('runLLM: cancelled mid-stream');
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

			// Flush any remainder (may be < MIN_WORDS if LLM ended early)
			const [tail] = extractChunks(pendingBuf, true);
			pendingBuf = '';
			for (const c of tail) enqueueChunk(c);
		} catch (e) {
			err('LLM stream failed', e);
			toast.error($i18n.t('LLM request failed'));
		} finally {
			producerDone = true;
		}

		log('runLLM: stream complete', {
			ms: Math.round(performance.now() - t0),
			replyLen: assistantFull.length
		});

		if (assistantFull.trim()) {
			history = [...history, { role: 'assistant', content: assistantFull }];
		}

		// If nothing was enqueued (empty reply), start consumer anyway so it resolves.
		if (!consumerStarted) {
			consumerStarted = true;
			playConsumer();
		}

		await consumerDone.promise;
		if (token === speakToken) {
			assistantSpeaking = false;
			suppressMic = false;
		}

		log('runLLM: playback complete, resuming listening');
		if (active && token === speakToken) startListening();
	};

	const synthesizeFull = async (text: string): Promise<string | null> => {
		const cleaned = stripForTTS(text);
		if (!cleaned) {
			warn('TTS synth: empty after cleaning', { rawPreview: text.slice(0, 60) });
			return null;
		}
		const t0 = performance.now();
		try {
			log('TTS synth start', { len: cleaned.length, preview: cleaned.slice(0, 80) });
			const res = await synthesizeOpenAISpeech(localStorage.token, '', cleaned);
			if (!res) {
				warn('TTS synth returned null');
				return null;
			}
			const blob = await res.blob();
			log('TTS synth ok', {
				ms: Math.round(performance.now() - t0),
				bytes: blob.size,
				type: blob.type
			});
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
				log('audio playback ended');
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
			log('audio playback start');
			a.play().catch((e) => {
				err('audio.play() rejected', e);
				resolve();
			});
		});


	const interrupt = () => {
		log('interrupt() — stopping TTS, resuming listen');
		speakToken++;
		assistantSpeaking = false;
		suppressMic = false;
		cleanupAudio();
		if (active) startListening();
	};

	$: statusLabel = (() => {
		switch (status) {
			case 'connecting':
				return $i18n.t('Connecting...');
			case 'listening':
				return hasStartedSpeaking
					? $i18n.t('Listening...')
					: $i18n.t('Listening... Speak anytime');
			case 'transcribing':
				return $i18n.t('Transcribing...');
			case 'thinking':
				return $i18n.t('Thinking...');
			case 'speaking':
				return $i18n.t('Speaking...');
			case 'error':
				return $i18n.t('Microphone unavailable');
			default:
				return $i18n.t('Starting...');
		}
	})();

	onMount(() => {
		log('onMount', { agent: { id: agent.id, name: agent.name, model_id: agent.model_id } });
		startListening();
	});

	onDestroy(() => {
		log('onDestroy');
		active = false;
		speakToken++;
		cleanupAudio();
		stopStream();
	});
</script>

<div class="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex flex-col">
	<div class="flex items-center justify-between px-4 py-3 text-white">
		<div class="text-sm opacity-80">{agent.name}</div>
		<button
			type="button"
			class="text-sm px-3 py-1 rounded-lg bg-white/10 hover:bg-white/20 transition"
			on:click={close}
		>
			{$i18n.t('End')}
		</button>
	</div>

	<div class="flex-1 overflow-y-auto px-4 py-6">
		<div class="mx-auto max-w-2xl flex flex-col gap-3">
			{#each history as m}
				<div class="flex {m.role === 'user' ? 'justify-end' : 'justify-start'}">
					<div
						class="px-3 py-2 rounded-2xl max-w-[80%] text-sm {m.role === 'user'
							? 'bg-blue-500 text-white'
							: 'bg-white/10 text-white'}"
					>
						{m.content}
					</div>
				</div>
			{/each}

			{#if history.length === 0}
				<div class="text-center text-white/60 text-sm py-10">
					{$i18n.t('Start talking — the agent will respond after you pause.')}
				</div>
			{/if}
		</div>
	</div>

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
