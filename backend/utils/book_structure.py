"""
书籍写作目录的结构化读取：有序叶子章节（含卷信息）与正文批量加载。

仪表盘（健康度 / 写作统计）与导出（EPUB / 整本 TXT）共用，
口径与 books.word-count 一致：Lexical 抽纯文本后去掉空白计字。
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from database.connection import DatabaseConnection

from database.crud.chapters import get_chapters
from database.crud.outlines import get_or_create_writing_outline
from utils.text import extract_text_from_lexical


def count_words(plain_text: str) -> int:
    """有效字数 = 去掉全部空白后的字符数（与编辑器统计规则一致）。"""
    return len(re.sub(r"\s", "", plain_text or "", flags=re.UNICODE))


async def get_ordered_leaf_chapters(
    db: DatabaseConnection, book_id: str
) -> list[dict[str, Any]]:
    """返回写作目录的有序叶子章节（跳过卷节点）。

    每项：``{id, title, index(1-based 全书序号), volume_title|None}``。
    顺序：顶层节点按 sort，卷内章节按 sort，整体即阅读顺序。
    """
    writing = await get_or_create_writing_outline(db, book_id)
    if not writing or not writing.get("id"):
        return []
    chapters = await get_chapters(db, str(writing["id"])) or []
    if not chapters:
        return []

    parent_ids = {
        str(c["parent_id"]) for c in chapters
        if c.get("parent_id") is not None and str(c.get("parent_id")).strip()
    }
    tops = sorted(
        [c for c in chapters if not c.get("parent_id")],
        key=lambda c: (c.get("sort") or 0),
    )
    by_parent: dict[str, list[dict]] = {}
    for c in chapters:
        pid = c.get("parent_id")
        if pid is not None and str(pid).strip():
            by_parent.setdefault(str(pid), []).append(c)
    for lst in by_parent.values():
        lst.sort(key=lambda c: (c.get("sort") or 0))

    leaves: list[dict[str, Any]] = []
    index = 0
    for top in tops:
        tid = str(top["id"])
        if tid in parent_ids:
            # 卷节点：展开卷内章节
            for child in by_parent.get(tid, []):
                index += 1
                leaves.append({
                    "id": str(child["id"]),
                    "title": str(child.get("title") or ""),
                    "index": index,
                    "volume_title": str(top.get("title") or "") or None,
                })
        else:
            index += 1
            leaves.append({
                "id": str(top["id"]),
                "title": str(top.get("title") or ""),
                "index": index,
                "volume_title": None,
            })
    return leaves


async def load_chapter_texts(
    db: DatabaseConnection, chapter_ids: list[str]
) -> dict[str, str]:
    """批量读取正文并抽成纯文本；无正文的章节映射为空串。"""
    texts: dict[str, str] = {cid: "" for cid in chapter_ids}
    # SQLite 变量上限 999，分批 IN 查询
    for i in range(0, len(chapter_ids), 500):
        batch = chapter_ids[i:i + 500]
        placeholders = ",".join("?" for _ in batch)
        rows = await db.fetch_all(
            f"SELECT chapter_id, content FROM articles WHERE chapter_id IN ({placeholders})",
            batch,
        )
        for row in rows:
            raw = row.get("content") or ""
            texts[str(row["chapter_id"])] = extract_text_from_lexical(raw) if raw else ""
    return texts
