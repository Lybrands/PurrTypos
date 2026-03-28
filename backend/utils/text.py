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


def extract_latest_paragraph(text: str) -> str:
    src = (text or "").replace("\r\n", "\n").strip()
    if not src:
        return ""
    paras = [p.strip() for p in re.split(r"\n{2,}", src) if p.strip()]
    return paras[-1] if paras else src


def format_chapters_as_text(chapters: list[dict]) -> str:
    """Build an indented tree string from a flat list with parent_id."""
    if not chapters:
        return ""

    def _build(parent_id: str | None, depth: int) -> str:
        items = [
            c for c in chapters
            if (c.get("parent_id") or None) == parent_id
        ]
        lines: list[str] = []
        for c in items:
            indent = "  " * depth
            children = _build(c.get("id"), depth + 1)
            line = f"{indent}{c.get('title', '')}"
            if children:
                line += "\n" + children
            lines.append(line)
        return "\n".join(lines)

    return _build(None, 0)


def format_characters_as_text(characters: list[dict]) -> str:
    if not characters:
        return ""
    parts: list[str] = []
    for c in characters:
        segs = [f"【{c.get('name', '未命名')}】", f"人物ID:{c.get('id', '')}"]
        for key, label in [
            ("gender", "性别"), ("age", "年龄"), ("occupation", "职业"),
            ("personality", "性格"), ("appearance", "外貌"), ("origin", "来历"),
            ("background", "背景"), ("biography", "经历"), ("tags", "标签"),
            ("remark", "备注"),
        ]:
            val = c.get(key)
            if val:
                segs.append(f"{label}：{val}")
        parts.append("，".join(segs))
    return "\n".join(parts)
