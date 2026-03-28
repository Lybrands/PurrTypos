"""
Session title generation — port of electron/sessionTitle.js.
"""

from __future__ import annotations

import re

SESSION_TITLE_MAX_CHARS = 10
SESSION_TITLE_TEMPERATURE = 1

SESSION_TITLE_SYSTEM_PROMPT = (
    "你是标题生成器。根据下面用户与助手的一轮对话摘录，生成一个简短对话标题。"
    "要求：严格不超过10个字（含标点）；只输出标题本身，不要引号、不要序号、不要解释。"
)


def normalize_session_title(raw: str) -> str:
    s = (raw or "").replace("\r\n", " ").replace("\n", " ").strip()
    s = re.sub(r'^[\s"\'「『【]+|[\s"\'」』】]+$', "", s).strip()
    first = re.split(r"[。！？!?]\s*|[\n\r]", s)[0]
    s = (first if first is not None else s).strip()
    if not s:
        return ""
    chars = list(s)
    return "".join(chars[:SESSION_TITLE_MAX_CHARS])
