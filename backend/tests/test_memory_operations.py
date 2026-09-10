from __future__ import annotations

import pytest
import pytest_asyncio

from application.memory_operations import (
    MemoryApplicationService,
    MemoryOperationError,
    memory_metadata,
)
from infrastructure.memory import (
    MemoryComponentResource,
    MemoryResourceConfiguration,
)
from purra_mem0 import EmbeddingResult, MemoryRef
from purra.contracts import ContextEvidenceReceipt
from purra_mem0 import MemorySource
from database.connection import DatabaseConnection


@pytest_asyncio.fixture
async def temp_db(tmp_path):
    db = DatabaseConnection(tmp_path / "business")
    await db.init()
    yield db
    await db.close()


class _EmbeddingGateway:
    async def embed(self, texts, signal):
        return EmbeddingResult(
            tuple((1.0, 0.0, 0.0, 0.0) for _ in texts),
            input_tokens=sum(len(text) for text in texts),
        )

    async def close(self):
        return None


async def _service(temp_db, tmp_path):
    await temp_db.execute(
        "INSERT INTO books (id, title) VALUES (?, ?)",
        ["book-memory", "Memory Book"],
    )
    resource = MemoryComponentResource(
        MemoryResourceConfiguration(tmp_path, 4),
        embedding_gateway=_EmbeddingGateway(),
    )
    return MemoryApplicationService(temp_db, resource), resource


@pytest.mark.asyncio
async def test_manual_create_replays_by_operation_not_content(temp_db, tmp_path):
    service, resource = await _service(temp_db, tmp_path)
    metadata = memory_metadata(kind="canon", pinned=True)

    first = await service.create_manual(
        book_id="book-memory",
        operation_key="command-1",
        text="主角害怕深水。",
        metadata=metadata,
    )
    replay = await service.create_manual(
        book_id="book-memory",
        operation_key="command-1",
        text="主角害怕深水。",
        metadata=metadata,
    )
    independent = await service.create_manual(
        book_id="book-memory",
        operation_key="command-2",
        text="主角害怕深水。",
        metadata=metadata,
    )

    assert replay == first
    assert independent["id"] != first["id"]
    with pytest.raises(MemoryOperationError) as caught:
        await service.create_manual(
            book_id="book-memory",
            operation_key="command-1",
            text="同一个命令不能改成另一段内容。",
            metadata=metadata,
        )
    assert caught.value.code == "memory_idempotency_conflict"
    await resource.close()


@pytest.mark.asyncio
async def test_update_requires_current_version_and_keeps_book_scope(temp_db, tmp_path):
    service, resource = await _service(temp_db, tmp_path)
    created = await service.create_manual(
        book_id="book-memory",
        operation_key="create",
        text="门上挂着铜锁。",
        metadata=memory_metadata(kind="plot"),
    )

    updated = await service.update(
        book_id="book-memory",
        item_id=created["id"],
        version=created["version"],
        operation_key="edit",
        text="门上改挂银锁。",
        metadata=memory_metadata(kind="plot", importance=4),
    )
    assert updated["version"] == created["version"] + 1
    assert updated["text"] == "门上改挂银锁。"

    with pytest.raises(MemoryOperationError) as caught:
        await service.set_state(
            book_id="book-memory",
            item_id=created["id"],
            version=created["version"],
            operation_key="archive-stale",
            state="disabled",
            reason="archived",
        )
    assert caught.value.code == "memory_version_conflict"
    await resource.close()


@pytest.mark.asyncio
async def test_link_binds_explicit_versions(temp_db, tmp_path):
    service, resource = await _service(temp_db, tmp_path)
    first = await service.create_manual(
        book_id="book-memory",
        operation_key="first",
        text="青铜钥匙属于北塔。",
        metadata=memory_metadata(kind="world"),
    )
    second = await service.create_manual(
        book_id="book-memory",
        operation_key="second",
        text="北塔入口在月井之后。",
        metadata=memory_metadata(kind="world"),
    )

    receipt = await service.link(
        book_id="book-memory",
        from_ref=MemoryRef(first["id"], first["version"]),
        to_ref=MemoryRef(second["id"], second["version"]),
        relation="supports",
        note="同一地点线索",
        operation_key="link-1",
    )
    assert receipt["state"] == "complete"
    assert receipt["ids"]
    await resource.close()


@pytest.mark.asyncio
async def test_component_history_links_and_delete_share_the_same_records(
    temp_db,
    tmp_path,
):
    service, resource = await _service(temp_db, tmp_path)
    first = await service.create_manual(
        book_id="book-memory",
        operation_key="history-first",
        text="银钥匙属于北塔。",
        metadata=memory_metadata(kind="world"),
    )
    second = await service.create_manual(
        book_id="book-memory",
        operation_key="history-second",
        text="北塔入口位于月井之后。",
        metadata=memory_metadata(kind="world"),
    )
    await service.link(
        book_id="book-memory",
        from_ref=MemoryRef(first["id"], first["version"]),
        to_ref=MemoryRef(second["id"], second["version"]),
        relation="supports",
        note="同一地点",
        operation_key="history-link",
    )

    history = await service.history(
        book_id="book-memory",
        item_id=first["id"],
    )
    links = await service.links(
        book_id="book-memory",
        item_id=first["id"],
    )
    deleted = await service.delete(
        book_id="book-memory",
        item_id=first["id"],
        version=first["version"],
        operation_key="history-delete",
    )

    assert history
    assert len(links["items"]) == 1
    assert links["items"][0]["relation"] == "supports"
    assert deleted["state"] == "complete"
    assert await service.get(
        book_id="book-memory",
        item_id=first["id"],
        include_inactive=True,
    ) is None
    await resource.close()


@pytest.mark.asyncio
async def test_unconfigured_component_fails_without_sqlite_fallback(temp_db):
    await temp_db.execute(
        "INSERT INTO books (id, title) VALUES (?, ?)",
        ["book-memory", "Memory Book"],
    )
    service = MemoryApplicationService(temp_db, None)

    with pytest.raises(MemoryOperationError) as caught:
        await service.create_manual(
            book_id="book-memory",
            operation_key="no-component",
            text="不会写入旧表。",
            metadata=memory_metadata(kind="canon"),
        )
    assert caught.value.code == "memory_component_unavailable"
    table = await temp_db.fetch_one(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
        ["memory_items"],
    )
    assert table is None


@pytest.mark.asyncio
async def test_source_head_change_rejects_previously_assembled_evidence(
    temp_db,
    tmp_path,
):
    service, resource = await _service(temp_db, tmp_path)
    await temp_db.execute(
        "INSERT INTO memory_source_heads "
        "(book_id, source_id, revision, deleted) VALUES (?, ?, 1, 0)",
        ["book-memory", "chapter:one"],
    )
    created = await service.add_source(
        book_id="book-memory",
        key="source-add",
        text="钟楼只在雨夜开放。",
        source=MemorySource("chapter:one", "1"),
        metadata=memory_metadata(kind="world"),
    )
    context = await service.build_context(
        book_id="book-memory",
        operation_key="assemble",
        query="",
        selected_ids=(created["id"],),
        token_budget=2_000,
        recall_limit=4,
    )
    receipt, = context.receipts
    await service.validate_evidence(
        book_id="book-memory",
        receipts=(receipt,),
    )

    await temp_db.execute(
        "UPDATE memory_source_heads SET revision = 2 WHERE book_id = ? "
        "AND source_id = ?",
        ["book-memory", "chapter:one"],
    )
    with pytest.raises(MemoryOperationError) as caught:
        await service.validate_evidence(
            book_id="book-memory",
            receipts=(receipt,),
        )
    assert caught.value.code == "memory_context_stale"
    await resource.close()


@pytest.mark.asyncio
async def test_book_bound_validator_rejects_other_memory_namespace(
    temp_db,
    tmp_path,
):
    service, resource = await _service(temp_db, tmp_path)
    wrong = ContextEvidenceReceipt(
        evidence_id="mem0:store:item:1",
        context_block="writing.memory",
        source="mem0/purra-other-scope",
        item_id="item",
        version=1,
        metadata={"sourceId": "manual", "sourceRevision": "1"},
    )
    with pytest.raises(MemoryOperationError) as caught:
        await service.validate_evidence(
            book_id="book-memory",
            receipts=(wrong,),
        )
    assert caught.value.code == "memory_context_stale"
    await resource.close()


@pytest.mark.asyncio
async def test_memory_model_follows_book_selection_and_explicit_override(temp_db):
    import json

    async def setting(key, value):
        await temp_db.execute(
            'INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)',
            [key, json.dumps(value)],
        )

    models = [dict(id=name, name=name, apiKey='test-key') for name in ('a', 'b', 'review')]
    await setting('ai_model_configs', models)
    await setting('writing_current_model:book-a', 'a')
    await setting('writing_current_model:book-b', 'b')
    service = MemoryApplicationService(temp_db, None)
    assert (await service._strict_memory_model(book_id='book-a'))['id'] == 'a'
    assert (await service._strict_memory_model(book_id='book-b'))['id'] == 'b'
    await setting('writing_current_model:book-a', 'b')
    assert (await service._strict_memory_model(book_id='book-a'))['id'] == 'b'
    await setting('memory_model_id', 'review')
    assert (await service._strict_memory_model(book_id='book-a'))['id'] == 'review'
    await setting('memory_model_id', '')
    assert (await service._strict_memory_model(book_id='book-a'))['id'] == 'b'
    # A missing selection must not silently choose an arbitrary configured model.
    with pytest.raises(MemoryOperationError, match='memory_model_unconfigured'):
        await service._strict_memory_model(book_id='unknown')
    await setting('memory_model_id', 'deleted')
    with pytest.raises(MemoryOperationError, match='memory_model_unconfigured'):
        await service._strict_memory_model(book_id='book-a')


@pytest.mark.asyncio
async def test_memory_completion_keeps_book_scope_per_provider(temp_db, monkeypatch):
    class Resource:
        def providers(self, *, budget, complete):
            return complete

    service = MemoryApplicationService(temp_db, Resource())

    async def complete(messages, result_capacity_target_tokens, signal, *, book_id):
        return book_id

    monkeypatch.setattr(service, '_complete', complete)
    a = service._providers('a', inference=True, book_id='book-a')
    b = service._providers('b', inference=True, book_id='book-b')
    import asyncio
    assert await asyncio.gather(a([], 100, None), b([], 100, None)) == ['book-a', 'book-b']
