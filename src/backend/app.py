import os
import re

os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

import torch
from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_sock import Sock
import cv2
import numpy as np
import base64
import time
from dotenv import load_dotenv
from google.cloud import vision

from backend.audio.asr import ASR
from backend.audio.tts import load_tts
from backend.model.generate import reply_to_transcript
from backend.model.loader import load_model

load_dotenv()

_SENTENCE_END = re.compile(r"[.!?\n]")

# Heavy components — loaded lazily on first WS connection so the vision
# endpoint stays usable without GPU/model deps.
_voice_components: dict | None = None


def _get_voice_components() -> dict:
    global _voice_components
    if _voice_components is None:
        print("[voice] loading ASR, Gemma, and TTS...")
        processor, model = load_model()
        _voice_components = {
            "asr": ASR(),
            "processor": processor,
            "model": model,
            "tts": load_tts(),
        }
        print("[voice] ready")
    return _voice_components

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
# WebSocket endpoint for streaming audio in/out.
#
# Client → server:
#   - {"type": "meta", "sampleRate": 16000, "channels": 1, "format": "f32"}
#   - {"type": "start"}                begin a new utterance
#   - binary Float32 LE PCM            mic frames during an utterance
#   - {"type": "end"} | {"type": "end", "discard": true}
#
# Server → client:
#   - {"type": "transcript", "text": ...}
#   - {"type": "token", "text": ...}
#   - {"type": "audio_start", "sample_rate": N}    precedes binary audio
#   - binary int16 LE mono PCM                     TTS frames
#   - {"type": "audio_end"}                        closes one sentence's audio
#   - {"type": "done"}
#   - {"type": "error", "message": ...}
# ---------------------------------------------------------------------------
def _speak(ws, tts, text: str) -> None:
    text = text.strip()
    if not text:
        return
    import json
    ws.send(json.dumps({"type": "audio_start", "sample_rate": tts.sample_rate}))
    for pcm in tts.synthesize_stream(text):
        if pcm:
            ws.send(pcm)
    ws.send(json.dumps({"type": "audio_end"}))


# Internal stage-direction sent to the LLM on wake. Not shown to the user;
# the LLM treats it as instructions for its opening line.
_WAKE_CUE = (
    "(STAGE DIRECTION: A customer has just walked up to the vending machine. "
    "They have not spoken yet. Deliver a short, punchy opening pitch to grab "
    "their attention and lure them in — like a hot-dog vendor at a baseball "
    "game. One max. Stay in character as Vendi.)"
)


def _handle_wake(ws, history: list) -> None:
    import json
    comps = _get_voice_components()
    processor = comps["processor"]
    model = comps["model"]
    tts = comps["tts"]

    reply_chunks: list[str] = []
    sentence_buf = ""
    for chunk in reply_to_transcript(processor, model, _WAKE_CUE, history=history, stream=True):
        reply_chunks.append(chunk)
        ws.send(json.dumps({"type": "token", "text": chunk}))
        sentence_buf += chunk
        while True:
            m = _SENTENCE_END.search(sentence_buf)
            if not m:
                break
            sentence, sentence_buf = sentence_buf[: m.end()], sentence_buf[m.end():]
            _speak(ws, tts, sentence)

    if sentence_buf.strip():
        _speak(ws, tts, sentence_buf)

    history.append({"role": "user", "content": [{"type": "text", "text": _WAKE_CUE}]})
    history.append({"role": "assistant", "content": [{"type": "text", "text": "".join(reply_chunks)}]})
    ws.send(json.dumps({"type": "done"}))

    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def _handle_utterance(ws, pcm_buffer: bytearray, history: list, discard: bool) -> None:
    import json
    if discard or not pcm_buffer:
        return

    comps = _get_voice_components()
    asr = comps["asr"]
    processor = comps["processor"]
    model = comps["model"]
    tts = comps["tts"]

    audio = np.frombuffer(bytes(pcm_buffer), dtype=np.float32)
    transcript = asr.transcribe_pcm(audio)

    if not transcript.text or not transcript.is_confident():
        print(f"[voice] dropped low-confidence: {transcript.text!r} "
              f"logprob={transcript.avg_logprob:.2f} no_speech={transcript.no_speech_prob:.2f}")
        return

    ws.send(json.dumps({"type": "transcript", "text": transcript.text}))

    reply_chunks: list[str] = []
    sentence_buf = ""
    for chunk in reply_to_transcript(processor, model, transcript.text, history=history, stream=True):
        reply_chunks.append(chunk)
        ws.send(json.dumps({"type": "token", "text": chunk}))
        sentence_buf += chunk
        while True:
            m = _SENTENCE_END.search(sentence_buf)
            if not m:
                break
            sentence, sentence_buf = sentence_buf[: m.end()], sentence_buf[m.end():]
            _speak(ws, tts, sentence)

    if sentence_buf.strip():
        _speak(ws, tts, sentence_buf)

    history.append({"role": "user", "content": [{"type": "text", "text": transcript.text}]})
    history.append({"role": "assistant", "content": [{"type": "text", "text": "".join(reply_chunks)}]})
    ws.send(json.dumps({"type": "done"}))

    if torch.cuda.is_available():
        torch.cuda.empty_cache()


@sock.route('/ws/audio')
def audio_socket(ws):
    import json

    print("[ws] client connected")
    pcm_buffer = bytearray()
    sample_rate = 16000  # default; client should override via meta
    in_utterance = False
    utterance_start = 0.0
    history: list = []

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
                discard = bool(ctrl.get("discard"))
                print(f"[ws] utterance end — {num_samples} samples, "
                      f"{audio_seconds:.2f}s audio, capture {duration:.2f}s"
                      f"{' (discarded)' if discard else ''}")

                try:
                    _handle_utterance(ws, pcm_buffer, history, discard)
                except Exception as e:
                    print(f"[voice] error: {e}")
                    ws.send(json.dumps({"type": "error", "message": str(e)}))
                pcm_buffer = bytearray()

            elif ctrl_type == "wake":
                print("[ws] wake — generating opening pitch")
                try:
                    _handle_wake(ws, history)
                except Exception as e:
                    print(f"[voice] wake error: {e}")
                    ws.send(json.dumps({"type": "error", "message": str(e)}))

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
    app.run(port=5000, debug=True, use_reloader=False)
