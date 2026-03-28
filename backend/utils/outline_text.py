"""
Outline text aggregation for agent context — port of
electron/outlineTextForAgent.js.
"""

from __future__ import annotations

from typing import Any

from constants import OUTLINE_TYPE_LABEL
from database.crud.outlines import (
    get_chapter_outlines,
    get_global_outline,
    get_other_outlines,
    get_volume_outlines,
    get_writing_outline,
)


async def collect_text_outline_entries(
    db: Any,
    book_id: str,
    outline_ids: list[str] | None = None,
) -> list[dict]:
    """Collect outlines that have markdown_content filled in."""
    if not book_id or not str(book_id).strip():
        return []

    filter_set: set[str] | None = None
    if outline_ids and len(outline_ids) > 0:
        filter_set = {str(x) for x in outline_ids}

    entries: list[dict] = []

    def _consider(row: dict | None, allow_legacy_global: bool = False):
        if not row:
            return
        same_book = row.get("book_id") is not None and str(row["book_id"]) == str(book_id)
        legacy_global = bool(
            allow_legacy_global
            and row.get("type") == "global"
            and (row.get("book_id") is None or row.get("book_id") == "")
        )
        if not same_book and not legacy_global:
            return
        md = str(row.get("markdown_content") or "").strip()
        if not md:
            return
        if filter_set and str(row.get("id")) not in filter_set:
            return
        entries.append({
            "id": row.get("id"),
            "title": row.get("title", ""),
            "type": row.get("type", ""),
            "markdown": md,
        })

    _consider(await get_global_outline(db, book_id), allow_legacy_global=True)

    for vol in await get_volume_outlines(db, book_id) or []:
        _consider(vol)
        for ch in vol.get("chapters", []):
            _consider(ch)

    for o in await get_chapter_outlines(db, book_id) or []:
        _consider(o)
    for o in await get_other_outlines(db, book_id) or []:
        _consider(o)

    writing = await get_writing_outline(db, book_id)
    _consider(writing)

    if outline_ids and len(outline_ids) > 0 and entries:
        order = {str(oid): i for i, oid in enumerate(outline_ids)}
        entries.sort(key=lambda e: order.get(str(e["id"]), 999))

    return entries


def format_text_outline_for_agent(
    entries: list[dict], max_length: int = 32000
) -> str:
    if not entries:
        return (
            "（暂无文本大纲：各大纲的「文本大纲」标签页中尚未填写内容，"
            "或 outlineIds 与本书无匹配项。）"
        )
    parts = []
    for e in entries:
        label = OUTLINE_TYPE_LABEL.get(e.get("type", ""), e.get("type", "大纲"))
        title = e.get("title", "未命名")
        parts.append(f"【{label} · {title}】大纲ID:{e['id']}\n{e['markdown']}")

    text = "\n\n---\n\n".join(parts)
    if len(text) > max_length:
        text = (
            text[:max_length]
            + "\n…（已截断；可缩小 outlineIds 范围或调大 maxTextLength 分批获取）"
        )
    return text
