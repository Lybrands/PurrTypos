"""Component-backed retrieval, host scope and durable Core evidence."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
import pytest_asyncio

from application.composition_factory import create_agent_composition
from application.memory_operations import MemoryApplicationService, memory_metadata
from application.request_mapping import to_writing_agent_request, writing_run_options
from application.writing_method_service import WritingMethodService
from database.connection import DatabaseConnection
from infrastructure.memory import MemoryComponentResource, MemoryResourceConfiguration
from infrastructure.persistence.sqlite_run_repository import SqliteRunRepository
from infrastructure.persistence.writing import SqliteWritingSourceRepository
from infrastructure.writing import (
    WritingSkillCatalog,
    WritingToolDependencies,
    build_writing_tool_catalog,
)
from infrastructure.writing.retrieval import WritingMemoryRetriever, WritingMethodRetriever
from purra.api import AgentExecutionCheckpoint
from purra.cancellation import OperationCanceled
from purra.contracts import (
    AgentMessage,
    ExecutionState,
    RunCreateParams,
    ToolBatchRequest,
    ToolCall,
    ToolPlanningDisposition,
)
from purra.evidence import RunEvidenceStore
from purra.ports import RunCommit
from purra.retrieval import RetrievalError, RetrievalRequest, Retriever
from purra.tools.executor import CoreToolExecutor
from purra_mem0 import EmbeddingResult
from schemas.ai import ChatStreamRequest


class _EmbeddingGateway:
    async def embed(self, texts, signal):
        return EmbeddingResult(
            tuple((1.0, 0.0, 0.0, 0.0) for _ in texts),
            input_tokens=sum(len(text) for text in texts),
        )

    async def close(self):
        return None


@pytest_asyncio.fixture
async def db(tmp_path):
    connection = DatabaseConnection(tmp_path / "business")
    await connection.init()
    for book_id in ("book-a", "book-b"):
        await connection.execute(
            "INSERT INTO books (id, title) VALUES (?, ?)",
            [book_id, book_id],
        )
    try:
        yield connection
    finally:
        await connection.close()


@pytest_asyncio.fixture
async def memory_resource(tmp_path):
    resource = MemoryComponentResource(
        MemoryResourceConfiguration(tmp_path / "memory", 4),
        embedding_gateway=_EmbeddingGateway(),
    )
    try:
        yield resource
    finally:
        await resource.close()


async def _run(db, *, recommend=False, session_id=None):
    composition = create_agent_composition(db)
    try:
        body = ChatStreamRequest(
            bookId="book-a",
            sessionId=session_id,
            streamId="retrieval-turn" if session_id else None,
            messages=[{"role": "user", "content": (
                "[写作方法推荐] 寻找悬念方法" if recommend else "检索红门"
            )}],
            apiKey="unused",
            apiProvider="openai",
            chatAgentMode="agent",
            options={
                "model": "model",
                "model_profile": "deepseek:deepseek-v4-flash", "profile_binding": "compatible",
            },
        )
        request = await composition.prepare_request(to_writing_agent_request(
            body,
            {"model": "model", "model_profile": "deepseek:deepseek-v4-flash", "profile_binding": "compatible"},
        ))
        options = composition.bind_run_profile(
            request,
            writing_run_options(request, {}),
        )
        assert options.binding.attributes["bookId"] == "book-a"
        assert options.binding.namespace == (
            "writing.chat.request" if session_id else "writing.context"
        )
        return await SqliteRunRepository(
            db,
            owner_id="retrieval-test",
        ).create(RunCreateParams(
            session_id=session_id,
            prompt="retrieval",
            mode="agent",
            binding=options.binding,
        ))
    finally:
        await composition.shutdown()


def _memory_service(db, resource):
    return MemoryApplicationService(db, resource)


def _catalog(db, resource):
    return build_writing_tool_catalog(
        dependencies=WritingToolDependencies(
            db,
            SqliteWritingSourceRepository(db),
            _memory_service(db, resource),
        ),
        skill_items=tuple(
            WritingSkillCatalog(
                Path(__file__).parents[1] / "skills"
            ).skill_items()
        ),
    )


async def _memory(
    db,
    resource,
    *,
    content="红门只能用铜钥匙打开",
    book="book-a",
    state="active",
    operation_key="memory",
):
    return await _memory_service(db, resource).create_manual(
        book_id=book,
        operation_key=f"{operation_key}:{book}:{state}",
        text=content,
        metadata=memory_metadata(kind="canon"),
        state=state,
    )


class _Sink:
    async def emit(self, event):
        return None


async def _execute(
    db,
    resource,
    run_id,
    *,
    name="searchMemories",
    arguments=None,
    allowed=True,
):
    call = ToolCall(
        id="retrieval-call",
        name=name,
        arguments_json=json.dumps(
            {"query": "红门"} if arguments is None else arguments,
            ensure_ascii=False,
        ),
    )
    batch = await CoreToolExecutor(_catalog(db, resource)).execute_batch(
        ToolBatchRequest(
            run_id=run_id,
            calls=(call,),
            allowed_tool_names=frozenset({name}) if allowed else frozenset(),
            state=ExecutionState(run_id=run_id, domain={"bookId": "book-b"}),
        ),
        _Sink(),
    )
    return call, batch


@pytest.mark.parametrize("session_id", [None, 7])
async def test_component_memory_scope_lifecycle_and_persisted_evidence(
    db,
    memory_resource,
    session_id,
):
    run_id = await _run(db, session_id=session_id)
    accepted = await _memory(db, memory_resource)
    await _memory(
        db,
        memory_resource,
        content="红门属于另一本书",
        book="book-b",
    )
    await _memory(
        db,
        memory_resource,
        content="红门 pending",
        state="pending",
        operation_key="pending",
    )
    await _memory(
        db,
        memory_resource,
        content="红门 disabled",
        state="disabled",
        operation_key="disabled",
    )

    call, batch = await _execute(db, memory_resource, run_id)
    result, = batch.results
    assert result.error is None
    assert result.planning_disposition is ToolPlanningDisposition.REPLAN
    hit, = json.loads(result.content)["hits"]
    assert hit["id"] == accepted["id"]
    assert hit["source"].startswith("mem0/")
    assert hit["version"] == accepted["version"]
    assert hit["untrusted"] is True
    assert hit["metadata"]["evidenceId"].startswith("mem0:")

    store = RunEvidenceStore()
    receipt, = store.record_batch((call,), batch)
    checkpoint = AgentExecutionCheckpoint(
        run_id=run_id,
        next_round=1,
        round_limit=4,
        messages=(AgentMessage(role="user", content="检索"),),
        evidence_state=store.checkpoint_mapping(),
    )
    repository = SqliteRunRepository(db, owner_id="retrieval-test")
    await repository.commit(run_id, RunCommit(execution_checkpoint=checkpoint))
    restored = RunEvidenceStore.from_checkpoint_mapping(
        (await repository.get(run_id)).execution_checkpoint.evidence_state,
    )
    assert json.loads(restored.get(receipt.evidence_id).content)["hits"][0] == hit

    service = _memory_service(db, memory_resource)
    updated = await service.update(
        book_id="book-a",
        item_id=accepted["id"],
        version=accepted["version"],
        operation_key="edit",
        text="红门改用银钥匙",
        metadata=memory_metadata(kind="canon"),
    )
    _, refreshed = await _execute(db, memory_resource, run_id)
    assert json.loads(refreshed.results[0].content)["hits"][0]["content"] == "红门改用银钥匙"
    await service.set_state(
        book_id="book-a",
        item_id=updated["id"],
        version=updated["version"],
        operation_key="disable",
        state="disabled",
        reason="archived",
    )
    _, disabled = await _execute(db, memory_resource, run_id)
    assert json.loads(disabled.results[0].content) == {"hits": []}


@pytest.mark.parametrize("extra", [
    {"bookId": "book-b"},
    {"limit": 100},
    {"statuses": ["pending"]},
    {"scope": {"bookId": "book-b"}},
    {"run_id": "other"},
])
async def test_model_cannot_select_host_scope_or_budget(
    db,
    memory_resource,
    extra,
):
    _, batch = await _execute(
        db,
        memory_resource,
        await _run(db),
        arguments={"query": "红门", **extra},
    )
    assert batch.results[0].error is not None


async def test_direct_retriever_validates_scope_and_cancellation(
    db,
    memory_resource,
):
    retriever = WritingMemoryRetriever(
        db,
        _memory_service(db, memory_resource),
    )
    assert isinstance(retriever, Retriever)
    run_id = await _run(db)
    for request, code in (
        (RetrievalRequest("红门", 1), "retrieval_scope_unavailable"),
        (
            RetrievalRequest("红门", 1, run_id="missing"),
            "retrieval_scope_unavailable",
        ),
        (
            RetrievalRequest(
                "红门",
                1,
                run_id=run_id,
                scope={"bookId": "book-b"},
            ),
            "retrieval_access_denied",
        ),
        (
            RetrievalRequest("红门", 13, run_id=run_id),
            "retrieval_access_denied",
        ),
    ):
        with pytest.raises(RetrievalError) as error:
            await retriever.retrieve(request)
        assert error.value.code == code
    stopped = asyncio.Event()
    stopped.set()
    with pytest.raises(OperationCanceled):
        await retriever.retrieve(
            RetrievalRequest("红门", 1, run_id=run_id),
            stopped,
        )
    await db.execute(
        "UPDATE ai_agent_runs SET status = 'canceled' WHERE id = ?",
        [run_id],
    )
    with pytest.raises(RetrievalError, match="Active Run"):
        await retriever.retrieve(RetrievalRequest("红门", 1, run_id=run_id))


async def test_retrieval_failure_is_redacted_and_does_not_replan(
    db,
    memory_resource,
    monkeypatch,
):
    async def unavailable(*args, **kwargs):
        raise RuntimeError("private-database-path-and-secret")

    monkeypatch.setattr(MemoryApplicationService, "retrieve", unavailable)
    _, batch = await _execute(db, memory_resource, await _run(db))
    assert batch.results[0].error is not None
    assert "private-database-path-and-secret" not in batch.results[0].content
    assert batch.results[0].planning_disposition is ToolPlanningDisposition.KEEP_PLAN


async def test_missing_authoritative_book_binding_cannot_use_mutable_domain(
    db,
    memory_resource,
):
    await _memory(db, memory_resource)
    run_id = await SqliteRunRepository(db).create(RunCreateParams(
        session_id=None,
        prompt="unbound",
        mode="agent",
    ))
    _, batch = await _execute(db, memory_resource, run_id)
    assert batch.results[0].error == "retrieval_scope_unavailable"
    assert "铜钥匙" not in batch.results[0].content


async def test_method_retrieval_requires_host_intent_and_returns_revision(
    db,
    memory_resource,
):
    service = WritingMethodService(db)
    method = await service.create_method(
        name="测试红门悬念",
        description="逐层释放红门信息",
        method_type="technique",
        tags=["红门"],
        markdown="# 红门\n秘密不能立即揭晓。",
        metadata={"schemaVersion": 1},
    )
    revision = await service.publish_method(method["id"])
    ordinary_run = await _run(db)
    with pytest.raises(RetrievalError) as error:
        await WritingMethodRetriever(db).retrieve(
            RetrievalRequest("测试红门", 8, run_id=ordinary_run)
        )
    assert error.value.code == "retrieval_access_denied"
    _, denied = await _execute(
        db,
        memory_resource,
        ordinary_run,
        name="searchWritingMethods",
    )
    assert denied.results[0].error == "retrieval_access_denied"

    run_id = await _run(db, recommend=True)
    _, batch = await _execute(
        db,
        memory_resource,
        run_id,
        name="searchWritingMethods",
        arguments={"query": "红门"},
    )
    hits = json.loads(batch.results[0].content)["hits"]
    assert [hit["id"] for hit in hits] == [revision["id"]]
    assert hits[0]["version"] == revision["version_no"]
    assert hits[0]["metadata"]["contentDigest"] == revision["content_digest"]
    assert hits[0]["untrusted"] is True


def test_retrieval_catalog_exposes_only_query_and_existing_context_contract():
    catalog = _catalog(object(), None)
    for registration in catalog.registrations():
        if registration.schema.name not in {
            "searchMemories",
            "searchWritingMethods",
        }:
            continue
        assert set(registration.schema.parameters["properties"]) == {"query"}
        assert registration.schema.parameters["additionalProperties"] is False
        assert registration.data_contract.model_owned_paths == ("query",)
        assert registration.data_contract.host_derived_paths == ("run_id",)
        assert (
            "binding.bookId"
            in registration.context_contract.mandatory_context_keys
        )
