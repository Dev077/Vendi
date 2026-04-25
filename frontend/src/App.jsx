import React, { useRef, useState, useEffect, useCallback } from 'react';
import Webcam from 'react-webcam';

function App() {
  const webcamRef = useRef(null);
  const [status, setStatus] = useState('Initializing camera...');
  const [motionScore, setMotionScore] = useState(0);

  // Function to capture the current frame and send it to Flask
  const captureAndProcess = useCallback(async () => {
    if (webcamRef.current) {
      // Get the frame as a Base64 encoded JPEG string
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
        
        // Update the UI based on the Flask response
        setMotionScore(data.motion_score);
        setStatus(data.message);
        
        if (data.api_called && data.human_detected) {
          console.log(`Human detected with ${data.confidence}% confidence!`);
        }

      } catch (error) {
        console.error("Error communicating with backend:", error);
        setStatus("Error connecting to Flask server.");
      }
    }
  }, [webcamRef]);

  // Set up an interval to trigger the capture function every 500ms
  useEffect(() => {
    const interval = setInterval(() => {
      captureAndProcess();
    }, 500); 

    // Cleanup the interval when the component unmounts
    return () => clearInterval(interval);
  }, [captureAndProcess]);

  return (
    <div style={{ fontFamily: 'sans-serif', padding: '2rem', maxWidth: '800px', margin: '0 auto' }}>
      <h1>Test0</h1>
      
      <div style={{ position: 'relative', marginBottom: '1rem', border: '2px solid #ccc', borderRadius: '8px', overflow: 'hidden' }}>
        <Webcam
          audio={false}
          ref={webcamRef}
          screenshotFormat="image/jpeg"
          width="100%"
          videoConstraints={{ facingMode: "user" }}
        />
      </div>

      <div style={{ padding: '1rem', backgroundColor: '#f5f5f5', borderRadius: '8px' }}>
        <h3>System Status</h3>
        <p><strong>Message:</strong> {status}</p>
        <p><strong>Motion Score:</strong> {motionScore}</p>
      </div>
    </div>
  );
}

export default App;