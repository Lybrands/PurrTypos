"""
Async SQLite connection wrapper using aiosqlite.

设计要点：
  * 单连接 + WAL：本应用是单进程 Electron 桌面端，并发量低，单连接已够用；
    WAL 让读写互不阻塞。
  * 显式事务：默认每个 ``execute`` 调用即提交（向后兼容旧 CRUD），
    需要原子多步时用 ``async with db.transaction(): ...``，期间所有写入
    会被合并成一笔事务，异常自动回滚，并支持 SAVEPOINT 嵌套。
  * 健康检查：``is_healthy()`` 跑 ``SELECT 1``，供 /health 端点判断 DB 是否
    仍可用——单连接挂掉时让前端立刻看到红灯而不是请求挂死。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

import aiosqlite


class DatabaseConnection:
    def __init__(self, data_dir: Path | None = None):
        self._data_dir = data_dir or Path(".")
        self._db_path = self._data_dir / "purrtypos.db"
        self._conn: aiosqlite.Connection | None = None
        # 0 表示当前不在事务里；>0 表示事务/SAVEPOINT 嵌套深度。
        # ``execute`` 看到 >0 就会跳过自动 commit，把多步写合并到外层事务。
        self._tx_depth = 0

    async def init(self) -> None:
        self._conn = await aiosqlite.connect(self._db_path)
        self._conn.row_factory = aiosqlite.Row
        # WAL：读写互不阻塞，符合 Electron 桌面端「读多写少」的画像。
        await self._conn.execute("PRAGMA journal_mode=WAL")
        # 写锁竞争时最多等 5 秒（默认 0 秒会立刻 SQLITE_BUSY）。
        # 注：未启用 ``PRAGMA foreign_keys=ON``——历史 schema 中的删除链
        # 是在 CRUD 里手动级联的，强行打开 FK 会破坏现有删除流程；
        # 待 schema 整体清理后再开。
        await self._conn.execute("PRAGMA busy_timeout=5000")
        await self._conn.commit()
        from database.schema import init_schema
        await init_schema(self)

    def _ensure_conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database not initialised – call init() first")
        return self._conn

    # ── Transactions ─────────────────────────────────────────────

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator["DatabaseConnection"]:
        """
        将块内所有写入合并为一笔原子事务；异常自动回滚。

        嵌套调用使用 SAVEPOINT，子块失败不会回滚父事务，
        但**未捕获**的异常会一直冒到最外层并整体回滚。

        用法::

            async with db.transaction():
                await db.execute("INSERT ...")
                await db.execute("DELETE ...")
        """
        conn = self._ensure_conn()

        if self._tx_depth > 0:
            sp_name = f"sp_{self._tx_depth}"
            await conn.execute(f"SAVEPOINT {sp_name}")
            self._tx_depth += 1
            try:
                yield self
            except BaseException:
                await conn.execute(f"ROLLBACK TO SAVEPOINT {sp_name}")
                await conn.execute(f"RELEASE SAVEPOINT {sp_name}")
                self._tx_depth -= 1
                raise
            else:
                await conn.execute(f"RELEASE SAVEPOINT {sp_name}")
                self._tx_depth -= 1
            return

        # 顶层事务：用 IMMEDIATE 抢写锁，避免读事务升级时 SQLITE_BUSY。
        await conn.execute("BEGIN IMMEDIATE")
        self._tx_depth = 1
        try:
            yield self
        except BaseException:
            await conn.rollback()
            self._tx_depth = 0
            raise
        else:
            await conn.commit()
            self._tx_depth = 0

    # ── Basic operations (transaction-aware) ─────────────────────

    async def execute(self, sql: str, params: list[Any] | tuple[Any, ...] = ()) -> None:
        conn = self._ensure_conn()
        await conn.execute(sql, params)
        if self._tx_depth == 0:
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
        if self._tx_depth == 0:
            await conn.commit()
        return cursor.lastrowid

    # ── Health & lifecycle ───────────────────────────────────────

    async def is_healthy(self) -> bool:
        """供 /health 端点使用：连接还活着且能执行最简查询返回 True。"""
        if self._conn is None:
            return False
        try:
            await self._conn.execute("SELECT 1")
            return True
        except Exception:
            return False

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
            self._tx_depth = 0
