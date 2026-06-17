"""
每书每日字数快照（book_word_stats）。

- 正文保存时调用 ``record_article_word_delta`` 增量维护「当日总字数」；
- 统计接口读取时用全量字数 ``sync_today_total`` 自校正（吸收删章等漂移）。
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from database.connection import DatabaseConnection

from utils.text import extract_text_from_lexical
from utils.book_structure import count_words


def today_str() -> str:
    """本地日期（写作者视角的「今天」）。"""
    return datetime.now().strftime("%Y-%m-%d")


async def _resolve_book_id(db: DatabaseConnection, chapter_id: str) -> str | None:
    row = await db.fetch_one(
        "SELECT o.book_id AS book_id FROM outline_chapters oc "
        "JOIN outlines o ON oc.outline_id = o.id WHERE oc.id = ?",
        [chapter_id],
    )
    bid = (row or {}).get("book_id")
    return str(bid) if bid else None


async def get_latest_snapshot(
    db: DatabaseConnection, book_id: str, before_date: str | None = None
) -> dict[str, Any] | None:
    if before_date:
        return await db.fetch_one(
            "SELECT date, total_words FROM book_word_stats "
            "WHERE book_id = ? AND date < ? ORDER BY date DESC LIMIT 1",
            [book_id, before_date],
        )
    return await db.fetch_one(
        "SELECT date, total_words FROM book_word_stats "
        "WHERE book_id = ? ORDER BY date DESC LIMIT 1",
        [book_id],
    )


async def upsert_snapshot(
    db: DatabaseConnection, book_id: str, date: str, total_words: int
) -> None:
    await db.execute(
        "INSERT INTO book_word_stats (book_id, date, total_words) VALUES (?, ?, ?) "
        "ON CONFLICT(book_id, date) DO UPDATE SET total_words = excluded.total_words",
        [book_id, date, int(total_words)],
    )


async def record_article_word_delta(
    db: DatabaseConnection,
    chapter_id: str,
    old_content: str | None,
    new_content: str | None,
) -> None:
    """正文保存后调用：把字数变化累加到当日快照。

    没有任何快照时不在这里全量补算（避免拖慢保存），
    由统计接口首次读取时播种基线。
    """
    try:
        old_words = count_words(extract_text_from_lexical(old_content)) if old_content else 0
        new_words = count_words(extract_text_from_lexical(new_content)) if new_content else 0
        delta = new_words - old_words
        if delta == 0:
            return
        book_id = await _resolve_book_id(db, chapter_id)
        if not book_id:
            return
        latest = await get_latest_snapshot(db, book_id)
        if latest is None:
            return
        # 无论最近快照是今天还是更早，「今天的总数」都是最近总数 + 本次增量
        await upsert_snapshot(
            db, book_id, today_str(), int(latest["total_words"]) + delta
        )
    except Exception:
        # 统计是旁路功能，绝不能影响正文保存
        pass


async def sync_today_total(
    db: DatabaseConnection, book_id: str, true_total: int
) -> None:
    """用全量计算出的真实总字数校正快照。

    - 还没有任何快照：播种今天 = 真实总数（作为日更统计基线）；
    - 今天已有快照：覆盖为真实总数（吸收增量统计的漂移）。
    - 今天没有快照且历史存在：不动历史（今日字数由调用方现算）。
    """
    latest = await get_latest_snapshot(db, book_id)
    today = today_str()
    if latest is None or str(latest["date"]) == today:
        await upsert_snapshot(db, book_id, today, true_total)


async def get_daily_series(
    db: DatabaseConnection, book_id: str, days: int = 30
) -> list[dict[str, Any]]:
    """近 N 天每日新增字数（含 0 的占位日），按日期升序。"""
    rows = await db.fetch_all(
        "SELECT date, total_words FROM book_word_stats "
        "WHERE book_id = ? ORDER BY date ASC",
        [book_id],
    )
    if not rows:
        return []
    totals = [(str(r["date"]), int(r["total_words"])) for r in rows]

    from datetime import timedelta
    end = datetime.now().date()
    start = end - timedelta(days=days - 1)

    series: list[dict[str, Any]] = []
    for offset in range(days):
        d = start + timedelta(days=offset)
        ds = d.strftime("%Y-%m-%d")
        cur = next((t for t in totals if t[0] == ds), None)
        if cur is None:
            series.append({"date": ds, "words": 0})
            continue
        prev_total = 0
        for t in totals:
            if t[0] < ds:
                prev_total = t[1]
            else:
                break
        series.append({"date": ds, "words": max(0, cur[1] - prev_total)})
    return series
