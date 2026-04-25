import { useEffect, useRef, useState } from 'react';

/**
 * useVoiceStream — VAD-gated audio streaming to a WebSocket.
 *
 * When `enabled` is true:
 *   1. Opens the mic at 16kHz mono.
 *   2. Connects to `wsUrl`.
 *   3. Runs RMS-based VAD on each PCM chunk:
 *        - When RMS crosses START_THRESHOLD → send {type:"start"}, begin streaming.
 *        - While speaking, stream binary Float32 PCM frames.
 *        - When RMS stays below END_THRESHOLD for SILENCE_MS → send {type:"end"}.
 *   4. Cleans up everything when `enabled` flips false or the component unmounts.
 *
 * Returns status flags useful for UI (listening / speaking / connected).
 */
export function useVoiceStream({
  enabled,
  wsUrl,
  startThreshold = 0.02,   // RMS to begin an utterance
  endThreshold = 0.012,    // RMS below this counts as silence
  silenceMs = 800,         // silence duration before ending utterance
  minUtteranceMs = 250,    // ignore blips shorter than this
}) {
  const [connected, setConnected] = useState(false);
  const [listening, setListening] = useState(false);
  const [speaking, setSpeaking] = useState(false);

  // Refs so the audio callback always sees current values.
  const wsRef = useRef(null);
  const audioCtxRef = useRef(null);
  const streamRef = useRef(null);
  const workletNodeRef = useRef(null);
  const sourceNodeRef = useRef(null);
  const playbackRef = useRef(null);

  const speakingRef = useRef(false);
  const utteranceStartRef = useRef(0);
  const lastVoiceRef = useRef(0);

  useEffect(() => {
    if (!enabled) return;

    let cancelled = false;

    const start = async () => {
      try {
        // 1. Open WebSocket first so we don't capture audio with no destination.
        const ws = new WebSocket(wsUrl);
        ws.binaryType = 'arraybuffer';
        wsRef.current = ws;

        await new Promise((resolve, reject) => {
          ws.onopen = resolve;
          ws.onerror = reject;
        });
        if (cancelled) {
          ws.close();
          return;
        }
        setConnected(true);

        playbackRef.current = createPlayback();

        ws.onmessage = (ev) => {
          if (typeof ev.data !== 'string') {
            // Binary frame from server = int16 PCM TTS audio.
            playbackRef.current?.pushPCM(ev.data);
            return;
          }
          let msg;
          try { msg = JSON.parse(ev.data); } catch { return; }
          if (msg.type === 'audio_start') playbackRef.current?.start(msg.sample_rate);
          else if (msg.type === 'audio_end') playbackRef.current?.flush();
          else console.log('[ws]', msg);
        };
        ws.onclose = () => setConnected(false);

        // 2. Open mic at 16kHz mono.
        const stream = await navigator.mediaDevices.getUserMedia({
          audio: {
            channelCount: 1,
            sampleRate: 16000,
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: true,
          },
          video: false,
        });
        if (cancelled) {
          stream.getTracks().forEach((t) => t.stop());
          ws.close();
          return;
        }
        streamRef.current = stream;

        // 3. Build audio graph — AudioContext sampleRate is what we'll send.
        const audioCtx = new (window.AudioContext || window.webkitAudioContext)({
          sampleRate: 16000,
        });
        audioCtxRef.current = audioCtx;

        // Send meta so the server knows the format.
        ws.send(JSON.stringify({
          type: 'meta',
          sampleRate: audioCtx.sampleRate,
          channels: 1,
          format: 'f32',
        }));

        await audioCtx.audioWorklet.addModule('/pcm-worklet.js');
        if (cancelled) {
          stream.getTracks().forEach((t) => t.stop());
          audioCtx.close();
          ws.close();
          return;
        }

        const source = audioCtx.createMediaStreamSource(stream);
        const worklet = new AudioWorkletNode(audioCtx, 'pcm-worklet');
        sourceNodeRef.current = source;
        workletNodeRef.current = worklet;

        // 4. VAD + streaming. Each message from the worklet is a chunk of
        //    Float32 PCM (ArrayBuffer of CHUNK_SIZE samples).
        worklet.port.onmessage = (ev) => {
          const buffer = ev.data; // ArrayBuffer
          const samples = new Float32Array(buffer);

          // Compute RMS for VAD.
          let sumSq = 0;
          for (let i = 0; i < samples.length; i++) sumSq += samples[i] * samples[i];
          const rms = Math.sqrt(sumSq / samples.length);

          const now = performance.now();
          const wsNow = wsRef.current;
          if (!wsNow || wsNow.readyState !== WebSocket.OPEN) return;

          if (!speakingRef.current) {
            // Not currently in an utterance — wait for voice onset.
            if (rms >= startThreshold) {
              speakingRef.current = true;
              utteranceStartRef.current = now;
              lastVoiceRef.current = now;
              setSpeaking(true);
              wsNow.send(JSON.stringify({ type: 'start' }));
              // Send the chunk that triggered onset so we don't clip the first phoneme.
              wsNow.send(buffer);
            }
            return;
          }

          // Currently speaking — always forward the chunk.
          wsNow.send(buffer);

          if (rms >= endThreshold) {
            lastVoiceRef.current = now;
          } else if (now - lastVoiceRef.current >= silenceMs) {
            // Silence long enough to end the utterance.
            const utteranceLen = now - utteranceStartRef.current;
            speakingRef.current = false;
            setSpeaking(false);
            if (utteranceLen >= minUtteranceMs) {
              wsNow.send(JSON.stringify({ type: 'end' }));
            } else {
              // Too short to be real speech — abandon, don't bother the ASR.
              wsNow.send(JSON.stringify({ type: 'end', discard: true }));
            }
          }
        };

        source.connect(worklet);
        // Worklet doesn't need to reach the destination, but some browsers
        // need it connected to the graph to actually pull audio. Route to a
        // muted gain so nothing comes out of the speakers.
        const muted = audioCtx.createGain();
        muted.gain.value = 0;
        worklet.connect(muted).connect(audioCtx.destination);

        setListening(true);
      } catch (err) {
        console.error('[useVoiceStream] startup failed:', err);
      }
    };

    start();

    return () => {
      cancelled = true;
      setListening(false);
      setSpeaking(false);
      setConnected(false);
      speakingRef.current = false;

      try { workletNodeRef.current?.disconnect(); } catch {}
      try { sourceNodeRef.current?.disconnect(); } catch {}
      try { streamRef.current?.getTracks().forEach((t) => t.stop()); } catch {}
      try { audioCtxRef.current?.close(); } catch {}
      try { wsRef.current?.close(); } catch {}
      try { playbackRef.current?.close(); } catch {}

      workletNodeRef.current = null;
      sourceNodeRef.current = null;
      streamRef.current = null;
      audioCtxRef.current = null;
      wsRef.current = null;
      playbackRef.current = null;
    };
  }, [enabled, wsUrl, startThreshold, endThreshold, silenceMs, minUtteranceMs]);

  return { connected, listening, speaking };
}

/**
 * Gapless int16 PCM playback queue. Buffers chunks per sentence and
 * schedules them back-to-back on a single AudioContext clock.
 */
function createPlayback() {
  const ctx = new (window.AudioContext || window.webkitAudioContext)();
  let sampleRate = 22050;
  let pending = [];
  let nextStart = 0;

  return {
    start(rate) {
      sampleRate = rate || sampleRate;
      pending = [];
      if (ctx.state === 'suspended') ctx.resume();
    },
    pushPCM(arrayBuffer) {
      const i16 = new Int16Array(arrayBuffer);
      const f32 = new Float32Array(i16.length);
      for (let i = 0; i < i16.length; i++) f32[i] = i16[i] / 0x8000;
      pending.push(f32);
    },
    flush() {
      if (!pending.length) return;
      let total = 0;
      for (const a of pending) total += a.length;
      const buf = ctx.createBuffer(1, total, sampleRate);
      const ch = buf.getChannelData(0);
      let offset = 0;
      for (const a of pending) { ch.set(a, offset); offset += a.length; }
      pending = [];

      const src = ctx.createBufferSource();
      src.buffer = buf;
      src.connect(ctx.destination);
      const startAt = Math.max(ctx.currentTime, nextStart);
      src.start(startAt);
      nextStart = startAt + buf.duration;
    },
    close() { try { ctx.close(); } catch {} },
  };
}