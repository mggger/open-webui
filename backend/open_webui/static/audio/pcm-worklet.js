// PCM downsampler worklet.
//
// Takes the AudioContext's native-rate float32 input and emits 16kHz
// int16 PCM frames of fixed size (512 samples -> 32ms, matching the
// backend Silero VAD expectation). Each frame is posted to the main
// thread as an ArrayBuffer via postMessage so the main thread can ship
// it over a WebSocket with minimal copying.

class PCMWorklet extends AudioWorkletProcessor {
	constructor(options) {
		super();
		this.targetRate = 16000;
		this.frameSize = 512; // samples at target rate
		this.ratio = sampleRate / this.targetRate;
		this.accumulator = new Float32Array(this.frameSize);
		this.accumulatorFilled = 0;
		this.resampleCursor = 0;
	}

	process(inputs) {
		const input = inputs[0];
		if (!input || input.length === 0) return true;
		const channel = input[0];
		if (!channel) return true;

		// Simple linear-interpolation resampler from sampleRate -> 16kHz.
		// Good enough for speech; Whisper is robust to minor resampling artifacts.
		while (this.resampleCursor < channel.length) {
			const idx = Math.floor(this.resampleCursor);
			const frac = this.resampleCursor - idx;
			const s0 = channel[idx];
			const s1 = idx + 1 < channel.length ? channel[idx + 1] : s0;
			const sample = s0 + (s1 - s0) * frac;

			this.accumulator[this.accumulatorFilled++] = sample;
			if (this.accumulatorFilled >= this.frameSize) {
				const i16 = new Int16Array(this.frameSize);
				for (let i = 0; i < this.frameSize; i++) {
					let s = this.accumulator[i];
					if (s > 1) s = 1;
					else if (s < -1) s = -1;
					i16[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
				}
				// Transfer the underlying buffer to avoid a copy.
				this.port.postMessage(i16.buffer, [i16.buffer]);
				this.accumulatorFilled = 0;
			}

			this.resampleCursor += this.ratio;
		}
		// Wrap cursor back so the next block continues cleanly.
		this.resampleCursor -= channel.length;
		return true;
	}
}

registerProcessor('pcm-worklet', PCMWorklet);
