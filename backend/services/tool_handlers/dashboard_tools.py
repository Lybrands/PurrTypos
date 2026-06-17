"""Read-only dashboard aggregate tools."""

from __future__ import annotations

import json
from typing import Callable

from dependencies import get_db
from services.tool_runtime import (
    ToolResult,
    _read_tool_cache_get,
    _read_tool_cache_set,
    build_read_cache_key,
    resolve_book_id_for_tools,
    tool,
)
from utils.book_structure import count_words, get_ordered_leaf_chapters, load_chapter_texts
from database.crud.characters import get_characters
from database.crud.word_stats import get_daily_series, get_latest_snapshot, today_str


FORESHADOW_DUE_SOON_WINDOW = 3
CHARACTER_GAP_WARN = 20


async def _goal_words(db, book_id: str) -> int:
    row = await db.fetch_one("SELECT value FROM settings WHERE key = ?", [f"writingGoalWords:{book_id}"])
    if not row:
        return 0
    try:
        return max(0, int(json.loads(row["value"])))
    except Exception:
        return 0


@tool("getStoryHealthDashboard")
async def _tool_get_story_health_dashboard(ctx: dict, args: dict, send_chunk: Callable | None) -> ToolResult:
    bid = resolve_book_id_for_tools(ctx, args)
    ck = build_read_cache_key("getStoryHealthDashboard", ctx, args)
    hit = _read_tool_cache_get(ctx, ck) if ck else None
    if hit is not None:
        return ToolResult(hit, from_cache=True)

    db = get_db()
    leaves = await get_ordered_leaf_chapters(db, str(bid))
    texts = await load_chapter_texts(db, [c["id"] for c in leaves])
    words_by_id = {cid: count_words(t) for cid, t in texts.items()}
    written = [c for c in leaves if words_by_id.get(c["id"], 0) > 0]
    latest_written_index = max((c["index"] for c in written), default=0)
    index_by_id = {c["id"]: c["index"] for c in leaves}
    title_by_id = {c["id"]: c["title"] for c in leaves}

    rows = await db.fetch_all(
        "SELECT * FROM ai_foreshadowing WHERE book_id = ? ORDER BY create_time ASC",
        [str(bid)],
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
        overdue = bool(expected_index is not None and latest_written_index > expected_index)
        due_soon = bool(
            not overdue
            and expected_index is not None
            and expected_index - latest_written_index <= FORESHADOW_DUE_SOON_WINDOW
        )
        unresolved.append({
            "id": r.get("id"),
            "content": str(r.get("content") or "")[:120],
            "type": str(r.get("type") or "悬念"),
            "chapterTitle": title_by_id.get(cid),
            "expectedChapterTitle": title_by_id.get(expected_id) if expected_id else None,
            "overdue": overdue,
            "dueSoon": due_soon,
        })
    unresolved.sort(key=lambda f: (0 if f["overdue"] else 1 if f["dueSoon"] else 2))

    chars = await get_characters(db, str(bid)) or []
    appearances = []
    for ch in chars:
        name = str(ch.get("name") or "").strip()
        if not name:
            continue
        appear_indices = [c["index"] for c in written if name in texts.get(c["id"], "")]
        last_index = max(appear_indices, default=None)
        appearances.append({
            "id": ch.get("id"),
            "name": name,
            "tags": str(ch.get("tags") or ""),
            "appearChapters": len(appear_indices),
            "lastChapterIndex": last_index,
            "lastChapterTitle": next((c["title"] for c in written if c["index"] == last_index), None) if last_index else None,
            "gapChapters": (latest_written_index - last_index) if last_index else None,
        })
    appearances.sort(key=lambda a: (a["gapChapters"] is None, -(a["gapChapters"] or 0)))

    payload = {
        "totalChapters": len(leaves),
        "writtenChapters": len(written),
        "totalWords": sum(words_by_id.values()),
        "latestWrittenIndex": latest_written_index,
        "foreshadowing": {
            "unresolvedCount": len(unresolved),
            "overdueCount": sum(1 for f in unresolved if f["overdue"]),
            "dueSoonCount": sum(1 for f in unresolved if f["dueSoon"]),
            "resolvedCount": resolved_count,
            "topUnresolved": unresolved[:8],
        },
        "characters": {
            "gapWarnThreshold": CHARACTER_GAP_WARN,
            "topGaps": appearances[:10],
        },
    }
    content = json.dumps(payload, ensure_ascii=False)
    if ck:
        _read_tool_cache_set(ctx, ck, content)
    return ToolResult(content)


@tool("getWritingStatsDashboard")
async def _tool_get_writing_stats_dashboard(ctx: dict, args: dict, send_chunk: Callable | None) -> ToolResult:
    bid = resolve_book_id_for_tools(ctx, args)
    ck = build_read_cache_key("getWritingStatsDashboard", ctx, args)
    hit = _read_tool_cache_get(ctx, ck) if ck else None
    if hit is not None:
        return ToolResult(hit, from_cache=True)

    db = get_db()
    leaves = await get_ordered_leaf_chapters(db, str(bid))
    texts = await load_chapter_texts(db, [c["id"] for c in leaves])
    chapters = [{
        "id": c["id"],
        "title": c["title"],
        "index": c["index"],
        "volumeTitle": c["volume_title"],
        "words": count_words(texts.get(c["id"], "")),
    } for c in leaves]
    total_words = sum(c["words"] for c in chapters)
    today = today_str()
    prev = await get_latest_snapshot(db, str(bid), before_date=today)
    today_words = max(0, total_words - int(prev["total_words"])) if prev else 0
    daily = await get_daily_series(db, str(bid), days=30)
    if daily:
        if str(daily[-1].get("date")) == today:
            daily[-1] = {**daily[-1], "words": today_words}
        else:
            daily.append({"date": today, "words": today_words})
    elif today_words > 0:
        daily = [{"date": today, "words": today_words}]
    goal = await _goal_words(db, str(bid))
    written = [c for c in chapters if c["words"] > 0]
    avg_words = round(sum(c["words"] for c in written) / len(written)) if written else 0

    payload = {
        "totalWords": total_words,
        "todayWords": today_words,
        "goalWords": goal,
        "avgChapterWords": avg_words,
        "writtenChapters": len(written),
        "totalChapters": len(chapters),
        "recentDaily": daily[-14:],
        "longestChapters": sorted(chapters, key=lambda c: c["words"], reverse=True)[:8],
        "emptyChapters": [c for c in chapters if c["words"] <= 0][:12],
    }
    content = json.dumps(payload, ensure_ascii=False)
    if ck:
        _read_tool_cache_set(ctx, ck, content)
    return ToolResult(content)
