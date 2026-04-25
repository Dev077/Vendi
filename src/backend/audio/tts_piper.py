"""Piper TTS backend.

Piper runs a local ONNX voice model — fast on CPU, no network. Set
`PIPER_VOICE` to the path of a `.onnx` voice file (the matching
`.onnx.json` config must sit next to it). Relative paths are resolved
against the repo root so they work regardless of cwd.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

from piper import PiperVoice

# tts_piper.py lives at src/backend/audio/ — three parents up is the repo root.
_REPO_ROOT = Path(__file__).resolve().parents[3]


class PiperTTS:
    def __init__(self, voice_path: str | None = None) -> None:
        voice_path = voice_path or os.getenv("PIPER_VOICE")
        if not voice_path:
            raise RuntimeError("PIPER_VOICE is not set (env or argument)")

        path = Path(voice_path)
        if not path.is_absolute():
            path = _REPO_ROOT / path
        if not path.is_file():
            raise FileNotFoundError(f"PIPER_VOICE not found: {path}")

        self.voice = PiperVoice.load(str(path))
        self.sample_rate = self.voice.config.sample_rate

    def synthesize(self, text: str) -> bytes:
        return b"".join(self.synthesize_stream(text))

    def synthesize_stream(self, text: str) -> Iterator[bytes]:
        for chunk in self.voice.synthesize(text):
            yield chunk.audio_int16_bytes
