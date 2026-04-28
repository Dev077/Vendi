import { Component, useRef, useState, useEffect, useCallback } from 'react';
import Webcam from 'react-webcam';
import { useVoiceStream } from './useVoiceStream';
import Live2DCharacter from './Live2DCharacter';

const WS_URL = 'ws://127.0.0.1:5000/ws/audio';
const MODEL_URL = '/live2d/haru_greeter_pro_jp/runtime/haru_greeter_t05.model3.json';

// Surface render-time errors instead of letting the awake view silently
// blank out. Without this, a throw inside Live2DCharacter (or any awake-side
// component) leaves only the dark background behind.
class ErrorBoundary extends Component {
  state = { error: null };
  static getDerivedStateFromError(error) { return { error }; }
  componentDidCatch(error, info) {
    console.error('[ErrorBoundary] caught:', error, info);
  }
  render() {
    if (this.state.error) {
      return (
        <div style={{ padding: 20, color: '#f88', fontFamily: 'monospace', fontSize: 13, whiteSpace: 'pre-wrap' }}>
          <div style={{ fontSize: 16, marginBottom: 8 }}>Render error</div>
          <div>{String(this.state.error?.stack || this.state.error)}</div>
          <button
            onClick={() => { this.setState({ error: null }); this.props.onReset?.(); }}
            style={{ marginTop: 12, padding: '6px 12px', background: '#222', color: '#fff', border: '1px solid #444', borderRadius: 4, cursor: 'pointer' }}
          >
            Reset
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

function App() {
  const webcamRef = useRef(null);
  const intervalRef = useRef(null);
  const live2dRef = useRef(null);
  const [isAwake, setIsAwake] = useState(false);
  const [debug, setDebug] = useState({ status: 'starting…', motionScore: 0, objects: [] });
  const [serverError, setServerError] = useState(null);

  const handleExpression = useCallback((name) => {
    if (!live2dRef.current) {
      console.warn(`[App] expression "${name}" arrived before Live2D ref was attached — dropping`);
      return;
    }
    live2dRef.current.setExpression(name);
  }, []);

  // Voice streaming activates only after wake.
  const { connected, listening, speaking } = useVoiceStream({
    enabled: isAwake,
    wsUrl: WS_URL,
    onServerError: setServerError,
    onExpression: handleExpression,
  });

  // Function to capture the current frame and send it to Flask
  const captureAndProcess = useCallback(async () => {
    if (!webcamRef.current) return;
    const imageSrc = webcamRef.current.getScreenshot();
    if (!imageSrc) {
      setDebug((d) => ({ ...d, status: 'webcam not ready' }));
      return;
    }

    try {
      const response = await fetch('http://127.0.0.1:5000/process_frame', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ image: imageSrc }),
      });

      const data = await response.json();
      console.log('[frame]', data);
      setDebug({
        status: data.message,
        motionScore: data.motion_score,
        objects: data.objects || [],
      });

      // WAKE TRIGGER: human confirmed → flip to awake state
      if (data.human_detected) {
        console.log(`Human detected (${data.matched}, ${data.confidence}%) — waking.`);
        setIsAwake(true);
      }
    } catch (error) {
      console.error('Error communicating with backend:', error);
      setDebug((d) => ({ ...d, status: `backend error: ${error.message}` }));
    }
  }, [webcamRef]);

  // Polling interval — runs while asleep, stops the moment isAwake flips true
  useEffect(() => {
    if (isAwake) {
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
      return;
    }

    intervalRef.current = setInterval(() => {
      captureAndProcess();
    }, 500);

    return () => {
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
    };
  }, [captureAndProcess, isAwake]);

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        background: '#000',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        fontFamily: 'sans-serif',
        color: '#fff',
        overflow: 'hidden',
      }}
    >
      {/* Hidden webcam — keeps capturing frames while screen is asleep.
          Mounted only while asleep; unmounted on wake to release the camera. */}
      {!isAwake && (
        <>
          <div style={{ position: 'absolute', width: 1, height: 1, opacity: 0, pointerEvents: 'none', overflow: 'hidden' }}>
            <Webcam
              audio={false}
              ref={webcamRef}
              screenshotFormat="image/jpeg"
              videoConstraints={{ facingMode: 'user' }}
            />
          </div>

          {/* Asleep diagnostics — shows what the wake pipeline is seeing. */}
          <div style={{ textAlign: 'center', fontSize: 13, opacity: 0.85, lineHeight: 1.6 }}>
            <div style={{ fontSize: 18, marginBottom: 12, opacity: 0.9 }}>Vendi is asleep…</div>
            <div>{debug.status}</div>
            <div>motion score: {debug.motionScore}</div>
            {debug.objects.length > 0 && (
              <div>objects: {debug.objects.map(([n, c]) => `${n} ${c}%`).join(', ')}</div>
            )}
            <button
              onClick={() => setIsAwake(true)}
              style={{
                marginTop: 18,
                padding: '8px 16px',
                fontSize: 13,
                background: '#222',
                color: '#fff',
                border: '1px solid #444',
                borderRadius: 4,
                cursor: 'pointer',
              }}
            >
              Wake manually
            </button>
          </div>
        </>
      )}

      {isAwake && (
        <ErrorBoundary onReset={() => { setIsAwake(false); setServerError(null); }}>
          <Live2DCharacter ref={live2dRef} modelUrl={MODEL_URL} />
          <div
            style={{
              position: 'absolute',
              top: 12,
              right: 12,
              fontSize: 12,
              opacity: 0.85,
              padding: '6px 10px',
              background: 'rgba(0,0,0,0.5)',
              borderRadius: 6,
              pointerEvents: 'none',
              maxWidth: 320,
            }}
          >
            <div>WS: {connected ? 'connected' : 'disconnected'}</div>
            <div>Mic: {listening ? 'listening' : 'idle'}</div>
            <div>VAD: {speaking ? 'speaking' : 'silent'}</div>
            {serverError && (
              <div style={{ marginTop: 6, color: '#f88', whiteSpace: 'pre-wrap' }}>
                server error{serverError.stage ? ` [${serverError.stage}]` : ''}: {serverError.message}
              </div>
            )}
          </div>
        </ErrorBoundary>
      )}
    </div>
  );
}

export default App;