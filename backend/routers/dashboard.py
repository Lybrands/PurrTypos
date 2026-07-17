"""
仪表盘：故事健康度（伏笔预警 / 人物出场统计）与写作统计（日更 / 连续达标 / 章节字数分布）。

全部为只读聚合（写作目标除外），数据来自现有表，无 AI 调用。
"""

from __future__ import annotations

import json

from fastapi import APIRouter

from database.crud.characters import get_characters
from database.crud.word_stats import (
    get_daily_series,
    get_latest_snapshot,
    sync_today_total,
    today_str,
)
from dependencies import get_db
from schemas.dashboard import SetWritingGoalRequest
from utils.book_structure import (
    count_words,
    get_ordered_leaf_chapters,
    load_chapter_texts,
)

router = APIRouter(tags=["dashboard"])

# 伏笔「临近回收」预警窗口：预期章节在最新已写章节之后 N 章以内
FORESHADOW_DUE_SOON_WINDOW = 3

# 人物「久未出场」提醒阈值（章）
CHARACTER_GAP_WARN = 20


def _goal_key(book_id: str) -> str:
    return f"writingGoalWords:{book_id}"


async def _get_goal_words(db, book_id: str) -> int:
    row = await db.fetch_one(
        "SELECT value FROM settings WHERE key = ?", [_goal_key(book_id)]
    )
    if not row:
        return 0
    try:
        return max(0, int(json.loads(row["value"])))
    except (ValueError, TypeError, json.JSONDecodeError):
        return 0


@router.get("/dashboard/health")
async def get_story_health(bookId: str):
    db = get_db()
    leaves = await get_ordered_leaf_chapters(db, bookId)
    texts = await load_chapter_texts(db, [c["id"] for c in leaves])

    words_by_id = {cid: count_words(t) for cid, t in texts.items()}
    written = [c for c in leaves if words_by_id.get(c["id"], 0) > 0]
    latest_written_index = max((c["index"] for c in written), default=0)
    index_by_id = {c["id"]: c["index"] for c in leaves}
    title_by_id = {c["id"]: c["title"] for c in leaves}

    # ── 伏笔 ────────────────────────────────────────────────────
    rows = await db.fetch_all(
        "SELECT * FROM ai_foreshadowing WHERE book_id = ? ORDER BY create_time ASC",
        [bookId],
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
    chars = await get_characters(db, bookId) or []
    appearances = []
    for ch in chars:
        name = str(ch.get("name") or "").strip()
        if not name:
            continue
        appear_indices = [
            c["index"] for c in written if name in texts.get(c["id"], "")
        ]
        last_index = max(appear_indices, default=None)
        appearances.append({
            "id": ch.get("id"),
            "name": name,
            "tags": str(ch.get("tags") or ""),
            "appearChapters": len(appear_indices),
            "lastChapterIndex": last_index,
            "lastChapterTitle": next(
                (c["title"] for c in written if c["index"] == last_index), None
            ) if last_index else None,
            "gapChapters": (latest_written_index - last_index) if last_index else None,
        })
    # 久未出场的排前面；从未出场的最后
    appearances.sort(key=lambda a: (
        a["gapChapters"] is None,
        -(a["gapChapters"] or 0),
    ))

    return {"success": True, "data": {
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
    }}


@router.get("/dashboard/writing-stats")
async def get_writing_stats(bookId: str):
    db = get_db()
    leaves = await get_ordered_leaf_chapters(db, bookId)
    texts = await load_chapter_texts(db, [c["id"] for c in leaves])

    chapters = [{
        "id": c["id"],
        "title": c["title"],
        "index": c["index"],
        "volumeTitle": c["volume_title"],
        "words": count_words(texts.get(c["id"], "")),
    } for c in leaves]
    total_words = sum(c["words"] for c in chapters)

    # 用真实总数播种/校正快照
    await sync_today_total(db, bookId, total_words)

    today = today_str()
    prev = await get_latest_snapshot(db, bookId, before_date=today)
    today_words = max(0, total_words - int(prev["total_words"])) if prev else 0

    daily = await get_daily_series(db, bookId, days=30)
    goal = await _get_goal_words(db, bookId)

    # 连续达标天数：从今天（或今天未达标时从昨天）往回数；按近一年算，避免被 30 天窗口截断
    streak_series = await get_daily_series(db, bookId, days=365)
    threshold = goal if goal > 0 else 1
    streak = 0
    if streak_series:
        start = len(streak_series) - 1
        if streak_series[-1]["words"] < threshold:
            start -= 1
        for i in range(start, -1, -1):
            if streak_series[i]["words"] >= threshold:
                streak += 1
            else:
                break

    written = [c for c in chapters if c["words"] > 0]
    avg_words = round(sum(c["words"] for c in written) / len(written)) if written else 0

    return {"success": True, "data": {
        "totalWords": total_words,
        "todayWords": today_words,
        "goalWords": goal,
        "streakDays": streak,
        "avgChapterWords": avg_words,
        "daily": daily,
        "chapters": chapters,
    }}


@router.post("/dashboard/writing-goal")
async def set_writing_goal(body: SetWritingGoalRequest):
    db = get_db()
    key = _goal_key(str(body.bookId))
    value = json.dumps(max(0, int(body.dailyWords or 0)))
    existing = await db.fetch_one("SELECT key FROM settings WHERE key = ?", [key])
    if existing:
        await db.execute("UPDATE settings SET value = ? WHERE key = ?", [value, key])
    else:
        await db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?)", [key, value]
        )
    return {"success": True}
