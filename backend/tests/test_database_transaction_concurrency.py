from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import pytest_asyncio

from database.connection import DatabaseConnection


class _RollbackOwner(Exception):
    pass


@pytest_asyncio.fixture
async def db(tmp_path: Path):
    connection = DatabaseConnection(tmp_path)
    await connection.init()
    await connection.execute(
        "CREATE TABLE transaction_task_probe (value TEXT PRIMARY KEY)"
    )
    try:
        yield connection
    finally:
        await connection.close()


async def _assert_not_set(event: asyncio.Event) -> None:
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(event.wait(), timeout=0.05)


@pytest.mark.asyncio
async def test_concurrent_transactions_are_top_level_and_task_isolated(
    db: DatabaseConnection,
):
    owner_entered = asyncio.Event()
    release_owner = asyncio.Event()
    contender_attempted = asyncio.Event()
    contender_entered = asyncio.Event()

    async def owner() -> None:
        try:
            async with db.transaction():
                await db.execute(
                    "INSERT INTO transaction_task_probe(value) VALUES (?)",
                    ["owner-rolled-back"],
                )
                owner_entered.set()
                await release_owner.wait()
                raise _RollbackOwner
        except _RollbackOwner:
            pass

    async def contender() -> None:
        await owner_entered.wait()
        contender_attempted.set()
        async with db.transaction():
            contender_entered.set()
            await db.execute(
                "INSERT INTO transaction_task_probe(value) VALUES (?)",
                ["contender-committed"],
            )

    owner_task = asyncio.create_task(owner())
    contender_task = asyncio.create_task(contender())
    await contender_attempted.wait()
    await _assert_not_set(contender_entered)

    release_owner.set()
    await asyncio.wait_for(
        asyncio.gather(owner_task, contender_task),
        timeout=2,
    )

    rows = await db.fetch_all(
        "SELECT value FROM transaction_task_probe ORDER BY value"
    )
    assert rows == [{"value": "contender-committed"}]


@pytest.mark.asyncio
async def test_crud_from_another_task_waits_for_transaction_rollback(
    db: DatabaseConnection,
):
    owner_entered = asyncio.Event()
    release_owner = asyncio.Event()
    crud_attempted = asyncio.Event()
    crud_done = asyncio.Event()

    async def owner() -> None:
        try:
            async with db.transaction():
                await db.execute(
                    "INSERT INTO transaction_task_probe(value) VALUES (?)",
                    ["owner-rolled-back"],
                )
                owner_entered.set()
                await release_owner.wait()
                raise _RollbackOwner
        except _RollbackOwner:
            pass

    async def external_crud() -> None:
        await owner_entered.wait()
        crud_attempted.set()
        await db.execute(
            "INSERT INTO transaction_task_probe(value) VALUES (?)",
            ["external-committed"],
        )
        crud_done.set()

    owner_task = asyncio.create_task(owner())
    crud_task = asyncio.create_task(external_crud())
    await crud_attempted.wait()
    await _assert_not_set(crud_done)

    release_owner.set()
    await asyncio.wait_for(asyncio.gather(owner_task, crud_task), timeout=2)

    rows = await db.fetch_all(
        "SELECT value FROM transaction_task_probe ORDER BY value"
    )
    assert rows == [{"value": "external-committed"}]


@pytest.mark.asyncio
async def test_fetch_from_another_task_cannot_see_uncommitted_rows(
    db: DatabaseConnection,
):
    owner_entered = asyncio.Event()
    release_owner = asyncio.Event()
    fetch_attempted = asyncio.Event()
    fetch_done = asyncio.Event()
    fetched: dict[str, object] = {}

    async def owner() -> None:
        try:
            async with db.transaction():
                await db.execute(
                    "INSERT INTO transaction_task_probe(value) VALUES (?)",
                    ["uncommitted"],
                )
                owner_entered.set()
                await release_owner.wait()
                raise _RollbackOwner
        except _RollbackOwner:
            pass

    async def external_fetch() -> None:
        await owner_entered.wait()
        fetch_attempted.set()
        fetched["row"] = await db.fetch_one(
            "SELECT value FROM transaction_task_probe WHERE value = ?",
            ["uncommitted"],
        )
        fetch_done.set()

    owner_task = asyncio.create_task(owner())
    fetch_task = asyncio.create_task(external_fetch())
    await fetch_attempted.wait()
    await _assert_not_set(fetch_done)

    release_owner.set()
    await asyncio.wait_for(asyncio.gather(owner_task, fetch_task), timeout=2)

    assert fetched == {"row": None}


@pytest.mark.asyncio
async def test_same_task_nested_transactions_use_savepoints_without_deadlock(
    db: DatabaseConnection,
):
    async def write_nested() -> None:
        async with db.transaction():
            await db.execute(
                "INSERT INTO transaction_task_probe(value) VALUES (?)",
                ["outer"],
            )
            try:
                async with db.transaction():
                    await db.execute(
                        "INSERT INTO transaction_task_probe(value) VALUES (?)",
                        ["inner-rolled-back"],
                    )
                    raise _RollbackOwner
            except _RollbackOwner:
                pass
            async with db.transaction():
                await db.execute(
                    "INSERT INTO transaction_task_probe(value) VALUES (?)",
                    ["inner-committed"],
                )

    await asyncio.wait_for(write_nested(), timeout=2)

    rows = await db.fetch_all(
        "SELECT value FROM transaction_task_probe ORDER BY value"
    )
    assert rows == [
        {"value": "inner-committed"},
        {"value": "outer"},
    ]


@pytest.mark.asyncio
async def test_read_transaction_keeps_a_snapshot_without_reserving_the_writer(
    tmp_path: Path,
):
    reader = DatabaseConnection(tmp_path)
    writer = DatabaseConnection(tmp_path)
    await reader.init()
    await writer.init()
    try:
        await writer.execute(
            "CREATE TABLE IF NOT EXISTS read_snapshot_probe "
            "(value TEXT PRIMARY KEY)"
        )
        await writer.execute(
            "INSERT INTO read_snapshot_probe(value) VALUES ('first')"
        )

        async with reader.transaction(write=False):
            before = await reader.fetch_all(
                "SELECT value FROM read_snapshot_probe ORDER BY value"
            )
            await writer.execute(
                "INSERT INTO read_snapshot_probe(value) VALUES ('second')"
            )
            during = await reader.fetch_all(
                "SELECT value FROM read_snapshot_probe ORDER BY value"
            )

        after = await reader.fetch_all(
            "SELECT value FROM read_snapshot_probe ORDER BY value"
        )
        assert before == during == [{"value": "first"}]
        assert after == [{"value": "first"}, {"value": "second"}]
    finally:
        await writer.close()
        await reader.close()


@pytest.mark.asyncio
async def test_read_transaction_rejects_write_receipt_mode(db):
    with pytest.raises(ValueError, match="must be writable"):
        async with db.transaction(
            write=False,
            cancellation_linearizable=True,
        ):
            pass
