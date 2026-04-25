from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_sock import Sock
import cv2
import numpy as np
import base64
import time
import struct
from dotenv import load_dotenv
from google.cloud import vision

load_dotenv()

app = Flask(__name__)
# Enable CORS so the React frontend can communicate with this API
CORS(app)
# WebSocket support — same Flask app, same port, no separate server needed.
sock = Sock(app)

# initialize Google Cloud Vision Client
client = vision.ImageAnnotatorClient()

# initialize OpenCV Background Subtractor
back_sub = cv2.createBackgroundSubtractorMOG2(history=500, varThreshold=50, detectShadows=False)

# debounce state
last_api_call_time = 0
COOLDOWN_SECONDS = 5.0

@app.route('/process_frame', methods=['POST'])
def process_frame():
    global last_api_call_time
    
    # 1. Receive the Base64 image from React
    data = request.json
    if 'image' not in data:
        return jsonify({"error": "No image provided"}), 400
        
    # Strip the "data:image/jpeg;base64," prefix
    image_b64 = data['image'].split(',')[1]
    
    # 2. Decode the Base64 string into an OpenCV readable format
    nparr = np.frombuffer(base64.b64decode(image_b64), np.uint8)
    frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

    # 3. Apply motion detection
    fg_mask = back_sub.apply(frame)
    motion_score = cv2.countNonZero(fg_mask)
    
    result = {
        "motion_score": motion_score,
        "api_called": False,
        "human_detected": False,
        "message": "Processing..."
    }

    # 4. Logic Gate: Check threshold and cooldown
    if motion_score > 10000:
        current_time = time.time()
        
        if (current_time - last_api_call_time) > COOLDOWN_SECONDS:
            print("Motion detected! Calling Vision API...")
            last_api_call_time = current_time
            
            # Encode frame to JPEG bytes for Google Vision
            success, encoded_image = cv2.imencode('.jpg', frame)
            content = encoded_image.tobytes()
            vision_image = vision.Image(content=content)
            
            # Call Google Cloud Vision API
            response = client.object_localization(image=vision_image)
            objects = response.localized_object_annotations
            
            # Check for humans
            for obj in objects:
                if obj.name.lower() == 'person':
                    result["human_detected"] = True
                    result["confidence"] = round(obj.score * 100, 1)
                    break
            
            result["api_called"] = True
            result["message"] = "API called: Human found!" if result["human_detected"] else "API called: False alarm."
        else:
            result["message"] = "Motion detected, but API is on cooldown."
    else:
         result["message"] = "No significant motion."

    return jsonify(result)


# ---------------------------------------------------------------------------
# WebSocket endpoint for streaming audio from the frontend.
#
# Protocol:
#   - Client sends JSON text messages for control:
#       {"type": "start"}            → begin a new utterance
#       {"type": "end"}              → end of utterance, ready for ASR
#       {"type": "meta", "sampleRate": 16000, "channels": 1, "format": "f32"}
#   - Client sends BINARY messages containing raw Float32 PCM samples
#     (little-endian) while an utterance is in progress.
#
# Server buffers PCM per-utterance and (later) hands it to ASR.transcribe_pcm.
# ---------------------------------------------------------------------------
@sock.route('/ws/audio')
def audio_socket(ws):
    import json

    print("[ws] client connected")
    pcm_buffer = bytearray()
    sample_rate = 16000  # default; client should override via meta
    in_utterance = False
    utterance_start = 0.0

    try:
        while True:
            # receive() returns str for text frames, bytes for binary frames.
            msg = ws.receive()
            if msg is None:
                # client closed
                break

            if isinstance(msg, (bytes, bytearray)):
                # Binary frame — raw PCM samples for the current utterance.
                if in_utterance:
                    pcm_buffer.extend(msg)
                else:
                    # ignore stray audio outside an utterance window
                    pass
                continue

            # Text frame — control message.
            try:
                ctrl = json.loads(msg)
            except json.JSONDecodeError:
                ws.send(json.dumps({"type": "error", "message": "invalid JSON control frame"}))
                continue

            ctrl_type = ctrl.get("type")

            if ctrl_type == "meta":
                sample_rate = int(ctrl.get("sampleRate", sample_rate))
                print(f"[ws] meta: sampleRate={sample_rate} channels={ctrl.get('channels')} format={ctrl.get('format')}")
                ws.send(json.dumps({"type": "meta_ack", "sampleRate": sample_rate}))

            elif ctrl_type == "start":
                pcm_buffer = bytearray()
                in_utterance = True
                utterance_start = time.time()
                print("[ws] utterance start")
                ws.send(json.dumps({"type": "start_ack"}))

            elif ctrl_type == "end":
                if not in_utterance:
                    ws.send(json.dumps({"type": "error", "message": "end without start"}))
                    continue
                in_utterance = False
                duration = time.time() - utterance_start
                num_samples = len(pcm_buffer) // 4  # float32 = 4 bytes/sample
                audio_seconds = num_samples / sample_rate if sample_rate else 0
                print(f"[ws] utterance end — {len(pcm_buffer)} bytes, "
                      f"{num_samples} samples, {audio_seconds:.2f}s of audio, "
                      f"capture took {duration:.2f}s")

                # ----- HOOK FOR WHISPER -----
                # When ready, convert pcm_buffer to numpy float32 and call
                # asr.transcribe_pcm(np.frombuffer(bytes(pcm_buffer), dtype=np.float32))
                # then ws.send the resulting Transcript as JSON.
                # ----------------------------

                ws.send(json.dumps({
                    "type": "utterance_received",
                    "bytes": len(pcm_buffer),
                    "samples": num_samples,
                    "seconds": round(audio_seconds, 3),
                }))
                pcm_buffer = bytearray()

            elif ctrl_type == "ping":
                ws.send(json.dumps({"type": "pong"}))

            else:
                ws.send(json.dumps({"type": "error", "message": f"unknown control type: {ctrl_type}"}))

    except Exception as e:
        print(f"[ws] error: {e}")
    finally:
        print("[ws] client disconnected")


if __name__ == '__main__':
    # Run the Flask server on port 5000
    app.run(port=5000, debug=True)