// AudioWorklet that runs in the audio rendering thread.
// Receives audio in 128-sample blocks, buffers them, and posts
// chunks of CHUNK_SIZE Float32 samples back to the main thread.

class PCMWorkletProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    // ~64ms of audio per chunk at 16kHz (1024 samples).
    // Small enough for low latency, big enough to avoid flooding the WS.
    this.CHUNK_SIZE = 1024;
    this.buffer = new Float32Array(this.CHUNK_SIZE);
    this.offset = 0;
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || input.length === 0) return true;

    // Mono — take channel 0.
    const channel = input[0];
    if (!channel) return true;

    for (let i = 0; i < channel.length; i++) {
      this.buffer[this.offset++] = channel[i];
      if (this.offset >= this.CHUNK_SIZE) {
        // Transfer the underlying ArrayBuffer for zero-copy.
        const out = this.buffer;
        this.port.postMessage(out.buffer, [out.buffer]);
        this.buffer = new Float32Array(this.CHUNK_SIZE);
        this.offset = 0;
      }
    }
    return true;
  }
}

registerProcessor('pcm-worklet', PCMWorkletProcessor);