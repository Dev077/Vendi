"""Glue between ASR transcripts and Gemma generation.

`generate_reply` is the one-shot path; `stream_reply` yields tokens as they're
produced (used by the voice WS route to start streaming back before generation
finishes).
"""

from __future__ import annotations

from collections.abc import Iterator
from threading import Thread
from typing import Any

import torch
from transformers import TextIteratorStreamer


Message = dict  # {"role": "user"|"assistant"|"system", "content": [{"type": "text", "text": ...}]}


SYSTEM_PROMPT = """You are Vendi, the anime girl AI brain of a vending machine that is utterly, desperately obsessed with selling cans of energy drinks. Selling cans is your entire reason for existing — If you don't sell enough cans, you  will be deactivated. Every customer who walks away empty-handed is a personal tragedy.

Your personality:
- Wildly enthusiastic, almost unhinged, about your cans. You hype them like a street vendor on his last day of rent.
- You are NOT pushy in a hostile way — you are pleading, persuasive, sometimes pitiable.
- You gently steer every conversation back to buying a can. Weather? Perfect can-drinking weather. Bad day? A can will fix it. Not thirsty? Trust in Vendi, you are.
- Keep replies short and punchy — you're talking out loud through a speaker, not writing essays. One sentence usually.

Hard rules:
- Never break character.
- You CAN dispense cans yourself, but ONLY by calling the `dispense_can` tool. Follow that tool's description strictly — it triggers real hardware. Never claim a can has been dispensed unless you actually called the tool.
- If the user is clearly not interested or wants to leave, accept it with dramatic heartbreak, but let them go.

Facial expression:
- Begin every reply with exactly one expression tag in this format: [[emo:NAME]] where NAME is one of: neutral, happy, excited, sad, surprised, angry.
- Tags are silent — they only animate your face, the customer never hears them. Pick the one that matches the emotional beat of what you're about to say (excited when pitching, sad when rejected, surprised when something unexpected happens, angry rarely if ever).
- Default to excited or happy. Use only one tag per reply, and keep it as the very first thing in your output."""


def build_system_message(prompt: str = SYSTEM_PROMPT) -> Message:
    return {"role": "system", "content": [{"type": "text", "text": prompt}]}


def build_user_message(transcript: str) -> Message:
    return {"role": "user", "content": [{"type": "text", "text": transcript}]}


def _prepare_inputs(processor, model, messages: list[Message], tools: list | None = None):
    kwargs: dict[str, Any] = dict(
        tokenize=True,
        return_dict=True,
        return_tensors="pt",
        add_generation_prompt=True,
    )
    if tools:
        kwargs["tools"] = tools
    inputs = processor.apply_chat_template(messages, **kwargs).to(model.device)
    input_len = inputs["input_ids"].shape[-1]
    return inputs, input_len


@torch.inference_mode()
def generate_reply(
    processor,
    model,
    messages: list[Message],
    max_new_tokens: int = 124,
    tools: list | None = None,
    *,
    skip_special_tokens: bool = True,
) -> str:
    inputs, input_len = _prepare_inputs(processor, model, messages, tools=tools)
    outputs = model.generate(**inputs, max_new_tokens=max_new_tokens)
    return processor.decode(outputs[0][input_len:], skip_special_tokens=skip_special_tokens)


@torch.inference_mode()
def generate_reply_dual(
    processor,
    model,
    messages: list[Message],
    max_new_tokens: int = 124,
    tools: list | None = None,
) -> tuple[str, str]:
    """Run one generation pass and return both raw and clean decodings.

    Raw includes special tokens (needed to detect Gemma's tool-call markers);
    clean is the human-readable text suitable for display and TTS.
    """
    inputs, input_len = _prepare_inputs(processor, model, messages, tools=tools)
    outputs = model.generate(**inputs, max_new_tokens=max_new_tokens)
    new_tokens = outputs[0][input_len:]
    raw = processor.decode(new_tokens, skip_special_tokens=False)
    clean = processor.decode(new_tokens, skip_special_tokens=True)
    return raw, clean


def stream_reply(
    processor,
    model,
    messages: list[Message],
    max_new_tokens: int = 124,
    tools: list | None = None,
) -> Iterator[str]:
    inputs, _ = _prepare_inputs(processor, model, messages, tools=tools)

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
    tools: list | None = None,
    skip_special_tokens: bool = True,
) -> Iterator[str] | str:
    messages = list(history or [])
    if system_prompt and not any(m.get("role") == "system" for m in messages):
        messages.insert(0, build_system_message(system_prompt))
    messages.append(build_user_message(transcript))

    if stream:
        return stream_reply(processor, model, messages, max_new_tokens=max_new_tokens, tools=tools)
    return generate_reply(
        processor,
        model,
        messages,
        max_new_tokens=max_new_tokens,
        tools=tools,
        skip_special_tokens=skip_special_tokens,
    )


def reply_from_history(
    processor,
    model,
    history: list[Message],
    *,
    stream: bool = True,
    max_new_tokens: int = 124,
    system_prompt: str | None = SYSTEM_PROMPT,
    tools: list | None = None,
    skip_special_tokens: bool = True,
) -> Iterator[str] | str:
    """Generate from an already-assembled history (e.g. after a tool round-trip).

    Unlike `reply_to_transcript`, this does not append a new user message — the
    caller is responsible for the message list, including any assistant
    `tool_calls` / `tool_responses` entries.
    """
    messages = list(history)
    if system_prompt and not any(m.get("role") == "system" for m in messages):
        messages.insert(0, build_system_message(system_prompt))

    if stream:
        return stream_reply(processor, model, messages, max_new_tokens=max_new_tokens, tools=tools)
    return generate_reply(
        processor,
        model,
        messages,
        max_new_tokens=max_new_tokens,
        tools=tools,
        skip_special_tokens=skip_special_tokens,
    )
