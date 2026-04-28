from __future__ import annotations

import os
from collections.abc import Iterator

from elevenlabs.client import ElevenLabs

class ElevenLabsTTS:
    def __init__(
        self,
        api_key: str | None = None,
        voice_id: str | None = None,
        model_id: str | None = None,
        output_format: str | None = None,
    ) -> None:
        self.api_key = api_key or os.getenv("ELEVENLABS_API_KEY")
        self.voice_id = voice_id or os.getenv("ELEVENLABS_VOICE_ID")
        self.model_id = model_id or os.getenv("ELEVENLABS_MODEL_ID", "eleven_multilingual_v2")
        self.output_format = output_format or os.getenv("ELEVENLABS_OUTPUT_FORMAT", "pcm_16000")

        if not self.api_key:
            raise RuntimeError("ELEVENLABS_API_KEY is not set")
        if not self.voice_id:
            raise RuntimeError("ELEVENLABS_VOICE_ID is not set")

        self.client = ElevenLabs(api_key=self.api_key)

        # Keep sample_rate aligned with output_format
        self.sample_rate = 16000
        if self.output_format == "pcm_22050":
            self.sample_rate = 22050
        elif self.output_format == "pcm_24000":
            self.sample_rate = 24000
        elif self.output_format == "pcm_44100":
            self.sample_rate = 44100

    def synthesize(self, text: str) -> bytes:
        return b"".join(self.synthesize_stream(text))

    def synthesize_stream(self, text: str) -> Iterator[bytes]:
        stream = self.client.text_to_speech.convert(
            voice_id=self.voice_id,
            model_id=self.model_id,
            text=text,
            output_format=self.output_format,  # IMPORTANT: choose pcm_* not mp3
        )
        for chunk in stream:
            if chunk:
                yield chunk