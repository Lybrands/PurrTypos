from __future__ import annotations

from pathlib import Path

import pytest

from application.writing_agent_profile import WritingAgentProfile
from application.request_mapping import to_writing_agent_request
from application.writing_method_service import WritingMethodService
from database.connection import DatabaseConnection
from domains.writing.context import WritingContextProvider, writing_context_claims
from domains.writing.contracts import WritingDomainContext
from domains.writing.method_resolution import (
    WRITING_METHODS_CONTEXT,
    build_writing_method_binding_snapshot,
    resolve_writing_methods,
)
from domains.writing.methods import WritingMethodConflictError
from purra.api import AgentExecutionCheckpoint
from purra.context_budget import allocate_context_budget
from purra.contracts import (
    AgentMessage,
    AgentRunRequest,
    MessageOrigin,
    MessageRole,
    ModelRequest,
    TaskContextRequest,
    TaskSpec,
)
from purra.evidence import CONTEXT_EVIDENCE_RECEIPTS_KEY, RunEvidenceStore
from purra.json_values import thaw_json_mapping
from routers.ai import _writing_chat_request_digest
from schemas.ai import ChatStreamRequest


BACKEND_DIR = Path(__file__).resolve().parent.parent


@pytest.fixture
async def db(tmp_path):
    connection = DatabaseConnection(tmp_path)
    await connection.init()
    await connection.execute(
        "INSERT INTO books (id, title) VALUES (?, ?)", ["book-1", "测试作品"]
    )
    try:
        yield connection
    finally:
        await connection.close()


async def _publish(service, *, name, method_type, markdown, tags=()):
    method = await service.create_method(
        name=name,
        description=f"{name}说明",
        method_type=method_type,
        tags=list(tags),
        markdown=markdown,
        metadata={"schemaVersion": 1},
    )
    return method, await service.publish_method(method["id"])


async def _bound_snapshot(db, *, force=(), exclude=()):
    service = WritingMethodService(db)
    _primary, primary = await _publish(
        service,
        name="叙事主方法",
        method_type="primary",
        markdown="# 主方法\n保持人物动机连续。",
    )
    _technique, technique = await _publish(
        service,
        name="悬念递进",
        method_type="technique",
        markdown="# 悬念递进\n逐层释放信息。",
        tags=("悬念",),
    )
    scheme = await service.create_scheme(
        name="测试方案",
        description="",
        member_revision_ids=[primary["id"], technique["id"]],
    )
    scheme_revision = await service.publish_scheme(scheme["id"])
    await service.bind_book_revision(
        book_id="book-1",
        binding_type="scheme",
        revision_id=scheme_revision["id"],
    )
    snapshot = await service.resolve_book_binding_snapshot(
        "book-1",
        force_revision_ids=force,
        exclude_revision_ids=exclude,
    )
    return service, primary, technique, snapshot


async def test_snapshot_expands_scheme_in_order_and_rejects_unbound_overrides(db):
    _service, primary, technique, snapshot = await _bound_snapshot(db)

    assert [item["revisionId"] for item in snapshot["catalog"]] == [
        primary["id"], technique["id"]
    ]
    assert snapshot["methods"][1]["schemeRevisionId"]
    assert len(snapshot["bindingSnapshotDigest"]) == 64

    with pytest.raises(WritingMethodConflictError, match="本书已经绑定"):
        await WritingMethodService(db).resolve_book_binding_snapshot(
            "book-1", force_revision_ids=["not-bound"]
        )
    with pytest.raises(WritingMethodConflictError, match="同时强制和排除"):
        build_writing_method_binding_snapshot(
            "book-1", (), force_revision_ids=["same"], exclude_revision_ids=["same"]
        )


async def test_same_method_with_two_bound_revisions_fails_closed(db):
    service = WritingMethodService(db)
    method, v1 = await _publish(
        service,
        name="同一方法",
        method_type="primary",
        markdown="# v1",
    )
    await service.update_method(
        method["id"],
        expected_draft_revision=method["draft_revision"],
        name=method["name"],
        description=method["description"],
        method_type=method["method_type"],
        tags=method["tags"],
        markdown="# v2",
        metadata=method["draft_metadata"],
    )
    v2 = await service.publish_method(method["id"])
    await service.bind_book_revision(
        book_id="book-1", binding_type="method", revision_id=v1["id"]
    )
    await service.bind_book_revision(
        book_id="book-1", binding_type="method", revision_id=v2["id"]
    )
    with pytest.raises(WritingMethodConflictError, match="多个版本"):
        await service.resolve_book_binding_snapshot("book-1")


def test_request_digest_includes_exact_turn_overrides():
    base = dict(
        messages=[{"role": "user", "content": "续写"}],
        apiKey="key",
        bookId="book-1",
    )
    forced = ChatStreamRequest(**base, writingMethodOverrides={
        "forceRevisionIds": ["revision-a"],
        "excludeRevisionIds": [],
    })
    excluded = ChatStreamRequest(**base, writingMethodOverrides={
        "forceRevisionIds": [],
        "excludeRevisionIds": ["revision-a"],
    })

    assert _writing_chat_request_digest(forced) != _writing_chat_request_digest(excluded)
    assert WritingDomainContext.from_core_context(
        to_writing_agent_request(forced, {"model": "model"}).domain_context
    ).writing_method_overrides == {
        "forceRevisionIds": ["revision-a"],
        "excludeRevisionIds": [],
    }


def test_only_structured_recommendation_request_opens_catalog_capability():
    ordinary = ChatStreamRequest(
        messages=[{"role": "user", "content": "继续写下一段"}],
        apiKey="key",
        bookId="book-1",
    )
    requested = ChatStreamRequest(
        messages=[{"role": "user", "content": "[写作方法推荐] 如何加强冲突"}],
        apiKey="key",
        bookId="book-1",
    )
    ordinary_context = WritingDomainContext.from_core_context(
        to_writing_agent_request(ordinary, {"model": "model"}).domain_context
    )
    requested_context = WritingDomainContext.from_core_context(
        to_writing_agent_request(requested, {"model": "model"}).domain_context
    )
    assert ordinary_context.writing_method_recommendation_requested is False
    assert requested_context.writing_method_recommendation_requested is True


async def test_task_context_selects_technique_and_emits_durable_receipts(db):
    _service, primary, technique, snapshot = await _bound_snapshot(db)
    request = AgentRunRequest(
        messages=(AgentMessage(role=MessageRole.USER, content="续写这一章"),),
        model=ModelRequest(provider="test", model="model"),
        domain_context=WritingDomainContext(
            book_id="book-1",
            writing_method_binding_snapshot=snapshot,
        ).to_core_context(),
        context_window=32_000,
    )
    budget = allocate_context_budget(
        window_tokens=32_000,
        output_reserve_tokens=4_096,
        claims=writing_context_claims(request),
    )
    task = TaskContextRequest(task_spec=TaskSpec(
        goal="让悬念逐步升级",
        instruction="使用悬念递进技法续写",
    ))

    bundle = await WritingContextProvider().build_task_context(request, budget, task)
    method_block = next(block for block in bundle.blocks if block.name == WRITING_METHODS_CONTEXT)
    assert method_block.untrusted is True
    assert primary["id"] in method_block.content
    assert technique["id"] in method_block.content
    receipt_rows = method_block.host_metadata[CONTEXT_EVIDENCE_RECEIPTS_KEY]
    assert [item["itemId"] for item in receipt_rows] == [primary["id"], technique["id"]]
    assert receipt_rows[1]["reason"] == "task_selected"

    store = RunEvidenceStore()
    store.record_context_messages((AgentMessage(
        role=MessageRole.DEVELOPER,
        content=method_block.content,
        origin=MessageOrigin.HOST_CONTEXT,
        attributes={"context_name": WRITING_METHODS_CONTEXT},
        host_metadata=method_block.host_metadata,
    ),))
    checkpoint = AgentExecutionCheckpoint(
        run_id="run-1",
        next_round=1,
        messages=(AgentMessage(role=MessageRole.USER, content="test"),),
        round_limit=4,
        evidence_state=store.checkpoint_mapping(),
    )
    restored = AgentExecutionCheckpoint.from_mapping(checkpoint.to_mapping())
    restored_store = RunEvidenceStore.from_checkpoint_mapping(
        thaw_json_mapping(restored.evidence_state)
    )
    receipts = [receipt.to_mapping() for receipt in restored_store.context_receipts()]
    binding = WritingAgentProfile(db, skills_dir=BACKEND_DIR / "skills").run_binding_attributes(request)
    assert receipts[1]["metadata"]["reason"] == "task_selected"
    assert "metadata" not in receipts[1]["metadata"]
    assert binding["writingMethodBindingSnapshot"]["bindingSnapshotDigest"] == snapshot["bindingSnapshotDigest"]
    assert [item["itemId"] for item in receipts] == [
        primary["id"], technique["id"]
    ]


async def test_forced_method_fails_closed_when_its_full_body_does_not_fit(db):
    _service, _primary, technique, snapshot = await _bound_snapshot(
        db, force=()
    )
    forced = {**snapshot, "forceRevisionIds": [technique["id"]]}
    with pytest.raises(WritingMethodConflictError, match="超出写作方法上下文预算"):
        resolve_writing_methods(forced, task=None, token_budget=1)


async def test_profile_freezes_exact_stack_before_later_publication_and_upgrade(db):
    service, primary_v1, _technique, _snapshot = await _bound_snapshot(db)
    profile = WritingAgentProfile(db, skills_dir=BACKEND_DIR / "skills")
    request = AgentRunRequest(
        messages=(AgentMessage(role=MessageRole.USER, content="续写"),),
        model=ModelRequest(provider="test", model="model"),
        domain_context=WritingDomainContext(book_id="book-1").to_core_context(),
        context_window=32_000,
    )
    prepared = await profile.prepare_request(request)
    frozen = profile.run_binding_attributes(prepared)["writingMethodBindingSnapshot"]

    method = await service.get_method(primary_v1["method_id"])
    await service.update_method(
        method["id"],
        expected_draft_revision=method["draft_revision"],
        name=method["name"],
        description=method["description"],
        method_type=method["method_type"],
        tags=method["tags"],
        markdown="# 主方法 v2",
        metadata=method["draft_metadata"],
    )
    await service.publish_method(method["id"])

    assert frozen["methods"][0]["revisionId"] == primary_v1["id"]
    assert frozen["bindingSnapshotDigest"] == profile.run_binding_attributes(prepared)[
        "writingMethodBindingSnapshot"
    ]["bindingSnapshotDigest"]
