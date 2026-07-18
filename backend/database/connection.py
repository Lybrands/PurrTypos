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

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

import aiosqlite


async def _await_task_uninterruptibly(task: asyncio.Task[None]) -> None:
    """Wait until a durable-state child has an authoritative outcome.

    Once COMMIT or ROLLBACK has started it must not inherit cancellation from
    its waiter. Every caller cancellation is suppressed at this boundary until
    the child reports success, failure, or its own cancellation. This includes
    repeated ``cancel()`` calls while a previous one is already being handled.
    """

    while True:
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.cancelled():
                raise
            if task.done():
                task.result()
                return
            continue
        return


async def _rollback_uninterruptibly(conn: aiosqlite.Connection) -> None:
    await _await_task_uninterruptibly(asyncio.create_task(conn.rollback()))


class DatabaseConnection:
    def __init__(self, data_dir: Path | None = None):
        self._data_dir = data_dir or Path(".")
        self._db_path = self._data_dir / "purrtypos.db"
        self._conn: aiosqlite.Connection | None = None
        # SQLite operations share one connection. A top-level transaction owns
        # that connection until it commits or rolls back. Only the owning Task
        # may use SAVEPOINT nesting; other Tasks wait for the connection lock.
        self._connection_lock = asyncio.Lock()
        self._tx_owner: asyncio.Task[Any] | None = None
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

    def _current_task(self) -> asyncio.Task[Any]:
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("database operation must run inside an asyncio Task")
        return task

    @asynccontextmanager
    async def _connection_access(
        self,
    ) -> AsyncIterator[tuple[aiosqlite.Connection, bool]]:
        """Yield the connection and whether the caller owns a transaction."""
        task = self._current_task()
        if self._tx_owner is task:
            # The top-level transaction already holds the non-reentrant lock.
            yield self._ensure_conn(), True
            return

        # A different Task must not observe or join an in-flight transaction.
        # Holding the lock through an operation and its auto-commit also keeps
        # another Task from placing work between those two queue entries.
        async with self._connection_lock:
            yield self._ensure_conn(), False

    # ── Transactions ─────────────────────────────────────────────

    @asynccontextmanager
    async def transaction(
        self,
        *,
        cancellation_linearizable: bool = False,
        write: bool = True,
    ) -> AsyncIterator["DatabaseConnection"]:
        """
        将块内所有写入合并为一笔原子事务；异常自动回滚。

        嵌套调用使用 SAVEPOINT，子块失败不会回滚父事务，
        但**未捕获**的异常会一直冒到最外层并整体回滚。

        用法::

            async with db.transaction():
                await db.execute("INSERT ...")
                await db.execute("DELETE ...")

        ``write=False`` starts a deferred transaction for a consistent read
        snapshot without eagerly taking SQLite's write reservation.

        ``cancellation_linearizable=True`` is reserved for receipt-returning
        persistence ports. Cancellation before COMMIT rolls back; cancellation
        during COMMIT finishes the durable acknowledgement so the port can
        return its receipt instead of reporting a false non-commit.
        """
        task = self._current_task()

        if cancellation_linearizable and not write:
            raise ValueError(
                "cancellation-linearizable transactions must be writable"
            )

        if self._tx_owner is task:
            conn = self._ensure_conn()
            if cancellation_linearizable:
                raise RuntimeError(
                    "cancellation-linearizable transaction cannot be nested"
                )
            sp_name = f"sp_{self._tx_depth}"
            await conn.execute(f"SAVEPOINT {sp_name}")
            self._tx_depth += 1
            try:
                yield self
            except BaseException:
                try:
                    await conn.execute(f"ROLLBACK TO SAVEPOINT {sp_name}")
                    await conn.execute(f"RELEASE SAVEPOINT {sp_name}")
                finally:
                    self._tx_depth -= 1
                raise
            else:
                try:
                    await conn.execute(f"RELEASE SAVEPOINT {sp_name}")
                finally:
                    self._tx_depth -= 1
            return

        # A peer Task's transaction is another top-level transaction, not a
        # nested SAVEPOINT. It waits here until that Task releases the single
        # connection.
        await self._connection_lock.acquire()
        try:
            conn = self._ensure_conn()
            try:
                await conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            except asyncio.CancelledError:
                # aiosqlite may already have queued BEGIN on its worker thread.
                # Queueing rollback behind it makes CancelledError authoritative:
                # no transaction from this context can become durable later.
                await _rollback_uninterruptibly(conn)
                raise
            self._tx_owner = task
            self._tx_depth = 1
            try:
                yield self
            except BaseException:
                await _rollback_uninterruptibly(conn)
                raise
            else:
                if not cancellation_linearizable:
                    await conn.commit()
                    return
                # COMMIT is the durable receipt boundary. If cancellation arrives
                # while its acknowledgement is in flight, finish COMMIT and
                # suppress that cancellation so the repository can return its
                # receipt. Failures still roll back and propagate.
                commit_task = asyncio.create_task(conn.commit())
                try:
                    await _await_task_uninterruptibly(commit_task)
                except BaseException:
                    await _rollback_uninterruptibly(conn)
                    raise
        finally:
            if self._tx_owner is task:
                self._tx_owner = None
                self._tx_depth = 0
            self._connection_lock.release()

    # ── Basic operations (transaction-aware) ─────────────────────

    async def execute(self, sql: str, params: list[Any] | tuple[Any, ...] = ()) -> None:
        async with self._connection_access() as (conn, in_transaction):
            await conn.execute(sql, params)
            if not in_transaction:
                await conn.commit()

    async def fetch_one(self, sql: str, params: list[Any] | tuple[Any, ...] = ()) -> dict[str, Any] | None:
        async with self._connection_access() as (conn, _):
            cursor = await conn.execute(sql, params)
            row = await cursor.fetchone()
            if row is None:
                return None
            return dict(row)

    async def fetch_all(self, sql: str, params: list[Any] | tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        async with self._connection_access() as (conn, _):
            cursor = await conn.execute(sql, params)
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def execute_and_get_id(self, sql: str, params: list[Any] | tuple[Any, ...] = ()) -> int | None:
        async with self._connection_access() as (conn, in_transaction):
            cursor = await conn.execute(sql, params)
            if not in_transaction:
                await conn.commit()
            return cursor.lastrowid

    # ── Health & lifecycle ───────────────────────────────────────

    async def is_healthy(self) -> bool:
        """供 /health 端点使用：连接还活着且能执行最简查询返回 True。"""
        if self._conn is None:
            return False
        try:
            async with self._connection_access() as (conn, _):
                await conn.execute("SELECT 1")
            return True
        except Exception:
            return False

    def get_db_path(self) -> Path:
        return self._db_path

    async def export_to_buffer(self) -> bytes | None:
        if self._conn is None:
            return None
        async with self._connection_access() as (conn, _):
            cursor = await conn.execute("PRAGMA wal_checkpoint(FULL)")
            await cursor.fetchall()
            await cursor.close()
            await conn.commit()
        return self._db_path.read_bytes()

    async def close(self) -> None:
        if self._conn is None:
            return
        if self._tx_owner is self._current_task():
            raise RuntimeError("cannot close database inside an active transaction")
        async with self._connection_lock:
            conn = self._conn
            if conn is None:
                return
            try:
                await conn.close()
            except Exception:
                pass
            finally:
                self._conn = None
                self._tx_owner = None
                self._tx_depth = 0
