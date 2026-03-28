"""
Async SQLite connection wrapper using aiosqlite.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import aiosqlite


class DatabaseConnection:
    def __init__(self, data_dir: Path | None = None):
        self._data_dir = data_dir or Path(".")
        self._db_path = self._data_dir / "purrtypos.db"
        self._conn: aiosqlite.Connection | None = None

    async def init(self) -> None:
        self._conn = await aiosqlite.connect(self._db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        from database.schema import init_schema
        await init_schema(self)

    def _ensure_conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database not initialised – call init() first")
        return self._conn

    async def execute(self, sql: str, params: list[Any] | tuple[Any, ...] = ()) -> None:
        conn = self._ensure_conn()
        await conn.execute(sql, params)
        await conn.commit()

    async def fetch_one(self, sql: str, params: list[Any] | tuple[Any, ...] = ()) -> dict[str, Any] | None:
        conn = self._ensure_conn()
        cursor = await conn.execute(sql, params)
        row = await cursor.fetchone()
        if row is None:
            return None
        return dict(row)

    async def fetch_all(self, sql: str, params: list[Any] | tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        conn = self._ensure_conn()
        cursor = await conn.execute(sql, params)
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def execute_and_get_id(self, sql: str, params: list[Any] | tuple[Any, ...] = ()) -> int | None:
        conn = self._ensure_conn()
        cursor = await conn.execute(sql, params)
        await conn.commit()
        return cursor.lastrowid

    def get_db_path(self) -> Path:
        return self._db_path

    async def export_to_buffer(self) -> bytes | None:
        if self._conn is None:
            return None
        await self._conn.commit()
        return self._db_path.read_bytes()

    async def close(self) -> None:
        if self._conn is not None:
            try:
                await self._conn.close()
            except Exception:
                pass
            self._conn = None
