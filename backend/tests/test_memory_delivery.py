from __future__ import annotations

import pytest
import pytest_asyncio

from application.memory_delivery import (
    MemoryDeliveryService,
    record_book_deletion,
    record_source_revision,
)
from application.memory_operations import MemoryApplicationService, memory_metadata
from database.connection import DatabaseConnection
from infrastructure.memory import MemoryComponentResource, MemoryResourceConfiguration
from purra.retrieval import RetrievalRequest
from purra_mem0 import EmbeddingResult


class _EmbeddingGateway:
    async def embed(self, texts, signal):
        return EmbeddingResult(
            tuple((1.0, 0.0, 0.0, 0.0) for _ in texts),
            input_tokens=sum(len(text) for text in texts),
        )

    async def close(self):
        return None


@pytest_asyncio.fixture
async def setup(tmp_path):
    db = DatabaseConnection(tmp_path / "business")
    await db.init()
    await db.execute("INSERT INTO books (id, title) VALUES ('book', 'Book')")
    resource = MemoryComponentResource(
        MemoryResourceConfiguration(tmp_path / "memory", 4),
        embedding_gateway=_EmbeddingGateway(),
    )
    service = MemoryApplicationService(db, resource)
    try:
        yield db, resource, service
    finally:
        await resource.close()
        await db.close()


@pytest.mark.asyncio
async def test_source_revision_fences_old_hit_before_delivery(setup):
    db, _resource, memory = setup
    delivery = MemoryDeliveryService(db, memory)
    async with db.transaction(cancellation_linearizable=True):
        first_keys = await record_source_revision(
            db,
            book_id="book",
            source_id="character:1#chunk:0001",
            text="林夜害怕深水。",
            metadata=memory_metadata(kind="character"),
            inference=False,
        )
    assert [item.status for item in await delivery.deliver_many(first_keys)] == [
        "completed"
    ]
    first_hits = await memory.retrieve(
        book_id="book",
        request=RetrievalRequest("深水", 4, run_id="run-one"),
    )
    assert len(first_hits) == 1

    async with db.transaction(cancellation_linearizable=True):
        second_keys = await record_source_revision(
            db,
            book_id="book",
            source_id="character:1#chunk:0001",
            text="林夜现在能够潜水。",
            metadata=memory_metadata(kind="character"),
            inference=False,
        )
    assert await memory.retrieve(
        book_id="book",
        request=RetrievalRequest("深水", 4, run_id="run-two"),
    ) == ()
    assert [item.status for item in await delivery.deliver_many(second_keys)] == [
        "completed",
        "completed",
    ]
    current = await memory.retrieve(
        book_id="book",
        request=RetrievalRequest("潜水", 4, run_id="run-three"),
    )
    assert [hit.content for hit in current] == ["林夜现在能够潜水。"]


@pytest.mark.asyncio
async def test_book_deletion_task_removes_manual_and_source_memory(setup):
    db, _resource, memory = setup
    await memory.create_manual(
        book_id="book",
        operation_key="manual",
        text="手工记忆。",
        metadata=memory_metadata(kind="canon"),
    )
    async with db.transaction(cancellation_linearizable=True):
        key = await record_book_deletion(db, book_id="book")
        await db.execute("DELETE FROM books WHERE id = 'book'")

    receipt = await MemoryDeliveryService(db, memory).deliver_book_deletion(key)
    assert receipt.status == "completed"
    await db.execute("INSERT INTO books (id, title) VALUES ('book', 'Recreated')")
    assert await memory.list_records(book_id="book") == ()
    assert await db.fetch_one(
        "SELECT status FROM memory_book_deletions WHERE book_id = 'book'"
    ) == {"status": "completed"}
