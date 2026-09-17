"""故事健康度聚合：伏笔预警与人物出场统计。

/dashboard/health 路由与写作 Agent 的 readWritingDashboard 工具共用这一份
实现，避免两处扫描逻辑漂移。全部为只读实时聚合，无 AI 调用。
"""

from __future__ import annotations

from typing import Any

from database.crud.characters import get_characters
from utils.book_structure import (
    count_words,
    get_ordered_leaf_chapters,
    load_chapter_texts,
)

# 伏笔「临近回收」预警窗口：预期章节在最新已写章节之后 N 章以内
FORESHADOW_DUE_SOON_WINDOW = 3

# 人物「久未出场」提醒阈值（章）
CHARACTER_GAP_WARN = 20


async def build_story_health(db, book_id: str) -> dict[str, Any]:
    leaves = await get_ordered_leaf_chapters(db, book_id)
    texts = await load_chapter_texts(db, [c["id"] for c in leaves])

    words_by_id = {cid: count_words(t) for cid, t in texts.items()}
    written = [c for c in leaves if words_by_id.get(c["id"], 0) > 0]
    latest_written_index = max((c["index"] for c in written), default=0)
    index_by_id = {c["id"]: c["index"] for c in leaves}
    title_by_id = {c["id"]: c["title"] for c in leaves}

    # ── 伏笔 ────────────────────────────────────────────────────
    rows = await db.fetch_all(
        "SELECT * FROM ai_foreshadowing WHERE book_id = ? ORDER BY create_time ASC",
        [book_id],
    )
    unresolved = []
    resolved_count = 0
    for r in rows:
        status = str(r.get("status") or "未回收")
        if status != "未回收":
            resolved_count += 1
            continue
        cid = str(r.get("chapter_id") or "")
        expected_id = str(r.get("expected_chapter_id") or "") or None
        expected_index = index_by_id.get(expected_id) if expected_id else None
        overdue = bool(
            expected_index is not None and latest_written_index > expected_index
        )
        due_soon = bool(
            not overdue
            and expected_index is not None
            and expected_index - latest_written_index <= FORESHADOW_DUE_SOON_WINDOW
        )
        unresolved.append({
            "id": r.get("id"),
            "content": str(r.get("content") or ""),
            "type": str(r.get("type") or "悬念"),
            "chapterId": cid or None,
            "chapterTitle": title_by_id.get(cid),
            "chapterIndex": index_by_id.get(cid),
            "expectedChapterId": expected_id,
            "expectedChapterTitle": title_by_id.get(expected_id) if expected_id else None,
            "expectedChapterIndex": expected_index,
            "overdue": overdue,
            "dueSoon": due_soon,
            "createTime": r.get("create_time"),
        })
    # 逾期在前、临近其次，其余按埋设章节顺序
    unresolved.sort(key=lambda f: (
        0 if f["overdue"] else 1 if f["dueSoon"] else 2,
        f["chapterIndex"] or 0,
    ))

    # ── 人物出场 ────────────────────────────────────────────────
    chars = await get_characters(db, book_id) or []
    appearances = []
    for ch in chars:
        name = str(ch.get("name") or "").strip()
        if not name:
            continue
        chapter_refs = []
        for c in written:
            mentions = texts.get(c["id"], "").count(name)
            if mentions <= 0:
                continue
            chapter_refs.append({
                "chapterId": c["id"],
                "index": c["index"],
                "title": c["title"],
                "mentions": mentions,
            })
        last_ref = chapter_refs[-1] if chapter_refs else None
        last_index = last_ref["index"] if last_ref else None
        appearances.append({
            "id": ch.get("id"),
            "name": name,
            "tags": str(ch.get("tags") or ""),
            "appearChapters": len(chapter_refs),
            "chapterRefs": chapter_refs,
            "lastChapterIndex": last_index,
            "lastChapterTitle": last_ref["title"] if last_ref else None,
            "gapChapters": (latest_written_index - last_index) if last_index else None,
        })
    # 久未出场的排前面；从未出场的最后
    appearances.sort(key=lambda a: (
        a["gapChapters"] is None,
        -(a["gapChapters"] or 0),
    ))

    return {
        "totalChapters": len(leaves),
        "writtenChapters": len(written),
        "totalWords": sum(words_by_id.values()),
        "latestWrittenIndex": latest_written_index,
        "gapWarnThreshold": CHARACTER_GAP_WARN,
        "foreshadowing": {
            "unresolved": unresolved,
            "unresolvedCount": len(unresolved),
            "overdueCount": sum(1 for f in unresolved if f["overdue"]),
            "dueSoonCount": sum(1 for f in unresolved if f["dueSoon"]),
            "resolvedCount": resolved_count,
        },
        "characters": appearances,
    }


__all__ = [
    "CHARACTER_GAP_WARN",
    "FORESHADOW_DUE_SOON_WINDOW",
    "build_story_health",
]
