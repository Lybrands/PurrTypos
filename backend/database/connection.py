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
import os
import shutil
import sqlite3
from uuid import uuid4
from contextlib import asynccontextmanager, nullcontext
from datetime import datetime
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
        self._file_participants = []

    async def init(self, *, initialize_schema: bool = True) -> None:
        self._data_dir.mkdir(parents=True, exist_ok=True)
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
        if initialize_schema:
            from database.schema import init_schema
            await init_schema(self)
            from application.creation_material_service import recover_materials
            await recover_materials(self)

    def _ensure_conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database not initialised – call init() first")
        return self._conn

    def _current_task(self) -> asyncio.Task[Any]:
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("database operation must run inside an asyncio Task")
        return task

    def current_task_owns_transaction(self) -> bool:
        """Return whether the caller is already inside this connection's transaction.

        Persistence adapters normally own their cancellation-linearizable
        transaction. A host projection that is deliberately part of another
        repository commit must instead join that ambient transaction so all
        effects share one COMMIT or ROLLBACK.
        """

        task = asyncio.current_task()
        return task is not None and self._tx_owner is task

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

    def enlist_file_change(self, participant):
        if not self.current_task_owns_transaction():
            raise RuntimeError("file changes require a host transaction")
        self._file_participants.append(participant)

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
            participant_count = len(self._file_participants)
            sp_name = f"sp_{self._tx_depth}"
            await conn.execute(f"SAVEPOINT {sp_name}")
            self._tx_depth += 1
            try:
                yield self
            except BaseException:
                del self._file_participants[participant_count:]
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
            self._file_participants = []
            prepared = []
            try:
                yield self
                for participant in self._file_participants:
                    prepared.append(participant)
                    participant.prepare()
                # Once files are published, resolve COMMIT before releasing the
                # connection or acknowledging cancellation.
                if cancellation_linearizable or prepared:
                    await _await_task_uninterruptibly(asyncio.create_task(conn.commit()))
                else:
                    await conn.commit()
            except BaseException:
                await _rollback_uninterruptibly(conn)
                for participant in reversed(prepared):
                    participant.rollback()
                raise
            else:
                for participant in prepared:
                    participant.committed()
            finally:
                self._file_participants = []

        finally:
            if self._tx_owner is task:
                self._tx_owner = None
                self._tx_depth = 0
            self._connection_lock.release()

    # ── Basic operations (transaction-aware) ─────────────────────

    async def execute(self, sql: str, params: list[Any] | tuple[Any, ...] = ()) -> None:
        async with self._connection_access() as (conn, in_transaction):
            try:
                await conn.execute(sql, params)
            except BaseException:
                if not in_transaction:
                    await _rollback_uninterruptibly(conn)
                raise
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
            try:
                cursor = await conn.execute(sql, params)
            except BaseException:
                if not in_transaction:
                    await _rollback_uninterruptibly(conn)
                raise
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

    async def get_connected_db_path(self) -> Path:
        databases = await self.fetch_all("PRAGMA database_list")
        for database in databases:
            if database["name"] == "main" and database["file"]:
                return Path(database["file"]).resolve()
        raise RuntimeError("当前数据库连接没有本地文件路径")

    async def export_to_buffer(self) -> bytes | None:
        return await self.export_with_resources(lambda payload: payload)

    async def export_with_resources(self, capture):
        """Capture host state and its file resources under one connection barrier."""
        if self._conn is None:
            return None
        async with self._connection_access() as (conn, in_transaction):
            if in_transaction:
                raise RuntimeError("cannot export inside an active transaction")
            cursor = await conn.execute("PRAGMA wal_checkpoint(FULL)")
            await cursor.fetchall()
            await cursor.close()
            await conn.commit()
            return await asyncio.to_thread(capture, self._db_path.read_bytes())

    async def import_from_buffer(self, payload: bytes, *, resource_install=None) -> None:
        """Atomically replace the active SQLite database from an uploaded backup.

        The candidate is validated before the live connection is closed. The
        same DatabaseConnection object is retained so repositories held by the
        application composition continue to reference the active database.
        """
        if not payload:
            raise ValueError("数据库备份为空")
        self._data_dir.mkdir(parents=True, exist_ok=True)
        candidate = self._db_path.with_name(f"{self._db_path.name}.{uuid4().hex}.importing")
        candidate.write_bytes(payload)
        try:
            try:
                validation = sqlite3.connect(candidate)
                try:
                    integrity = validation.execute("PRAGMA integrity_check").fetchone()
                    if not integrity or integrity[0] != "ok":
                        raise ValueError("数据库完整性校验失败")
                    has_books = validation.execute(
                        "SELECT 1 FROM sqlite_master "
                        "WHERE type='table' AND name='books'"
                    ).fetchone()
                    if not has_books:
                        raise ValueError("不是有效的 PurrTypos 数据库备份")
                    knowledge = validation.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='novel_knowledge_bindings'").fetchone()
                    if knowledge:
                        validation.execute("UPDATE novel_knowledge_bindings SET host_id='',state='reauthorization_required',semantic_config=NULL,generation=generation+1,version=version+1 WHERE state!='unbound'")
                        validation.commit()
                    if resource_install is None and validation.execute("SELECT 1 FROM sqlite_master WHERE name='creation_material_books'").fetchone():
                        validation.execute("UPDATE creation_material_books SET state='unavailable'")
                        validation.commit()
                    from database.writing_technique_retirement import prepare_import_schema
                    await prepare_import_schema(validation)
                finally:
                    validation.close()
            except sqlite3.DatabaseError as error:
                raise ValueError("不是有效的 PurrTypos 数据库备份") from error

            async with self._connection_lock:
                if self._tx_owner is not None:
                    raise RuntimeError("数据库正在执行事务，请稍后重试")
                current = self._conn
                if current is not None:
                    await current.close()
                    self._conn = None

                backup = self._db_path.with_name(
                    f"{self._db_path.name}.before-import-"
                    f"{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid4().hex[:8]}.bak"
                )
                if self._db_path.exists():
                    shutil.copy2(self._db_path, backup)
                for suffix in ("-wal", "-shm"):
                    sidecar = Path(f"{self._db_path}{suffix}")
                    if sidecar.exists():
                        sidecar.unlink()
                try:
                    with resource_install() if resource_install else nullcontext():
                        os.replace(candidate, self._db_path)
                        await self._open_imported_connection()
                except BaseException:
                    async def restore_database():
                        if self._conn is not None:
                            await self._conn.close()
                            self._conn = None
                        for suffix in ("-wal", "-shm"):
                            Path(f"{self._db_path}{suffix}").unlink(missing_ok=True)
                        if backup.exists():
                            shutil.copy2(backup, self._db_path)
                        await self._open_imported_connection()
                    await _await_task_uninterruptibly(asyncio.create_task(restore_database()))
                    raise
                self._tx_owner = None
                self._tx_depth = 0
        finally:
            if candidate.exists():
                candidate.unlink()

    async def _open_imported_connection(self):
        self._conn = await aiosqlite.connect(self._db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA busy_timeout=5000")
        await self._conn.commit()

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
