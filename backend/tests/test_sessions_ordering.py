"""会话列表置顶与手动排序 API 的落库行为。"""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import FastAPI

from database.connection import DatabaseConnection
from dependencies import set_db
from routers.ai import router as ai_router  # noqa: F401 -- 依赖链初始化
from routers.sessions import router as sessions_router
from routers.sessions import (
    reorder_sessions,
    update_session_pinned,
)
from schemas.sessions import ReorderSessionsRequest, UpdateSessionPinnedRequest
from tests.support.asgi_sse import request_json

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


async def test_pin_and_reorder_persist_through_http_round_trip(
    temp_db: DatabaseConnection,
):
    """置顶与排序经真实路由写入后，会话查询按原值返回（持久化闭环）。"""

    await _seed_sessions(temp_db)
    app = FastAPI()
    app.include_router(sessions_router, prefix="/api")

    pinned = await request_json(
        app,
        method="PUT",
        path="/api/sessions/2/pinned",
        json_body={"pinned": True},
    )
    assert pinned.status_code == 200
    assert pinned.json() == {"success": True}

    reordered = await request_json(
        app,
        method="PUT",
        path="/api/sessions/reorder",
        json_body={"orderedIds": [3, 1, 2]},
    )
    assert reordered.status_code == 200

    listed = await request_json(
        app,
        method="GET",
        path="/api/sessions?bookId=book-1&chapterId=chapter-1",
        json_body=None,
    )
    assert listed.status_code == 200
    rows = listed.json()["data"]
    by_id = {row["id"]: row for row in rows}
    assert by_id[2]["pinned"] == 1
    assert by_id[1]["pinned"] == 0
    assert by_id[3]["sort_order"] == 0
    assert by_id[1]["sort_order"] == 1
    assert by_id[2]["sort_order"] == 2
