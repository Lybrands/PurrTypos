from __future__ import annotations

import asyncio
import inspect
from functools import partial
from pathlib import Path

import pytest

import dependencies
from purra.cancellation import await_with_cancellation
from purra.contracts import ExecutionState
from database.connection import DatabaseConnection
from domains.writing.tools.contracts import _err
from exceptions import DatabaseNotReadyError
from infrastructure.persistence.writing.sqlite_writing_tool_memory_repository import (
    SqliteWritingToolMemoryRepository,
)
from infrastructure.writing import (
    WritingSkillCatalog,
    WritingToolDependencies,
    build_writing_tool_catalog,
)
from infrastructure.writing.tools.handlers import WRITING_TOOL_OPERATIONS
from infrastructure.writing.tools.runtime import bind_writing_tool_handlers


BACKEND_DIR = Path(__file__).resolve().parent.parent
SKILL_ITEMS = tuple(
    WritingSkillCatalog(BACKEND_DIR / "skills").skill_items()
)


def _tool_dependencies(db) -> WritingToolDependencies:
    return WritingToolDependencies(
        db,
        SqliteWritingToolMemoryRepository(db),
    )


def _registration(db, name: str):
    catalog = build_writing_tool_catalog(
        dependencies=_tool_dependencies(db),
        skill_items=SKILL_ITEMS,
    )
    return next(
        item for item in catalog.registrations() if item.schema.name == name
    )


@pytest.mark.asyncio
async def test_two_catalogs_keep_explicit_db_dependencies_isolated(
    monkeypatch,
):
    from infrastructure.writing.tools.handlers import book_style_tools

    global_db = object()
    first_db = object()
    second_db = object()
    monkeypatch.setattr(dependencies, "_db_instance", global_db)
    observed: list[tuple[object, str]] = []

    async def _get_book_style(db, book_id):
        await asyncio.sleep(0)
        observed.append((db, book_id))
        return None

    monkeypatch.setattr(book_style_tools, "get_book_style", _get_book_style)
    first = _registration(first_db, "getBookStyle")
    second = _registration(second_db, "getBookStyle")

    await asyncio.gather(
        first.handler(ExecutionState(domain={"bookId": "first"}), {}),
        second.handler(ExecutionState(domain={"bookId": "second"}), {}),
    )

    assert set(observed) == {(first_db, "first"), (second_db, "second")}
    assert dependencies.get_db() is global_db


@pytest.mark.asyncio
async def test_memory_repository_writes_work_with_no_global_db(
    tmp_path,
    monkeypatch,
):
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first_dir.mkdir()
    second_dir.mkdir()
    first_db = DatabaseConnection(first_dir)
    second_db = DatabaseConnection(second_dir)
    await first_db.init()
    await second_db.init()
    monkeypatch.setattr(dependencies, "_db_instance", None)
    try:
        first = _registration(first_db, "createMemory")
        second = _registration(second_db, "createMemory")
        first_result, second_result = await asyncio.gather(
            first.handler(
                ExecutionState(domain={"bookId": "shared-book"}),
                {"kind": "summary", "content": "first database only"},
            ),
            second.handler(
                ExecutionState(domain={"bookId": "shared-book"}),
                {"kind": "summary", "content": "second database only"},
            ),
        )

        assert first_result.error_code is None
        assert second_result.error_code is None
        first_rows = await first_db.fetch_all(
            "SELECT content FROM memory_items ORDER BY id"
        )
        second_rows = await second_db.fetch_all(
            "SELECT content FROM memory_items ORDER BY id"
        )
        assert first_rows == [{"content": "first database only"}]
        assert second_rows == [{"content": "second database only"}]
        with pytest.raises(DatabaseNotReadyError):
            dependencies.get_db()
    finally:
        await first_db.close()
        await second_db.close()


def test_clear_db_only_clears_the_expected_lifespan_owner(monkeypatch):
    owner = object()
    replacement = object()
    monkeypatch.setattr(dependencies, "_db_instance", owner)

    dependencies.clear_db(replacement)
    assert dependencies.get_db() is owner

    dependencies.clear_db(owner)
    with pytest.raises(DatabaseNotReadyError):
        dependencies.get_db()


def test_all_bound_handlers_keep_the_writing_operation_call_signature():
    bound = bind_writing_tool_handlers(
        WRITING_TOOL_OPERATIONS,
        WritingToolDependencies(
            object(),  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
        ),
    )

    assert len(bound) == 36
    for name, handler in bound.items():
        assert isinstance(handler, partial)
        assert handler.func is WRITING_TOOL_OPERATIONS[name]
        assert inspect.iscoroutinefunction(handler)
        assert len(inspect.signature(handler).parameters) == 3


@pytest.mark.asyncio
async def test_confirmed_memory_write_returns_receipt_when_canceled_during_commit(
    tmp_path,
    monkeypatch,
):
    db = DatabaseConnection(tmp_path)
    await db.init()
    try:
        registration = _registration(db, "createMemory")
        connection = db._ensure_conn()
        original_commit = connection.commit
        durable = asyncio.Event()
        release_ack = asyncio.Event()
        commit_cancel_count = 0

        async def _commit_then_hold_ack():
            nonlocal commit_cancel_count
            await original_commit()
            durable.set()
            try:
                await release_ack.wait()
            except asyncio.CancelledError:
                commit_cancel_count += 1
                raise

        monkeypatch.setattr(connection, "commit", _commit_then_hold_ack)
        assert registration.cancellation_linearizable is True
        task = asyncio.create_task(await_with_cancellation(
            registration.handler(
                ExecutionState(domain={"bookId": "book-a"}),
                {"kind": "summary", "content": "已经提交的记忆"},
            ),
            None,
            completion_wins_after_cancel=(
                registration.cancellation_linearizable
            ),
        ))
        await asyncio.wait_for(durable.wait(), timeout=1)

        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        assert commit_cancel_count == 0

        release_ack.set()
        result = await asyncio.wait_for(task, timeout=1)
        assert result.error_code is None
        assert '"success": true' in result.content
        assert commit_cancel_count == 0
        row = await db.fetch_one(
            "SELECT content FROM memory_items WHERE book_id = ?",
            ["book-a"],
        )
        assert row == {"content": "已经提交的记忆"}
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_atomic_handler_rolls_back_when_operation_returns_error(
    tmp_path,
):
    db = DatabaseConnection(tmp_path)
    await db.init()
    try:
        async def _write_then_fail(dependencies, ctx, _args, _send_chunk):
            await dependencies.db.execute(
                "INSERT INTO books (id, title) VALUES (?, ?)",
                ["rolled-back", "must not persist"],
            )
            ctx["chapterId"] = "mutated"
            ctx["readToolCache"].clear()
            return _err({"success": False, "error": "synthetic failure"})

        handler = bind_writing_tool_handlers(
            {"writeThenFail": _write_then_fail},
            _tool_dependencies(db),
            atomic_operation_names=frozenset({"writeThenFail"}),
        )["writeThenFail"]

        context = {
            "chapterId": "original",
            "readToolCache": {"stable": "cached"},
        }
        result = await handler(context, {}, None)
        assert "synthetic failure" in result.content
        assert context == {
            "chapterId": "original",
            "readToolCache": {"stable": "cached"},
        }
        assert await db.fetch_one(
            "SELECT id FROM books WHERE id = ?",
            ["rolled-back"],
        ) is None
    finally:
        await db.close()
