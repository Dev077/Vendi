"""TTS interface + backend factory.

Backends implement `TTS`. Swap providers by changing `TTS_BACKEND` (env) —
callers only depend on this module.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Protocol


class TTS(Protocol):
    sample_rate: int

    def synthesize(self, text: str) -> bytes:
        """Return 16-bit mono PCM bytes for `text`."""
        ...

    def synthesize_stream(self, text: str) -> Iterator[bytes]:
        """Yield 16-bit mono PCM chunks as they're produced."""
        ...


def load_tts(backend: str | None = None) -> TTS:
    backend = (backend or os.getenv("TTS_BACKEND") or "piper").lower()

    if backend == "piper":
        from backend.audio.tts_piper import PiperTTS
        return PiperTTS()
    if backend == "elevenlabs":
        from backend.audio.tts_elevenlabs import ElevenLabsTTS
        return ElevenLabsTTS()

    raise ValueError(f"unknown TTS_BACKEND: {backend!r}")
