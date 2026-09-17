"""会话列表置顶与手动排序 API 的落库行为。"""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from database.connection import DatabaseConnection
from dependencies import set_db
from routers.sessions import reorder_sessions, update_session_pinned
from schemas.sessions import ReorderSessionsRequest, UpdateSessionPinnedRequest

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def temp_db(tmp_path: Path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    set_db(db)
    try:
        yield db
    finally:
        await db.close()


async def _seed_sessions(db: DatabaseConnection) -> None:
    await db.execute(
        "INSERT INTO ai_sessions (id, book_id, chapter_id, title, create_time) VALUES "
        "(1, 'book-1', 'chapter-1', '会话一', '2026-09-17 10:00:00'), "
        "(2, 'book-1', 'chapter-1', '会话二', '2026-09-17 11:00:00'), "
        "(3, 'book-1', 'chapter-1', '会话三', '2026-09-17 12:00:00')"
    )


async def test_pin_toggle_persists_pinned_flag(temp_db: DatabaseConnection):
    await _seed_sessions(temp_db)

    await update_session_pinned(2, UpdateSessionPinnedRequest(pinned=True))
    assert await temp_db.fetch_one(
        "SELECT pinned FROM ai_sessions WHERE id = 2"
    ) == {"pinned": 1}
    assert await temp_db.fetch_one(
        "SELECT pinned FROM ai_sessions WHERE id = 1"
    ) == {"pinned": 0}

    await update_session_pinned(2, UpdateSessionPinnedRequest(pinned=False))
    assert await temp_db.fetch_one(
        "SELECT pinned FROM ai_sessions WHERE id = 2"
    ) == {"pinned": 0}


async def test_reorder_writes_contiguous_sort_order(temp_db: DatabaseConnection):
    await _seed_sessions(temp_db)

    await reorder_sessions(ReorderSessionsRequest(orderedIds=[3, 1, 2]))

    rows = await temp_db.fetch_all(
        "SELECT id, sort_order FROM ai_sessions ORDER BY sort_order"
    )
    assert rows == [
        {"id": 3, "sort_order": 0},
        {"id": 1, "sort_order": 1},
        {"id": 2, "sort_order": 2},
    ]


async def test_reorder_ignores_unknown_ids_and_keeps_single_transaction(
    temp_db: DatabaseConnection,
):
    await _seed_sessions(temp_db)

    await reorder_sessions(ReorderSessionsRequest(orderedIds=[999, 2]))

    assert await temp_db.fetch_one(
        "SELECT sort_order FROM ai_sessions WHERE id = 2"
    ) == {"sort_order": 1}
    assert await temp_db.fetch_one(
        "SELECT sort_order FROM ai_sessions WHERE id = 1"
    ) == {"sort_order": None}
