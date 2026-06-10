"""
Book style → system prompt fragment.

Layer 1（强制注入）：在写作专家 system prompt 顶部拼入「本书风格基调」精简摘要；
仅当字段非空时输出对应行，避免空段噪音。
"""

from __future__ import annotations

import json
from typing import Any


def _norm_lines(s: str, *, max_lines: int = 6) -> list[str]:
    if not s:
        return []
    out: list[str] = []
    for raw in str(s).splitlines():
        line = raw.strip().lstrip("-•·").strip()
        if line:
            out.append(line)
        if len(out) >= max_lines:
            break
    return out


def build_book_style_appendix(
    style: dict[str, Any] | None,
    *,
    writing_chapters: list[dict[str, Any]] | None = None,
) -> str:
    """风格基调 → 强制注入文本；style=None 或全空 → 返回 ''."""
    if not style:
        return ""

    pov = str(style.get("pov") or "").strip()
    tone = str(style.get("tone") or "").strip()
    pace = str(style.get("pace") or "").strip()
    banned = _norm_lines(str(style.get("banned_rules") or ""), max_lines=8)
    notes = str(style.get("free_notes") or "").strip()

    ref_ids_raw = str(style.get("reference_chapter_ids") or "").strip()
    ref_titles: list[str] = []
    if ref_ids_raw and writing_chapters:
        try:
            ids = json.loads(ref_ids_raw) if ref_ids_raw.startswith("[") else []
        except Exception:
            ids = []
        if isinstance(ids, list) and ids:
            id_to_title = {
                str(c.get("id")): str(c.get("title") or "").strip() for c in writing_chapters
            }
            for cid in ids[:5]:
                t = id_to_title.get(str(cid), "")
                if t:
                    ref_titles.append(t)

    if not (pov or tone or pace or banned or notes or ref_titles):
        return ""

    lines: list[str] = ["【本书风格基调 — 强制遵守】"]
    if pov:
        lines.append(f"- 视角：{pov}")
    if tone:
        lines.append(f"- 语调：{tone}")
    if pace:
        lines.append(f"- 节奏：{pace}")
    if banned:
        lines.append("- 禁忌（任何输出禁止违反）：")
        for b in banned:
            lines.append(f"  · {b}")
    if ref_titles:
        bits = "、".join(f"《{t}》" for t in ref_titles)
        lines.append(
            f"- 参考章节（语感对齐）：{bits}（必要时用 getChapterContent 读取后再下笔）"
        )
    if notes:
        lines.append(f"- 作者备注：{notes}")

    return "\n".join(lines)
