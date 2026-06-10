"""
Agent 工具的数据读取层：大纲 / 章节正文 / 写作目录的 DB 加载与整形。

只读、无 ctx 依赖；handler 在外面自己决定缓存与鉴权。
"""

from __future__ import annotations

from database.crud.articles import get_article
from database.crud.chapters import get_chapters
from database.crud.outlines import (
    get_chapter_outlines,
    get_global_outline,
    get_or_create_global_outline,
    get_or_create_writing_outline,
    get_volume_outlines,
)
from dependencies import get_db
from utils.outline_text import collect_text_outline_entries
from utils.text import (
    extract_text_from_lexical,
    format_chapters_as_text,
)


# ---------------------------------------------------------------------------
# Outline loaders
# ---------------------------------------------------------------------------

async def _load_outline_with_chapters(outline: dict) -> dict:
    chapters = await get_chapters(get_db(), outline["id"])
    return {"outline": outline, "chapters": chapters, "chaptersText": format_chapters_as_text(chapters)}


async def _load_all_outlines_for_book(book_id: str) -> dict:
    db = get_db()
    global_res = await get_global_outline(db, book_id)
    volume_res = await get_volume_outlines(db, book_id)
    chapter_res = await get_chapter_outlines(db, book_id)
    writing_res = await get_or_create_writing_outline(db, book_id)

    global_outline = await _load_outline_with_chapters(global_res) if global_res else None
    volume_outlines = []
    for vol in (volume_res or []):
        chapters_detail = []
        for ch in (vol.get("chapters") or []):
            chapters_detail.append(await _load_outline_with_chapters(ch))
        volume_outlines.append({**vol, "chapters_detail": chapters_detail})
    chapter_outlines = []
    for o in (chapter_res or []):
        chapter_outlines.append(await _load_outline_with_chapters(o))
    writing_outline = await _load_outline_with_chapters(writing_res) if writing_res else None
    return {
        "globalOutline": global_outline,
        "volumeOutlines": volume_outlines,
        "chapterOutlines": chapter_outlines,
        "writingOutline": writing_outline,
    }


async def _get_available_outlines(book_id: str) -> list[dict]:
    db = get_db()
    result: list[dict] = []
    seen: set[str] = set()

    def add_row(row: dict | None) -> None:
        if row is None:
            return
        oid = row.get("id")
        if oid is None or str(oid) in seen:
            return
        seen.add(str(oid))
        result.append(dict(row))

    add_row(await get_global_outline(db, book_id))
    for vol in await get_volume_outlines(db, book_id) or []:
        add_row({k: v for k, v in vol.items() if k != "chapters"})
        for ch in vol.get("chapters") or []:
            add_row(ch)
    for ch in await get_chapter_outlines(db, book_id) or []:
        add_row(ch)
    return result


async def _get_global_outline(book_id: str, max_text_length: int = 32000) -> dict | None:
    row = await get_or_create_global_outline(get_db(), book_id)
    if not row:
        return None
    md_raw = row.get("markdown_content") or ""
    md = md_raw[:max_text_length] + "\n…（已截断）" if isinstance(md_raw, str) and len(md_raw) > max_text_length else md_raw
    return {
        "success": True,
        "bookId": str(book_id),
        "outlineId": str(row["id"]),
        "title": row.get("title") or "总纲",
        "type": row.get("type") or "global",
        "markdown": md or "",
        "hasMarkdown": bool(md_raw and str(md_raw).strip()),
    }


async def _query_outline(book_id: str, outline_ids: list[str], max_text_length: int = 32000) -> dict:
    all_outlines = await _get_available_outlines(book_id)
    filter_set: set[str] | None = set(str(x) for x in outline_ids) if outline_ids else None
    targets = [o for o in all_outlines if filter_set is None or str(o.get("id")) in filter_set]
    text_entries = await collect_text_outline_entries(
        get_db(), book_id, list(filter_set) if filter_set else None,
    )
    by_id_text = {str(e["id"]): e["markdown"] for e in text_entries}
    outlines = []
    for o in targets:
        oid = str(o.get("id"))
        md = by_id_text.get(oid)
        trimmed = md[:max_text_length] + "\n…（已截断）" if isinstance(md, str) and len(md) > max_text_length else md
        outlines.append({
            "id": oid,
            "title": o.get("title", ""),
            "type": o.get("type", ""),
            "xmindData": o.get("xmind_data", "") if isinstance(o.get("xmind_data"), str) else "",
            "markdown": trimmed or "",
            "hasMarkdown": bool(md and str(md).strip()),
        })
    return {"success": True, "bookId": str(book_id), "total": len(outlines), "outlines": outlines}


# ---------------------------------------------------------------------------
# Chapter loaders
# ---------------------------------------------------------------------------

async def _read_chapter_plain_full(chapter_id: str) -> dict | None:
    row = await get_article(get_db(), chapter_id)
    if not row:
        return None
    raw = row.get("content") or ""
    return {"plainTextFull": extract_text_from_lexical(raw) if raw else ""}


async def _list_writing_chapters(book_id: str) -> dict:
    db = get_db()
    writing = await get_or_create_writing_outline(db, book_id)
    if not writing or not writing.get("id"):
        return {"success": False, "error": "未找到写作目录"}
    rows = await get_chapters(db, writing["id"]) or []
    parent_set: set[str] = set()
    for r in rows:
        pid = r.get("parent_id")
        if pid is not None and pid != "":
            parent_set.add(str(pid))
    items = []
    for r in rows:
        rid = str(r.get("id"))
        has_children = rid in parent_set
        items.append({
            "id": rid,
            "title": str(r.get("title") or ""),
            "parentId": None if r.get("parent_id") in (None, "") else str(r["parent_id"]),
            "level": int(r.get("level") or 1),
            "sort": int(r.get("sort") or 0),
            "nodeType": "volume" if has_children else "chapter",
            "hasChildren": has_children,
        })
    return {
        "success": True,
        "bookId": str(book_id),
        "writingOutlineId": str(writing["id"]),
        "total": len(items),
        "items": items,
    }
