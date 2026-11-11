"""
Context window budgeting for OpenAI-compatible upstreams (e.g. self-hosted vLLM)
that enforce a hard `max_model_len`.

The upstream rejects requests where `prompt_tokens + max_tokens > max_model_len`
with errors like `max_tokens must be at least 1, got -N`. We pre-emptively:
  1. Count prompt tokens
  2. Reserve the requested output budget (`max_tokens`) in full
  3. Drop the oldest non-system messages until the *prompt* fits the remainder

The output is never shrunk to make room for input; only the input is trimmed.

A truncation notice is returned so the caller can surface it to the user.
"""

from __future__ import annotations

import logging
from typing import Optional

import tiktoken

log = logging.getLogger(__name__)


_ENCODING = None


def _get_encoding():
    global _ENCODING
    if _ENCODING is None:
        try:
            _ENCODING = tiktoken.get_encoding("cl100k_base")
        except Exception as e:
            log.warning(f"tiktoken encoding unavailable, falling back to char/4: {e}")
            _ENCODING = False
    return _ENCODING


def _count_text_tokens(text: str) -> int:
    enc = _get_encoding()
    if not enc:
        return max(1, len(text) // 4)
    try:
        return len(enc.encode(text, disallowed_special=()))
    except Exception:
        return max(1, len(text) // 4)


def _message_token_count(message: dict) -> int:
    """Approximate per-message tokens. Adds 4 tokens of OpenAI-style overhead."""
    content = message.get("content")
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        # Multimodal content parts — only count text parts.
        text = "".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        )
    else:
        text = ""
    return _count_text_tokens(text) + 4


def count_messages_tokens(messages: list[dict]) -> int:
    return sum(_message_token_count(m) for m in messages) + 2


def apply_context_budget(
    payload: dict,
    max_model_len: int,
    min_output_tokens: int = 256,
) -> Optional[dict]:
    """
    Mutate `payload` so it fits within `max_model_len`.

    Strategy: keep all `system` messages and the most recent user/assistant turns;
    drop the oldest non-system messages first. If a single remaining message still
    overflows, hard-truncate its text content from the start.

    Returns a notice dict when truncation happened, otherwise None:
        {"dropped_messages": int, "truncated_message": bool,
         "original_tokens": int, "final_tokens": int}
    """
    if max_model_len <= 0:
        return None

    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        return None

    has_max_tokens = "max_tokens" in payload
    has_max_completion_tokens = "max_completion_tokens" in payload
    requested_max = payload.get("max_tokens") or payload.get("max_completion_tokens")
    target_output = requested_max if isinstance(requested_max, int) else min_output_tokens
    # Reserve the full requested output; never shrink output to make room for input.
    # We only cap the reservation at max_model_len so prompt_budget stays non-negative.
    target_output = max(min_output_tokens, min(target_output, max_model_len))

    original_tokens = count_messages_tokens(messages)
    if original_tokens + target_output <= max_model_len:
        return None

    system_messages = [m for m in messages if m.get("role") == "system"]
    other_messages = [m for m in messages if m.get("role") != "system"]

    # Budget for the prompt is whatever remains after fully reserving the output.
    # This is the key: input is trimmed to fit around the output, not the reverse.
    prompt_budget = max(0, max_model_len - target_output)
    system_tokens = sum(_message_token_count(m) for m in system_messages) + 2

    if system_tokens >= prompt_budget:
        # Pathological: system prompt alone overflows. Keep it but force min output.
        log.warning(
            "System prompt (%d tokens) exceeds prompt budget (%d).",
            system_tokens,
            prompt_budget,
        )
        kept_others: list[dict] = []
    else:
        kept_others = []
        running = system_tokens
        for msg in reversed(other_messages):
            cost = _message_token_count(msg)
            if running + cost > prompt_budget:
                break
            kept_others.append(msg)
            running += cost
        kept_others.reverse()

    dropped = len(other_messages) - len(kept_others)
    truncated_single = False

    if not kept_others and other_messages:
        # Need to keep at least the most recent user message; hard-truncate its text.
        last = dict(other_messages[-1])
        remaining = max(64, prompt_budget - system_tokens - 4)
        content = last.get("content")
        if isinstance(content, str):
            enc = _get_encoding()
            if enc:
                try:
                    tokens = enc.encode(content, disallowed_special=())
                    last["content"] = enc.decode(tokens[-remaining:])
                except Exception:
                    last["content"] = content[-(remaining * 4):]
            else:
                last["content"] = content[-(remaining * 4):]
            kept_others = [last]
            truncated_single = True
            dropped = len(other_messages) - 1

    new_messages = system_messages + kept_others
    final_prompt_tokens = count_messages_tokens(new_messages)

    # Preserve the requested output. Only fall back to the space-constrained value
    # if the trimmed prompt still leaves less room than the output we wanted —
    # this only happens at token-count approximation edges, and the upstream hard
    # limit (prompt + max_tokens <= max_model_len) must still be respected.
    headroom = max_model_len - final_prompt_tokens
    output_budget = target_output
    if headroom < output_budget:
        output_budget = max(min_output_tokens, headroom)

    payload["messages"] = new_messages
    if has_max_completion_tokens:
        payload["max_completion_tokens"] = output_budget
    elif has_max_tokens:
        payload["max_tokens"] = output_budget

    return {
        "dropped_messages": max(0, dropped),
        "truncated_message": truncated_single,
        "original_tokens": original_tokens,
        "final_tokens": final_prompt_tokens,
        "max_model_len": max_model_len,
    }


def format_truncation_notice(notice: dict, lang: str = "en") -> str:
    """Context truncation notices are intentionally hidden from chat output."""
    return ""
