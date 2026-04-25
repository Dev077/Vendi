"""Realtime ASR wrapper around faster-whisper.

Loaded once at app startup; `transcribe_pcm` is called per utterance.
Input is raw Float32 PCM (mono, 16 kHz, range [-1.0, 1.0]) — no files,
no decoding step.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from faster_whisper import WhisperModel


@dataclass
class Transcript:
    text: str
    avg_logprob: float
    no_speech_prob: float

    def is_confident(self, min_logprob: float = -1.0, max_no_speech: float = 0.6) -> bool:
        return self.avg_logprob >= min_logprob and self.no_speech_prob <= max_no_speech


class ASR:
    def __init__(
        self,
        model_size: str = "distil-large-v3",
        device: str = "cuda",
        compute_type: str = "float16",
        language: str = "en",
        initial_prompt: str | None = None,
    ) -> None:
        self.model = WhisperModel(model_size, device=device, compute_type=compute_type)
        self.language = language
        self.initial_prompt = initial_prompt

    def transcribe_pcm(self, pcm: bytes | np.ndarray) -> Transcript:
        audio = self._to_float32(pcm)

        segments, _info = self.model.transcribe(
            audio,
            language=self.language,
            beam_size=1,
            vad_filter=False,
            condition_on_previous_text=False,
            initial_prompt=self.initial_prompt,
        )
        segments = list(segments)

        if not segments:
            return Transcript(text="", avg_logprob=-99.0, no_speech_prob=1.0)

        text = " ".join(s.text.strip() for s in segments).strip()
        avg_logprob = sum(s.avg_logprob for s in segments) / len(segments)
        no_speech_prob = sum(s.no_speech_prob for s in segments) / len(segments)
        return Transcript(text=text, avg_logprob=avg_logprob, no_speech_prob=no_speech_prob)

    @staticmethod
    def _to_float32(pcm: bytes | np.ndarray) -> np.ndarray:
        if isinstance(pcm, np.ndarray):
            audio = pcm
        else:
            audio = np.frombuffer(pcm, dtype=np.float32)
        if audio.dtype != np.float32:
            audio = audio.astype(np.float32)
        return audio
