"""
仪表盘：故事健康度（伏笔预警 / 人物出场统计）与写作统计（日更 / 连续达标 / 章节字数分布）。

全部为只读聚合（写作目标除外），数据来自现有表，无 AI 调用。
"""

from __future__ import annotations

import json

from fastapi import APIRouter

from application.story_health import build_story_health
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
    data = await build_story_health(db, bookId)
    return {"success": True, "data": data}


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
