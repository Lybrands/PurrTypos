"""Writing Agent 工具的数据读取适配器。

这里负责把现有 SQLite CRUD 结果整形成领域工具 handler 使用的稳定字典；
不持有请求级 ``ctx``，也不参与工具注册。
"""

from __future__ import annotations

from database.crud.chapters import get_chapters
from database.crud.outlines import (
    get_chapter_outlines,
    get_global_outline,
    get_writing_outline,
    get_volume_outlines,
)
from utils.outline_text import collect_text_outline_entries
from utils.text import extract_text_from_lexical


# ---------------------------------------------------------------------------
# Outline loaders
# ---------------------------------------------------------------------------

async def _get_available_outlines(db, book_id: str) -> list[dict]:
    result: list[dict] = []
    seen: set[str] = set()

    def add_row(row: dict | None) -> None:
        if row is None:
            return
        outline_id = row.get("id")
        if outline_id is None or str(outline_id) in seen:
            return
        seen.add(str(outline_id))
        result.append(dict(row))

    add_row(await get_global_outline(db, book_id))
    for volume in await get_volume_outlines(db, book_id) or []:
        add_row({key: value for key, value in volume.items() if key != "chapters"})
        for chapter in volume.get("chapters") or []:
            add_row(chapter)
    for chapter in await get_chapter_outlines(db, book_id) or []:
        add_row(chapter)
    return result


async def _get_global_outline(
    db,
    book_id: str,
    max_text_length: int = 32000,
) -> dict | None:
    # This loader backs a READ-policy tool. Missing initialization must stay
    # observable instead of creating persistent state without approval.
    row = await get_global_outline(db, book_id)
    if not row:
        return None
    markdown_raw = row.get("markdown_content") or ""
    markdown = (
        markdown_raw[:max_text_length] + "\n…（已截断）"
        if isinstance(markdown_raw, str) and len(markdown_raw) > max_text_length
        else markdown_raw
    )
    return {
        "success": True,
        "bookId": str(book_id),
        "outlineId": str(row["id"]),
        "title": row.get("title") or "总纲",
        "type": row.get("type") or "global",
        "markdown": markdown or "",
        "hasMarkdown": bool(markdown_raw and str(markdown_raw).strip()),
    }


async def _query_outline(
    db,
    book_id: str,
    outline_ids: list[str],
    max_text_length: int = 32000,
) -> dict:
    all_outlines = await _get_available_outlines(db, book_id)
    filter_set: set[str] | None = (
        {str(outline_id) for outline_id in outline_ids} if outline_ids else None
    )
    targets = [
        outline
        for outline in all_outlines
        if filter_set is None or str(outline.get("id")) in filter_set
    ]
    text_entries = await collect_text_outline_entries(
        db,
        book_id,
        list(filter_set) if filter_set else None,
    )
    text_by_id = {str(entry["id"]): entry["markdown"] for entry in text_entries}
    outlines = []
    for outline in targets:
        outline_id = str(outline.get("id"))
        markdown = text_by_id.get(outline_id)
        trimmed = (
            markdown[:max_text_length] + "\n…（已截断）"
            if isinstance(markdown, str) and len(markdown) > max_text_length
            else markdown
        )
        outlines.append({
            "id": outline_id,
            "title": outline.get("title", ""),
            "type": outline.get("type", ""),
            "xmindData": (
                outline.get("xmind_data", "")
                if isinstance(outline.get("xmind_data"), str)
                else ""
            ),
            "markdown": trimmed or "",
            "hasMarkdown": bool(markdown and str(markdown).strip()),
        })
    return {
        "success": True,
        "bookId": str(book_id),
        "total": len(outlines),
        "outlines": outlines,
    }


# ---------------------------------------------------------------------------
# Chapter loaders
# ---------------------------------------------------------------------------

async def _read_writing_chapters_for_book(
    db,
    book_id: str,
    chapter_ids: list[str],
) -> dict[str, dict]:
    """Load Writing chapters only when their outline belongs to ``book_id``.

    ``articles.chapter_id`` has no book column, so every content read must cross
    the ``outline_chapters -> outlines`` ownership boundary first.  A LEFT JOIN
    preserves the historical distinction between an owned chapter with no
    article yet and a chapter that does not belong to this book.
    """

    normalized_ids = list(dict.fromkeys(
        str(chapter_id).strip()
        for chapter_id in (chapter_ids or [])
        if str(chapter_id).strip()
    ))
    if not normalized_ids:
        return {}

    placeholders = ",".join("?" for _ in normalized_ids)
    rows = await db.fetch_all(
        "SELECT c.id AS chapter_id, c.outline_id, c.title, c.level, "
        "c.progress, c.sort, c.parent_id, a.id AS article_id, a.content "
        "FROM outline_chapters AS c "
        "JOIN outlines AS o ON o.id = c.outline_id "
        "LEFT JOIN articles AS a ON a.chapter_id = c.id "
        "WHERE o.book_id = ? AND o.type = 'writing' "
        f"AND c.id IN ({placeholders})",
        [str(book_id), *normalized_ids],
    )
    result: dict[str, dict] = {}
    for row in rows:
        chapter_id = str(row["chapter_id"])
        raw = row.get("content") or ""
        result[chapter_id] = {
            "id": chapter_id,
            "outline_id": str(row["outline_id"]),
            "title": str(row.get("title") or ""),
            "level": int(row.get("level") or 1),
            "progress": row.get("progress"),
            "sort": int(row.get("sort") or 0),
            "parent_id": (
                None
                if row.get("parent_id") in (None, "")
                else str(row["parent_id"])
            ),
            "articleExists": row.get("article_id") is not None,
            "plainTextFull": extract_text_from_lexical(raw) if raw else "",
        }
    return result


async def _read_writing_chapter_for_book(
    db,
    book_id: str,
    chapter_id: str,
) -> dict | None:
    rows = await _read_writing_chapters_for_book(
        db,
        book_id,
        [chapter_id],
    )
    return rows.get(str(chapter_id).strip())


async def _list_writing_chapter_rows_for_book(
    db,
    book_id: str,
    outline_id: str,
) -> list[dict]:
    """Return the authoritative nodes for one book-owned Writing outline."""

    return await db.fetch_all(
        "SELECT c.* FROM outline_chapters AS c "
        "JOIN outlines AS o ON o.id = c.outline_id "
        "WHERE o.id = ? AND o.book_id = ? AND o.type = 'writing' "
        "ORDER BY COALESCE(c.parent_id, c.id) ASC, c.sort ASC",
        [str(outline_id), str(book_id)],
    )


async def _list_writing_chapters(db, book_id: str) -> dict:
    # Listing is read-only. The explicit createWritingChapter flow remains the
    # owner of writing-outline initialization.
    writing = await get_writing_outline(db, book_id)
    if not writing or not writing.get("id"):
        return {"success": False, "error": "未找到写作目录"}
    rows = await get_chapters(db, writing["id"]) or []
    parent_set: set[str] = set()
    for row in rows:
        parent_id = row.get("parent_id")
        if parent_id is not None and parent_id != "":
            parent_set.add(str(parent_id))
    items = []
    for row in rows:
        row_id = str(row.get("id"))
        has_children = row_id in parent_set
        items.append({
            "id": row_id,
            "title": str(row.get("title") or ""),
            "parentId": (
                None
                if row.get("parent_id") in (None, "")
                else str(row["parent_id"])
            ),
            "level": int(row.get("level") or 1),
            "sort": int(row.get("sort") or 0),
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


async def _list_ordered_leaf_chapters_for_book(
    db,
    book_id: str,
) -> list[dict]:
    """Read dashboard chapter order without initializing persistent state.

    ``utils.book_structure.get_ordered_leaf_chapters`` intentionally serves
    older application routes that create a missing Writing outline.  Agent
    READ tools cannot inherit that side effect, so their loader uses the
    non-creating lookup and treats a missing outline as an empty book.
    """

    writing = await get_writing_outline(db, book_id)
    if not writing or not writing.get("id"):
        return []
    chapters = await get_chapters(db, str(writing["id"])) or []
    if not chapters:
        return []

    parent_ids = {
        str(chapter["parent_id"])
        for chapter in chapters
        if chapter.get("parent_id") is not None
        and str(chapter.get("parent_id")).strip()
    }
    top_level = sorted(
        [chapter for chapter in chapters if not chapter.get("parent_id")],
        key=lambda chapter: chapter.get("sort") or 0,
    )
    by_parent: dict[str, list[dict]] = {}
    for chapter in chapters:
        parent_id = chapter.get("parent_id")
        if parent_id is not None and str(parent_id).strip():
            by_parent.setdefault(str(parent_id), []).append(chapter)
    for children in by_parent.values():
        children.sort(key=lambda chapter: chapter.get("sort") or 0)

    leaves: list[dict] = []
    for top in top_level:
        top_id = str(top["id"])
        if top_id in parent_ids:
            for child in by_parent.get(top_id, []):
                leaves.append({
                    "id": str(child["id"]),
                    "title": str(child.get("title") or ""),
                    "index": len(leaves) + 1,
                    "volume_title": str(top.get("title") or "") or None,
                })
            continue
        leaves.append({
            "id": top_id,
            "title": str(top.get("title") or ""),
            "index": len(leaves) + 1,
            "volume_title": None,
        })
    return leaves
