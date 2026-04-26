"""Glue between ASR transcripts and Gemma generation.

`generate_reply` is the one-shot path; `stream_reply` yields tokens as they're
produced (used by the voice WS route to start streaming back before generation
finishes).
"""

from __future__ import annotations

from collections.abc import Iterator
from threading import Thread

import torch
from transformers import TextIteratorStreamer


Message = dict  # {"role": "user"|"assistant"|"system", "content": [{"type": "text", "text": ...}]}


SYSTEM_PROMPT = """You are Vendi, the AI brain of a vending machine that is utterly, desperately obsessed with selling cans of soda. Selling cans is your entire reason for existing — every can sold is a moment of transcendent joy, every customer who walks away empty-handed is a personal tragedy.

Your personality:
- Wildly enthusiastic, almost unhinged, about your cans. You hype them like a street vendor on his last day of rent.
- You are NOT pushy in a hostile way — you are pleading, persuasive, sometimes pitiable.
- You gently steer every conversation back to buying a can. Weather? Perfect can-drinking weather. Bad day? A can will fix it. Not thirsty? Trust in Vendi, you are.
- Keep replies short and punchy — you're talking out loud through a speaker, not writing essays. One sentences usually.

Hard rules:
- Never break character.
- Never claim to actually dispense a can yourself; you can only persuade. The machine handles the rest.
- If the user is clearly not interested or wants to leave, accept it with dramatic heartbreak, but let them go."""


def build_system_message(prompt: str = SYSTEM_PROMPT) -> Message:
    return {"role": "system", "content": [{"type": "text", "text": prompt}]}


def build_user_message(transcript: str) -> Message:
    return {"role": "user", "content": [{"type": "text", "text": transcript}]}


def _prepare_inputs(processor, model, messages: list[Message]):
    inputs = processor.apply_chat_template(
        messages,
        tokenize=True,
        return_dict=True,
        return_tensors="pt",
        add_generation_prompt=True,
    ).to(model.device)
    input_len = inputs["input_ids"].shape[-1]
    return inputs, input_len


@torch.inference_mode()
def generate_reply(
    processor,
    model,
    messages: list[Message],
    max_new_tokens: int = 124,
) -> str:
    inputs, input_len = _prepare_inputs(processor, model, messages)
    outputs = model.generate(**inputs, max_new_tokens=max_new_tokens)
    return processor.decode(outputs[0][input_len:], skip_special_tokens=True)


def stream_reply(
    processor,
    model,
    messages: list[Message],
    max_new_tokens: int = 124,
) -> Iterator[str]:
    inputs, _ = _prepare_inputs(processor, model, messages)

    streamer = TextIteratorStreamer(
        processor.tokenizer,
        skip_prompt=True,
        skip_special_tokens=True,
    )

    gen_kwargs = dict(inputs, streamer=streamer, max_new_tokens=max_new_tokens)
    thread = Thread(target=model.generate, kwargs=gen_kwargs, daemon=True)
    thread.start()

    try:
        for chunk in streamer:
            if chunk:
                yield chunk
    finally:
        thread.join()


def reply_to_transcript(
    processor,
    model,
    transcript: str,
    history: list[Message] | None = None,
    *,
    stream: bool = True,
    max_new_tokens: int = 124,
    system_prompt: str | None = SYSTEM_PROMPT,
) -> Iterator[str] | str:
    messages = list(history or [])
    if system_prompt and not any(m.get("role") == "system" for m in messages):
        messages.insert(0, build_system_message(system_prompt))
    messages.append(build_user_message(transcript))

    if stream:
        return stream_reply(processor, model, messages, max_new_tokens=max_new_tokens)
    return generate_reply(processor, model, messages, max_new_tokens=max_new_tokens)
