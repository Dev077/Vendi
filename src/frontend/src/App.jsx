import React, { useRef, useState, useEffect, useCallback } from 'react';
import Webcam from 'react-webcam';
import { useVoiceStream } from './useVoiceStream';

const WS_URL = 'ws://127.0.0.1:5000/ws/audio';

function App() {
  const webcamRef = useRef(null);
  const intervalRef = useRef(null);
  const [isAwake, setIsAwake] = useState(false);

  // Voice streaming activates only after wake.
  const { connected, listening, speaking } = useVoiceStream({
    enabled: isAwake,
    wsUrl: WS_URL,
  });

  // Function to capture the current frame and send it to Flask
  const captureAndProcess = useCallback(async () => {
    if (webcamRef.current) {
      const imageSrc = webcamRef.current.getScreenshot();
      if (!imageSrc) return;

      try {
        const response = await fetch('http://127.0.0.1:5000/process_frame', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({ image: imageSrc }),
        });

        const data = await response.json();

        // WAKE TRIGGER: human confirmed → flip to awake state
        if (data.api_called && data.human_detected) {
          console.log(`Human detected with ${data.confidence}% confidence — waking character.`);
          setIsAwake(true);
        }
      } catch (error) {
        console.error("Error communicating with backend:", error);
      }
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
        <div style={{ position: 'absolute', width: 1, height: 1, opacity: 0, pointerEvents: 'none', overflow: 'hidden' }}>
          <Webcam
            audio={false}
            ref={webcamRef}
            screenshotFormat="image/jpeg"
            videoConstraints={{ facingMode: 'user' }}
          />
        </div>
      )}

      {/* Awake view — character placeholder. Replace with the real character component. */}
      {isAwake && (
        <div style={{ textAlign: 'center' }}>
          <h1>Character Awake</h1>
          <p>Human detected. Character has taken over from here.</p>
          <div style={{ marginTop: 24, fontSize: 14, opacity: 0.8 }}>
            <div>WS: {connected ? ' connected' : ' disconnected'}</div>
            <div>Mic: {listening ? ' listening' : ' idle'}</div>
            <div>VAD: {speaking ? ' speaking' : ' silent'}</div>
          </div>
        </div>
      )}
    </div>
  );
}

export default App;