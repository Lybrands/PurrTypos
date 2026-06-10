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
