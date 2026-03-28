"""
Stream chunk text extraction — extracted from duplicate logic in
subagentPipeline.js (textFromChatDelta / streamPartToText) and
main.js (appendModelContent).
"""

from __future__ import annotations

from typing import Any


def stream_part_to_text(part: Any) -> str:
    if part is None:
        return ""
    if isinstance(part, str):
        return part
    if isinstance(part, dict):
        if "text" in part and isinstance(part["text"], str):
            return part["text"]
        if "content" in part and isinstance(part["content"], str):
            return part["content"]
    return ""


def text_from_chat_delta(delta: dict | None) -> str:
    """Extract concatenable content text from a streaming delta object."""
    if not delta or not isinstance(delta, dict):
        return ""
    c = delta.get("content")
    if c is None or c == "":
        refusal = delta.get("refusal")
        if refusal and str(refusal).strip():
            return str(refusal)
        text = delta.get("text")
        if isinstance(text, str) and text:
            return text
        return ""
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "".join(stream_part_to_text(p) for p in c)
    return str(c)


def text_from_stream_choice0(choice0: dict | None) -> str:
    if not choice0:
        return ""
    from_delta = text_from_chat_delta(choice0.get("delta", {}))
    if from_delta:
        return from_delta
    msg = choice0.get("message")
    if isinstance(msg, dict):
        c = msg.get("content")
        if isinstance(c, str):
            return c
    text = choice0.get("text")
    if isinstance(text, str):
        return text
    return ""


def append_model_content(
    accumulated: str, delta_content: str, message_content: str | None = None
) -> tuple[str, str]:
    """
    Merge incremental content.  Returns ``(next_accumulated, emitted_delta)``.
    """
    cur = accumulated or ""
    d = delta_content or ""
    if d:
        return cur + d, d
    msg = message_content if isinstance(message_content, str) else ""
    if not msg:
        return cur, ""
    if not cur:
        return msg, msg
    if msg.startswith(cur):
        tail = msg[len(cur):]
        return msg, tail
    return cur, ""
