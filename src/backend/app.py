import os
import re
import json
import traceback

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
from backend.model.generate import (
    SYSTEM_PROMPT,
    build_system_message,
    build_user_message,
    generate_reply_dual,
    reply_from_history,
)
from backend.model.loader import load_model
from backend.tools.dispenser import TOOL_SCHEMAS, Dispenser, build_dispatch

load_dotenv()

_SENTENCE_END = re.compile(r"[.!?\n]")

# Gemma 4 tool-call format per the chat template:
#   <|tool_call>call:NAME{ARG_KEY:<|"|>VAL<|"|>, ...}<tool_call|>
# We only register zero-arg tools today, so the args body is `{}`. The pattern
# tolerates whitespace and an optional args body just in case.
_TOOL_CALL_RE = re.compile(
    r"<\|tool_call>\s*call:(?P<name>\w+)\s*\{(?P<args>.*?)\}\s*<tool_call\|>",
    re.DOTALL,
)

# Heavy components — loaded lazily on first WS connection so the vision
# endpoint stays usable without GPU/model deps.
_voice_components: dict | None = None


def _get_voice_components() -> dict:
    global _voice_components
    if _voice_components is None:
        print("[voice] loading ASR, Gemma, TTS, and dispenser...")
        processor, model = load_model()
        # Hard-fail here if the Arduino isn't connected — voice is useless
        # without the motor anyway.
        dispenser = Dispenser()
        _voice_components = {
            "asr": ASR(),
            "processor": processor,
            "model": model,
            "tts": load_tts(),
            "dispenser": dispenser,
            "tools": TOOL_SCHEMAS,
            "dispatch": build_dispatch(dispenser),
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
            _, encoded_image = cv2.imencode('.jpg', frame)
            content = encoded_image.tobytes()
            vision_image = vision.Image(content=content)

            # Call Google Cloud Vision API — try object localization first,
            # then fall back to face detection (close-up webcam framing
            # often returns "Face" / "Head" rather than "Person").
            response = client.object_localization(image=vision_image)
            objects = response.localized_object_annotations

            # Anything in this set counts as "a human is here".
            HUMAN_LABELS = {"person", "face", "head", "human", "man", "woman", "boy", "girl"}

            detected = [(obj.name, round(obj.score * 100, 1)) for obj in objects]
            print(f"[vision] objects: {detected}")
            result["objects"] = detected

            for obj in objects:
                if obj.name.lower() in HUMAN_LABELS and obj.score >= 0.5:
                    result["human_detected"] = True
                    result["confidence"] = round(obj.score * 100, 1)
                    result["matched"] = obj.name
                    break

            # Fallback: explicit face detection if no human-ish object found.
            if not result["human_detected"]:
                face_resp = client.face_detection(image=vision_image)
                faces = face_resp.face_annotations
                print(f"[vision] faces: {len(faces)}")
                if faces:
                    result["human_detected"] = True
                    result["confidence"] = round(faces[0].detection_confidence * 100, 1)
                    result["matched"] = "Face (face_detection)"

            result["api_called"] = True
            result["message"] = (
                f"API called: Human found ({result.get('matched')})!"
                if result["human_detected"]
                else "API called: False alarm."
            )
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


def _stream_text_to_tts(ws, tts, text: str) -> None:
    """Send `text` as a single token frame, then speak it sentence by sentence.

    Used to replay text that was generated non-streaming (phase 1 of a turn).
    The text is already complete, so there's nothing to gain from artificial
    chunking — we just preserve the per-sentence TTS cadence.
    """
    import json
    if not text:
        return
    ws.send(json.dumps({"type": "token", "text": text}))
    sentence_buf = text
    while True:
        m = _SENTENCE_END.search(sentence_buf)
        if not m:
            break
        sentence, sentence_buf = sentence_buf[: m.end()], sentence_buf[m.end():]
        _speak(ws, tts, sentence)
    if sentence_buf.strip():
        _speak(ws, tts, sentence_buf)


def _stream_iter_to_tts(ws, tts, chunks) -> str:
    """Stream a token iterator to the client and TTS; return the joined text."""
    import json
    pieces: list[str] = []
    sentence_buf = ""
    for chunk in chunks:
        pieces.append(chunk)
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
    return "".join(pieces)


def _parse_tool_call(raw: str):
    """Return (pre_text, name, args_dict) if a tool call is present, else None.

    `pre_text` is the human-readable text the model emitted before the tool
    call (rare, but we speak it if present). Special-token noise outside the
    tool-call match is stripped.
    """
    m = _TOOL_CALL_RE.search(raw)
    if not m:
        return None
    pre = raw[: m.start()]
    pre_clean = re.sub(r"<\|.*?\|>|<[^>]+\|>", "", pre).strip()
    args_body = m.group("args").strip()
    # Today we only register zero-arg tools; richer parsing can be added later.
    args: dict = {} if not args_body else {"_raw": args_body}
    return pre_clean, m.group("name"), args


def _run_turn(ws, history: list, user_text: str) -> None:
    """Two-phase turn: tool-aware non-streaming pass, then streaming reply.

    1. Run a non-streaming generation with the tool schemas exposed.
    2. If the model emitted a tool call: dispatch it (fire-and-forget for the
       motor), append the assistant `tool_calls`/`tool_responses` message to
       history, then a second streaming generation (no tools) for the spoken
       follow-up.
    3. Otherwise: replay the already-generated text through TTS sentence by
       sentence so the UX still feels live.
    """
    import json
    comps = _get_voice_components()
    processor = comps["processor"]
    model = comps["model"]
    tts = comps["tts"]
    tools = comps["tools"]
    dispatch = comps["dispatch"]

    if not any(m.get("role") == "system" for m in history):
        history.insert(0, build_system_message(SYSTEM_PROMPT))
    history.append(build_user_message(user_text))

    raw, clean = generate_reply_dual(processor, model, history, tools=tools)
    parsed = _parse_tool_call(raw)

    if parsed is None:
        _stream_text_to_tts(ws, tts, clean)
        history.append({"role": "assistant", "content": [{"type": "text", "text": clean}]})
        ws.send(json.dumps({"type": "done"}))
        return

    pre_text, name, args = parsed
    print(f"[tool] model called {name}({args})")

    if pre_text:
        _stream_text_to_tts(ws, tts, pre_text)

    handler = dispatch.get(name)
    if handler is None:
        # Model hallucinated a tool. Surface as an error response so it can recover.
        response = {"error": f"unknown tool: {name}"}
    else:
        try:
            response = handler(**args)
        except Exception as e:
            response = {"error": str(e)}

    history.append(
        {
            "role": "assistant",
            "tool_calls": [{"function": {"name": name, "arguments": args}}],
            "tool_responses": [{"name": name, "response": response}],
        }
    )

    spoken = _stream_iter_to_tts(
        ws,
        tts,
        reply_from_history(processor, model, history, stream=True),
    )
    history.append({"role": "assistant", "content": [{"type": "text", "text": spoken}]})
    ws.send(json.dumps({"type": "done"}))


def _handle_wake(ws, history: list) -> None:
    _run_turn(ws, history, _WAKE_CUE)
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def _handle_utterance(ws, pcm_buffer: bytearray, history: list, discard: bool) -> None:
    if discard or not pcm_buffer:
        return

    comps = _get_voice_components()
    asr = comps["asr"]

    audio = np.frombuffer(bytes(pcm_buffer), dtype=np.float32)
    transcript = asr.transcribe_pcm(audio)

    if not transcript.text or not transcript.is_confident():
        print(f"[voice] dropped low-confidence: {transcript.text!r} "
              f"logprob={transcript.avg_logprob:.2f} no_speech={transcript.no_speech_prob:.2f}")
        return

    ws.send(json.dumps({"type": "transcript", "text": transcript.text}))
    _run_turn(ws, history, transcript.text)

    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def _safe_send(ws, payload) -> bool:
    """Send and return False if the socket is already closed instead of raising."""
    try:
        ws.send(payload)
        return True
    except Exception as e:
        print(f"[ws] send failed (peer likely gone): {type(e).__name__}: {e}")
        return False


@sock.route('/ws/audio')
def audio_socket(ws):
    print("[ws] client connected")
    pcm_buffer = bytearray()
    sample_rate = 16000  # default; client should override via meta
    in_utterance = False
    utterance_start = 0.0
    history: list = []
    exit_reason = "client closed (None frame)"

    try:
        while True:
            # receive() returns str for text frames, bytes for binary frames.
            msg = ws.receive()
            if msg is None:
                break

            if isinstance(msg, (bytes, bytearray)):
                # Binary frame — raw PCM samples for the current utterance.
                if in_utterance:
                    pcm_buffer.extend(msg)
                continue

            # Text frame — control message.
            try:
                ctrl = json.loads(msg)
            except json.JSONDecodeError:
                _safe_send(ws, json.dumps({"type": "error", "message": "invalid JSON control frame"}))
                continue

            ctrl_type = ctrl.get("type")

            if ctrl_type == "meta":
                sample_rate = int(ctrl.get("sampleRate", sample_rate))
                print(f"[ws] meta: sampleRate={sample_rate} channels={ctrl.get('channels')} format={ctrl.get('format')}")
                _safe_send(ws, json.dumps({"type": "meta_ack", "sampleRate": sample_rate}))

            elif ctrl_type == "start":
                pcm_buffer = bytearray()
                in_utterance = True
                utterance_start = time.time()
                print("[ws] utterance start")
                _safe_send(ws, json.dumps({"type": "start_ack"}))

            elif ctrl_type == "end":
                if not in_utterance:
                    _safe_send(ws, json.dumps({"type": "error", "message": "end without start"}))
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
                    print(f"[voice] utterance error: {type(e).__name__}: {e}")
                    traceback.print_exc()
                    _safe_send(ws, json.dumps({"type": "error", "stage": "utterance", "message": f"{type(e).__name__}: {e}"}))
                pcm_buffer = bytearray()

            elif ctrl_type == "wake":
                print("[ws] wake — generating opening pitch")
                try:
                    _handle_wake(ws, history)
                except Exception as e:
                    print(f"[voice] wake error: {type(e).__name__}: {e}")
                    traceback.print_exc()
                    _safe_send(ws, json.dumps({"type": "error", "stage": "wake", "message": f"{type(e).__name__}: {e}"}))

            elif ctrl_type == "ping":
                _safe_send(ws, json.dumps({"type": "pong"}))

            else:
                _safe_send(ws, json.dumps({"type": "error", "message": f"unknown control type: {ctrl_type}"}))

    except Exception as e:
        exit_reason = f"loop exception: {type(e).__name__}: {e}"
        traceback.print_exc()
    finally:
        print(f"[ws] client disconnected — {exit_reason}")


if __name__ == '__main__':
    # Run the Flask server on port 5000
    app.run(port=5000, debug=True, use_reloader=False)
