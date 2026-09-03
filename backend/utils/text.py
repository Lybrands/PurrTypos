"""
Text processing utilities — ported from toolExecutor.js helpers.
"""

from __future__ import annotations

import json
import re
from typing import Any


def extract_text_from_lexical(raw: str | Any) -> str:
    """Extract plain text from a Lexical editor JSON state."""
    try:
        state = json.loads(raw) if isinstance(raw, str) else raw

        def _extract(node: dict) -> str:
            if node.get("type") == "text":
                return node.get("text", "")
            children = node.get("children")
            if children:
                return "".join(_extract(c) for c in children)
            return ""

        text = _extract(state.get("root", {}))
        return re.sub(r"\n{3,}", "\n\n", text).strip()
    except Exception:
        return raw if isinstance(raw, str) else ""


def format_characters_as_text(characters: list[dict]) -> str:
    """人物档案 Markdown 拼接（人物档案已 Markdown 化，按人物分节直出）。"""
    if not characters:
        return ""
    parts: list[str] = []
    for c in characters:
        lines = [f"### {c.get('name', '未命名')}（人物ID:{c.get('id', '')}）"]
        tags = str(c.get("tags") or "").strip()
        if tags:
            lines.append(f"标签：{tags}")
        profile = str(c.get("profile_md") or "").strip()
        if profile:
            lines.append(profile)
        parts.append("\n".join(lines))
    return "\n\n---\n\n".join(parts)


def format_setting_entities_as_text(
    entities: list[dict], type_labels: dict[str, str] | None = None
) -> str:
    """世界设定实体 Markdown 拼接（地点 / 势力 / 物品 / 其他）。"""
    if not entities:
        return ""
    labels = type_labels or {}
    parts: list[str] = []
    for e in entities:
        type_label = labels.get(str(e.get("entity_type") or ""), e.get("entity_type") or "")
        lines = [
            f"### [{type_label}] {e.get('name', '未命名')}（实体ID:{e.get('id', '')}）"
        ]
        tags = str(e.get("tags") or "").strip()
        if tags:
            lines.append(f"标签：{tags}")
        profile = str(e.get("profile_md") or "").strip()
        if profile:
            lines.append(profile)
        parts.append("\n".join(lines))
    return "\n\n---\n\n".join(parts)
