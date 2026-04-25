"""Loads the Gemma processor + model. Call `load_model()` once at app startup."""

from __future__ import annotations

import os

import torch
from dotenv import load_dotenv
from transformers import AutoModelForMultimodalLM, AutoProcessor


def load_model(model_id: str | None = None):
    load_dotenv()
    model_id = model_id or os.getenv("MODEL_ID")
    if not model_id:
        raise RuntimeError("MODEL_ID is not set (env or argument)")

    processor = AutoProcessor.from_pretrained(model_id)
    model = AutoModelForMultimodalLM.from_pretrained(
        model_id,
        dtype=torch.bfloat16,
        device_map="auto",
        low_cpu_mem_usage=True,
    )
    return processor, model


if __name__ == "__main__":
    # Smoke test: load the model and run a one-shot video prompt.
    processor, model = load_model()
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "video", "video": "https://github.com/bebechien/gemma/raw/refs/heads/main/videos/ForBiggerBlazes.mp4"},
                {"type": "text", "text": "Describe this video."},
            ],
        }
    ]
    inputs = processor.apply_chat_template(
        messages,
        tokenize=True,
        return_dict=True,
        return_tensors="pt",
        add_generation_prompt=True,
    ).to(model.device)
    input_len = inputs["input_ids"].shape[-1]
    outputs = model.generate(**inputs, max_new_tokens=512)
    response = processor.decode(outputs[0][input_len:], skip_special_tokens=False)
    print(response)
