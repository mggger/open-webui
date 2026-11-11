<script lang="ts">
	import { onDestroy } from 'svelte';
	import { toast } from 'svelte-sonner';
	import {
		Room,
		RoomEvent,
		Track,
		createLocalAudioTrack,
		type LocalAudioTrack,
		type RemoteTrack,
		type RemoteTrackPublication,
		type RemoteParticipant
	} from 'livekit-client';
	import { createBrainSession } from '$lib/apis/brain';

	type State =
		| 'idle'
		| 'connecting'
		| 'waiting'
		| 'listening'
		| 'thinking'
		| 'speaking'
		| 'error';
	let state: State = 'idle';
	let room: Room | null = null;
	let microphone: LocalAudioTrack | null = null;
	let transcript: { speaker: string; text: string }[] = [];

	const labels: Record<State, string> = {
		idle: 'Ready',
		connecting: 'Connecting',
		waiting: 'Agent standby',
		listening: 'Listening',
		thinking: 'Processing',
		speaking: 'Responding',
		error: 'Connection failed'
	};

	const stateDetails: Record<State, string> = {
		idle: 'VOICE SESSION OFFLINE',
		connecting: 'ESTABLISHING SECURE LINK',
		waiting: 'WAITING FOR BRAIN AGENT',
		listening: 'MICROPHONE ACTIVE · SPEAK NOW',
		thinking: 'ANALYZING YOUR REQUEST',
		speaking: 'VOICE OUTPUT ACTIVE',
		error: 'CHECK CONNECTION AND SETTINGS'
	};

	const attachAudio = (
		track: RemoteTrack,
		_publication: RemoteTrackPublication,
		_participant: RemoteParticipant
	) => {
		if (track.kind !== Track.Kind.Audio) return;
		const element = track.attach();
		element.autoplay = true;
		document.body.appendChild(element);
	};

	const start = async () => {
		state = 'connecting';
		try {
			const session = await createBrainSession(localStorage.token);
			room = new Room({ adaptiveStream: true, dynacast: true });
			room.on(RoomEvent.TrackSubscribed, attachAudio);
			room.on(RoomEvent.ParticipantConnected, (participant) => {
				if (participant.identity.startsWith('agent')) state = 'listening';
			});
			room.on(RoomEvent.ParticipantDisconnected, (participant) => {
				if (participant.identity.startsWith('agent')) state = 'waiting';
			});
			room.on(RoomEvent.ActiveSpeakersChanged, (speakers) => {
				const agentSpeaking = speakers.some((p) => p.identity.startsWith('agent'));
				state = agentSpeaking ? 'speaking' : 'listening';
			});
			room.on(RoomEvent.ParticipantAttributesChanged, (attributes, participant) => {
				if (!participant.identity.startsWith('agent')) return;
				const agentState = attributes['lk.agent.state'];
				if (agentState && ['idle', 'listening', 'thinking', 'speaking'].includes(agentState)) {
					state = agentState === 'idle' ? 'listening' : (agentState as State);
				}
			});
			room.on(RoomEvent.TranscriptionReceived, (segments, participant) => {
				for (const segment of segments) {
					if (!segment.final || !segment.text.trim()) continue;
					transcript = [
						...transcript,
						{
							speaker: participant?.identity?.startsWith('agent') ? 'Brain' : 'You',
							text: segment.text.trim()
						}
					];
				}
			});
			room.on(RoomEvent.Disconnected, () => {
				if (state !== 'idle') state = 'idle';
			});
			await room.connect(session.livekit_url, session.token);
			microphone = await createLocalAudioTrack({
				echoCancellation: true,
				noiseSuppression: true,
				autoGainControl: true
			});
			await room.localParticipant.publishTrack(microphone);
			const agentConnected = [...room.remoteParticipants.values()].some((participant) =>
				participant.identity.startsWith('agent')
			);
			state = agentConnected ? 'listening' : 'waiting';
		} catch (error) {
			console.error(error);
			state = 'error';
			toast.error(error instanceof Error ? error.message : 'Unable to connect to Brain');
			await stop();
		}
	};

	const stop = async () => {
		microphone?.stop();
		microphone = null;
		await room?.disconnect();
		room = null;
		state = 'idle';
	};

	onDestroy(() => void stop());
</script>

<svelte:head><title>Brain</title></svelte:head>

<div
	class="brain-stage h-full min-h-0 min-w-0 flex-1 overflow-hidden text-gray-900 dark:text-gray-100"
>
	<div class="grid-overlay"></div>
	<div class="scan-line"></div>
	<main class:with-transcript={transcript.length > 0} class="brain-layout">
		<section class="brain-viewport">
			<div class="brain-core state-{state}" aria-label={labels[state]}>
				<div class="orbit orbit-one"><i></i><i></i><i></i></div>
				<div class="orbit orbit-two"><i></i><i></i></div>
				<div class="neural-halo"></div>
				<svg viewBox="0 0 240 210" class="brain-svg" fill="none">
					<defs
						><linearGradient id="neural" x1="30" y1="20" x2="210" y2="190"
							><stop stop-color="#22d3ee" /><stop offset=".5" stop-color="#818cf8" /><stop
								offset="1"
								stop-color="#e879f9"
							/></linearGradient
						></defs
					>
					<path
						class="brain-outline"
						d="M107 29c-23-15-52 2-49 28-24 3-33 31-17 48-17 17-6 48 18 48 2 26 30 39 49 23 7 13 18 19 29 16 18-4 24-19 21-35 25-3 35-32 18-49 18-19 5-48-19-49 2-25-27-42-49-27Z"
					/>
					<path
						class="synapse"
						d="M66 71l31 18 25-29 28 37 24-13M54 119l39-8 23 31 35-16 24 22M80 160l13-49m29-51-6 82m34-45 1 29"
					/>
					{#each [[66, 71], [97, 89], [122, 60], [150, 97], [174, 84], [54, 119], [93, 111], [116, 142], [151, 126], [175, 148], [80, 160]] as point}
						<circle class="node" cx={point[0]} cy={point[1]} r="4" />
					{/each}
				</svg>
			</div>
			<div class="status-console status-{state}" role="status" aria-live="polite">
				<div class="status-signal" aria-hidden="true">
					<span class="status-dot"></span>
					<div class="signal-bars">
						{#each [0, 1, 2, 3, 4] as index}
							<i style={`--bar-index: ${index}`}></i>
						{/each}
					</div>
				</div>
				<div class="status-copy">
					<strong>{labels[state]}</strong>
					<span>{stateDetails[state]}</span>
				</div>
				<div class="status-live"><i></i>{state === 'idle' || state === 'error' ? 'OFF' : 'LIVE'}</div>
			</div>

			{#if state === 'idle' || state === 'error'}
				<button class="primary-action" on:click={start}>Start conversation</button>
			{:else}
				<button class="end-action" on:click={stop}>End session</button>
			{/if}
		</section>

		{#if transcript.length}
			<aside class="transcript-panel">
				<div class="panel-heading"><span>LIVE TRANSCRIPT</span><i></i></div>
				<div class="transcript-feed">
					{#each transcript.slice(-8) as item}
						<div class="transcript-line">
							<span>{item.speaker}</span>
							<p>{item.text}</p>
						</div>
					{/each}
				</div>
			</aside>
		{/if}
	</main>
</div>

<style>
	.brain-stage {
		position: relative;
		isolation: isolate;
		background: radial-gradient(circle at 50% 46%, rgba(79, 70, 229, 0.18), transparent 44%),
			linear-gradient(145deg, rgba(15, 23, 42, 0.02), rgba(6, 182, 212, 0.03));
	}
	@media (min-width: 768px) {
		.brain-stage {
			margin-left: 260px;
		}
	}
	.brain-layout {
		height: 100%;
		display: grid;
		grid-template-columns: minmax(0, 1fr);
		padding: 24px 28px;
	}
	.brain-layout.with-transcript {
		grid-template-columns: minmax(0, 1fr) minmax(280px, 360px);
		gap: 24px;
	}
	.brain-viewport {
		min-height: 0;
		display: flex;
		flex-direction: column;
		align-items: center;
		justify-content: center;
		gap: 14px;
	}
	.status-console {
		--status-rgb: 100, 116, 139;
		--status-color: rgb(var(--status-rgb));
		position: relative;
		display: grid;
		grid-template-columns: 48px minmax(150px, 1fr) auto;
		align-items: center;
		gap: 14px;
		min-width: min(390px, calc(100vw - 48px));
		margin-top: -20px;
		padding: 12px 14px;
		border: 1px solid rgba(var(--status-rgb), 0.34);
		border-radius: 18px;
		background: linear-gradient(110deg, rgba(var(--status-rgb), 0.12), rgba(255, 255, 255, 0.52));
		box-shadow: 0 14px 42px rgba(15, 23, 42, 0.1), 0 0 30px rgba(var(--status-rgb), 0.12), inset 0 1px rgba(255, 255, 255, 0.7);
		backdrop-filter: blur(20px) saturate(145%);
		transition: border-color 0.35s, background 0.35s, box-shadow 0.35s;
	}
	:global(.dark) .status-console {
		background: linear-gradient(110deg, rgba(var(--status-rgb), 0.14), rgba(15, 23, 42, 0.62));
		box-shadow: 0 14px 42px rgba(0, 0, 0, 0.28), 0 0 34px rgba(var(--status-rgb), 0.16), inset 0 1px rgba(255, 255, 255, 0.08);
	}
	.status-listening {
		--status-rgb: 6, 182, 212;
	}
	.status-thinking,
	.status-connecting,
	.status-waiting {
		--status-rgb: 129, 140, 248;
	}
	.status-speaking {
		--status-rgb: 217, 70, 239;
	}
	.status-error {
		--status-rgb: 248, 113, 113;
	}
	.status-signal {
		position: relative;
		width: 48px;
		height: 48px;
		display: grid;
		place-items: center;
		border: 1px solid rgba(var(--status-rgb), 0.3);
		border-radius: 14px;
		background: rgba(var(--status-rgb), 0.1);
		box-shadow: inset 0 0 18px rgba(var(--status-rgb), 0.08);
	}
	.status-dot {
		position: absolute;
		top: 6px;
		right: 6px;
		width: 5px;
		height: 5px;
		border-radius: 50%;
		background: var(--status-color);
		box-shadow: 0 0 9px var(--status-color);
	}
	.signal-bars {
		height: 20px;
		display: flex;
		align-items: center;
		gap: 3px;
	}
	.signal-bars i {
		display: block;
		width: 2px;
		height: 6px;
		border-radius: 4px;
		background: var(--status-color);
		box-shadow: 0 0 7px rgba(var(--status-rgb), 0.7);
		opacity: 0.65;
	}
	.status-listening .signal-bars i,
	.status-speaking .signal-bars i {
		animation: voiceBar 0.8s ease-in-out infinite alternate;
		animation-delay: calc(var(--bar-index) * -0.11s);
	}
	.status-thinking .signal-bars i,
	.status-connecting .signal-bars i,
	.status-waiting .signal-bars i {
		animation: computeBar 1.1s ease-in-out infinite;
		animation-delay: calc(var(--bar-index) * 0.1s);
	}
	.status-copy {
		min-width: 0;
		display: flex;
		flex-direction: column;
		gap: 4px;
	}
	.status-copy strong {
		font-size: 21px;
		line-height: 1;
		letter-spacing: -0.025em;
		color: var(--status-color);
		text-shadow: 0 0 22px rgba(var(--status-rgb), 0.25);
	}
	.status-copy span {
		color: #64748b;
		font: 600 9px/1.2 monospace;
		letter-spacing: 0.11em;
	}
	:global(.dark) .status-copy span {
		color: #94a3b8;
	}
	.status-live {
		display: flex;
		align-items: center;
		gap: 6px;
		align-self: start;
		padding: 4px 7px;
		border: 1px solid rgba(var(--status-rgb), 0.25);
		border-radius: 99px;
		color: var(--status-color);
		font: 700 8px/1 monospace;
		letter-spacing: 0.1em;
	}
	.status-live i {
		width: 5px;
		height: 5px;
		border-radius: 50%;
		background: currentColor;
		box-shadow: 0 0 7px currentColor;
	}
	.status-listening .status-live i,
	.status-thinking .status-live i,
	.status-speaking .status-live i {
		animation: livePulse 1.35s ease-out infinite;
	}
	.primary-action,
	.end-action {
		margin-top: 10px;
		border-radius: 99px;
		padding: 11px 24px;
		font-size: 13px;
		font-weight: 600;
		transition: 0.2s;
	}
	.primary-action {
		color: white;
		background: linear-gradient(90deg, #4f46e5, #7c3aed);
		box-shadow: 0 0 30px rgba(99, 102, 241, 0.28);
	}
	.primary-action:hover {
		transform: translateY(-1px);
		box-shadow: 0 0 40px rgba(99, 102, 241, 0.45);
	}
	.end-action {
		color: #f87171;
		border: 1px solid rgba(248, 113, 113, 0.35);
		background: rgba(248, 113, 113, 0.05);
	}
	.transcript-panel {
		align-self: stretch;
		min-height: 0;
		margin-top: 6px;
		border: 1px solid rgba(129, 140, 248, 0.18);
		border-radius: 20px;
		background: rgba(255, 255, 255, 0.38);
		backdrop-filter: blur(20px);
		padding: 18px;
		overflow: hidden;
		display: flex;
		flex-direction: column;
	}
	:global(.dark) .transcript-panel {
		background: rgba(15, 23, 42, 0.38);
	}
	.panel-heading {
		display: flex;
		align-items: center;
		justify-content: space-between;
		color: #818cf8;
		font: 600 9px/1 monospace;
		letter-spacing: 0.2em;
		padding-bottom: 14px;
		border-bottom: 1px solid rgba(129, 140, 248, 0.15);
	}
	.panel-heading i {
		width: 6px;
		height: 6px;
		border-radius: 50%;
		background: #22d3ee;
		box-shadow: 0 0 9px #22d3ee;
	}
	.transcript-feed {
		overflow: auto;
		padding-top: 8px;
	}
	.transcript-line {
		padding: 12px 2px;
		border-bottom: 1px solid rgba(129, 140, 248, 0.1);
	}
	.transcript-line span {
		display: block;
		margin-bottom: 5px;
		color: #818cf8;
		font: 600 9px/1 monospace;
		text-transform: uppercase;
		letter-spacing: 0.12em;
	}
	.transcript-line p {
		margin: 0;
		font-size: 13px;
		line-height: 1.55;
	}
	.scan-line {
		position: absolute;
		inset: 0;
		z-index: -1;
		background: linear-gradient(transparent 49.8%, rgba(34, 211, 238, 0.08) 50%, transparent 50.2%);
		background-size: 100% 160px;
		animation: scan 7s linear infinite;
	}
	.grid-overlay {
		position: absolute;
		inset: 0;
		z-index: -1;
		opacity: 0.18;
		background-image: linear-gradient(rgba(99, 102, 241, 0.18) 1px, transparent 1px),
			linear-gradient(90deg, rgba(99, 102, 241, 0.18) 1px, transparent 1px);
		background-size: 42px 42px;
		mask-image: radial-gradient(circle at center, black, transparent 75%);
	}
	.brain-core {
		position: relative;
		width: min(52vh, 470px);
		height: min(52vh, 470px);
		min-width: 330px;
		min-height: 330px;
		display: grid;
		place-items: center;
		filter: drop-shadow(0 0 28px rgba(99, 102, 241, 0.28));
	}
	.neural-halo {
		position: absolute;
		inset: 42px;
		border-radius: 50%;
		background: radial-gradient(
			circle,
			rgba(99, 102, 241, 0.22),
			rgba(34, 211, 238, 0.08) 45%,
			transparent 70%
		);
		animation: breathe 3s ease-in-out infinite;
	}
	.brain-svg {
		width: 78%;
		z-index: 2;
		overflow: visible;
	}
	.brain-outline,
	.synapse {
		stroke: url(#neural);
		stroke-width: 3;
		stroke-linecap: round;
		stroke-linejoin: round;
	}
	.synapse {
		stroke-width: 1.6;
		stroke-dasharray: 5 8;
		animation: signal 2s linear infinite;
	}
	.node {
		fill: #e0e7ff;
		stroke: #22d3ee;
		stroke-width: 2;
		animation: nodePulse 2.2s ease-in-out infinite;
	}
	.orbit {
		position: absolute;
		inset: 20px;
		border: 1px solid rgba(129, 140, 248, 0.28);
		border-radius: 50%;
		animation: rotate 11s linear infinite;
	}
	.orbit-two {
		inset: 5px 38px;
		animation-duration: 16s;
		animation-direction: reverse;
		transform: rotate(60deg);
	}
	.orbit i {
		position: absolute;
		width: 7px;
		height: 7px;
		border-radius: 50%;
		background: #22d3ee;
		box-shadow: 0 0 14px #22d3ee;
	}
	.orbit i:nth-child(1) {
		top: 20%;
		left: 5%;
	}
	.orbit i:nth-child(2) {
		top: 74%;
		right: 5%;
	}
	.orbit i:nth-child(3) {
		top: 2%;
		left: 58%;
	}
	.state-thinking .brain-svg {
		animation: think 0.65s ease-in-out infinite alternate;
	}
	.state-speaking .synapse {
		animation-duration: 0.55s;
	}
	.state-speaking .neural-halo {
		animation-duration: 0.75s;
	}
	.state-listening .orbit {
		border-color: rgba(34, 211, 238, 0.5);
	}
	.state-idle {
		opacity: 0.72;
		filter: saturate(0.65);
	}
	@keyframes rotate {
		to {
			transform: rotate(360deg);
		}
	}
	@keyframes signal {
		to {
			stroke-dashoffset: -26;
		}
	}
	@keyframes breathe {
		50% {
			transform: scale(1.16);
			opacity: 0.6;
		}
	}
	@keyframes think {
		to {
			transform: scale(1.035);
			filter: brightness(1.25);
		}
	}
	@keyframes nodePulse {
		50% {
			r: 6px;
			filter: drop-shadow(0 0 7px #22d3ee);
		}
	}
	@keyframes scan {
		to {
			background-position: 0 160px;
		}
	}
	@keyframes voiceBar {
		0% {
			height: 5px;
			opacity: 0.55;
		}
		100% {
			height: 20px;
			opacity: 1;
		}
	}
	@keyframes computeBar {
		0%,
		100% {
			height: 5px;
			opacity: 0.4;
		}
		50% {
			height: 16px;
			opacity: 1;
		}
	}
	@keyframes livePulse {
		0% {
			box-shadow: 0 0 0 0 rgba(var(--status-rgb), 0.65);
		}
		70%,
		100% {
			box-shadow: 0 0 0 6px rgba(var(--status-rgb), 0);
		}
	}
	@media (max-width: 900px) {
		.brain-layout.with-transcript {
			grid-template-columns: 1fr;
			overflow: auto;
		}
		.transcript-panel {
			min-height: 190px;
			max-height: 240px;
		}
		.brain-core {
			width: min(46vh, 400px);
			height: min(46vh, 400px);
			min-width: 280px;
			min-height: 280px;
		}
	}
	@media (max-width: 520px) {
		.brain-layout {
			padding: 18px 14px;
		}
		.status-console {
			grid-template-columns: 42px minmax(0, 1fr) auto;
			gap: 10px;
			min-width: 0;
			width: 100%;
			padding: 10px;
		}
		.status-signal {
			width: 42px;
			height: 42px;
		}
		.status-copy strong {
			font-size: 18px;
		}
		.status-copy span {
			font-size: 8px;
			letter-spacing: 0.07em;
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.brain-core *,
		.status-console * {
			animation: none !important;
		}
	}
</style>
