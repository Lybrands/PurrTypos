from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from agent_core.artifacts import (
    ArtifactAppendCommand,
    ArtifactClaimLeaseCommand,
    ArtifactCreateCommand,
    ArtifactMutationLease,
    ArtifactScope,
    ArtifactWriteClaimCommand,
)
from agent_core.context_budget import allocate_context_budget
from agent_core.contracts import (
    AgentMessage,
    AgentRunRequest,
    MessageRole,
    ModelRequest,
    PlanningCapabilities,
    TaskContextRequest,
    TaskSpec,
)
from agent_core.work_items import (
    WorkItemCreateCommand,
    WorkItemRunLinkCommand,
    WorkItemRunRelation,
)
from application.artifact_continuity import ArtifactContinuityCoordinator
from database.connection import DatabaseConnection
from domains.screenplay.artifact_projection import SCREENPLAY_ARTIFACT_CONTEXT
from domains.screenplay.context import (
    SCREENPLAY_PROJECT_CONTEXT,
    ScreenplayContextProvider,
)
from domains.screenplay.contracts import ScreenplayDomainContext
from domains.screenplay.planning import ScreenplayPlanningPolicy
from infrastructure.persistence.sqlite_artifact_claim_repository import (
    SqliteArtifactClaimRepository,
)
from infrastructure.persistence.sqlite_artifact_repository import (
    SqliteArtifactRepository,
)
from infrastructure.persistence.sqlite_artifact_continuity_query import (
    SqliteArtifactContinuityQuery,
)
from infrastructure.persistence.sqlite_work_item_repository import (
    SqliteWorkItemRepository,
)


@pytest_asyncio.fixture
async def screenplay_continuity(tmp_path: Path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    try:
        await db.execute(
            "INSERT INTO screenplay_projects "
            "(id, title, active_stage) VALUES (?, ?, ?)",
            ["project-1", "测试剧本", "scenes"],
        )
        await db.execute(
            "INSERT INTO ai_sessions "
            "(id, title, scope, screenplay_project_id) VALUES (?, ?, ?, ?)",
            [1, "当前会话", "screenplay", "project-1"],
        )
        await db.execute(
            "INSERT INTO ai_sessions "
            "(id, title, scope, screenplay_project_id) VALUES (?, ?, ?, ?)",
            [2, "其他会话", "screenplay", "project-1"],
        )
        for run_id, session_id in (
            ("run-origin", 1),
            ("run-current", 1),
            ("run-reference", 1),
            ("run-holder", 1),
            ("run-other-session", 2),
        ):
            await db.execute(
                "INSERT INTO ai_agent_runs (id, session_id, prompt) "
                "VALUES (?, ?, ?)",
                [run_id, session_id, "test"],
            )
        work_items = SqliteWorkItemRepository(db)
        item = await work_items.create(
            "work-item-scenes",
            WorkItemCreateCommand(
                namespace="purrtypos.screenplay",
                kind="scene_list",
                owner_id="project-1",
                created_by_run_id="run-origin",
                metadata={"title": "未完成场景表"},
            ),
        )
        artifacts = SqliteArtifactRepository(db)
        artifact = await artifacts.create(
            "artifact-scenes",
            ArtifactCreateCommand(
                namespace="purrtypos.screenplay",
                kind="scene_list_batches",
                owner_id="project-1",
                run_id="run-origin",
                scope=ArtifactScope.WORK_ITEM,
                work_item_id=item.id,
                created_by_run_id="run-origin",
                expected_item_count=3,
                metadata={"title": "场景表", "expectedSceneIds": ["s1", "s2", "s3"]},
            ),
        )
        claims = SqliteArtifactClaimRepository(db)
        claim = await claims.acquire(ArtifactWriteClaimCommand(
            artifact_id=artifact.id,
            work_item_id=item.id,
            run_id="run-origin",
            expected_revision=artifact.revision,
            lease_duration_ms=300_000,
        ))
        await artifacts.append(ArtifactAppendCommand(
            artifact_id=artifact.id,
            expected_revision=artifact.revision,
            sequence=artifact.next_sequence,
            batch_id="batch-scenes-1",
            idempotency_key="batch-scenes-1",
            items=({"id": "s1", "order": 1, "summary": "开场"},),
            coverage_keys=("scene:1",),
            write_lease=ArtifactMutationLease(
                run_id="run-origin",
                claim_token=claim.claim_token,
            ),
        ))
        await claims.release(ArtifactClaimLeaseCommand(
            artifact_id=artifact.id,
            run_id="run-origin",
            claim_token=claim.claim_token,
        ))
        artifact = await artifacts.load(artifact.id)
        assert artifact is not None
        yield db, item, artifact
    finally:
        await db.close()


def _request(*, session_id: int = 1) -> AgentRunRequest:
    return AgentRunRequest(
        messages=(AgentMessage(
            role=MessageRole.USER,
            content="继续完成刚才的场景表",
        ),),
        model=ModelRequest(provider="openai", model="test"),
        domain_context=ScreenplayDomainContext(
            project_id="project-1",
            requested_stage="scenes",
        ).to_core_context(),
        session_id=session_id,
        context_window=128_000,
        tools_enabled=True,
    )


def test_continuation_task_waives_only_authenticated_completed_dependencies():
    available = frozenset({
        "beginSceneListArtifact",
        "appendSceneListBatch",
        "finalizeSceneListProposal",
    })
    guidance = {
        "appendSceneListBatch": {
            "requires": ["beginSceneListArtifact"],
        },
        "finalizeSceneListProposal": {
            "requires": ["appendSceneListBatch"],
        },
    }
    facts = {"artifactContinuity": {"candidates": [{
        "artifactId": "artifact-scenes",
        "workItemId": "work-item-scenes",
        "kind": "scene_list_batches",
        "committedItemCount": 3,
        "expectedItemCount": 3,
    }]}}
    capabilities = PlanningCapabilities(
        available_tool_names=available,
        host_planning_facts=facts,
        tool_guidance=guidance,
    )
    constraints = ScreenplayPlanningPolicy().planning_constraints_for_task(
        _request(),
        capabilities,
        TaskSpec(
            goal="完成场景表",
            target={"artifactContinuity": {
                "action": "continue",
                "artifactId": "artifact-scenes",
                "workItemId": "work-item-scenes",
            }},
        ),
    )

    assert constraints.satisfied_tool_dependency_edges == frozenset({
        ("appendSceneListBatch", "beginSceneListArtifact"),
        ("finalizeSceneListProposal", "appendSceneListBatch"),
    })

    unrelated = ScreenplayPlanningPolicy().planning_constraints_for_task(
        _request(),
        capabilities,
        TaskSpec(
            goal="回答新问题",
            target={"artifactContinuity": {"action": "ignore"}},
        ),
    )
    assert unrelated.satisfied_tool_dependency_edges == frozenset()


def _provider(db, *, claims=None) -> ScreenplayContextProvider:
    claim_repository = claims or SqliteArtifactClaimRepository(db)
    return ScreenplayContextProvider(
        db,
        artifact_continuity=ArtifactContinuityCoordinator(
            query=SqliteArtifactContinuityQuery(db),
            work_items=SqliteWorkItemRepository(db),
            claims=claim_repository,
        ),
    )


def _task(
    *,
    action: str,
    run_id: str,
) -> TaskContextRequest:
    selection = {"action": action}
    if action != "ignore":
        selection.update({
            "artifactId": "artifact-scenes",
            "workItemId": "work-item-scenes",
        })
    return TaskContextRequest(
        task_spec=TaskSpec(
            goal="处理当前请求",
            operation="write",
            instruction="按用户意图处理",
            deliverable="场景表",
            target={"artifactContinuity": selection},
        ),
        run_id=run_id,
    )


async def _budget(provider, request, task=None):
    claims = list(await provider.describe_context_demands(request))
    if task is not None:
        claims.extend(await provider.describe_task_context_demands(
            request,
            task,
        ))
    return allocate_context_budget(
        window_tokens=128_000,
        output_reserve_tokens=8_000,
        safety_reserve_tokens=4_000,
        runtime_reserve_tokens=4_000,
        minimum_message_tokens=2_000,
        claims=tuple(claims),
    )


@pytest.mark.asyncio
async def test_planning_exposes_only_same_session_stage_candidates(
    screenplay_continuity,
) -> None:
    db, _, _ = screenplay_continuity
    provider = _provider(db)
    request = _request()

    planning = await provider.build_planning_context(
        request,
        await _budget(provider, request),
    )

    facts = planning.diagnostics["hostPlanningFacts"]
    candidates = facts["artifactContinuity"]["candidates"]
    assert planning.blocks == ()
    assert [(item["artifactId"], item["workItemId"]) for item in candidates] == [
        ("artifact-scenes", "work-item-scenes")
    ]
    assert candidates[0]["candidateOrdinal"] == 1
    assert "untrustedLabel" not in candidates[0]

    other_session = _request(session_id=2)
    other_planning = await provider.build_planning_context(
        other_session,
        await _budget(provider, other_session),
    )
    assert "artifactContinuity" not in other_planning.diagnostics[
        "hostPlanningFacts"
    ]


@pytest.mark.asyncio
async def test_ignore_does_not_link_or_claim_unfinished_artifact(
    screenplay_continuity,
) -> None:
    db, item, _ = screenplay_continuity
    provider = _provider(db)
    request = _request()
    task = _task(action="ignore", run_id="run-current")

    assert await provider.describe_task_context_demands(request, task) == ()
    bundle = await provider.build_task_context(
        request,
        await _budget(provider, request, task),
        task,
    )

    assert [block.name for block in bundle.blocks] == [
        "screenplay_policy",
        SCREENPLAY_PROJECT_CONTEXT,
    ]
    links = await SqliteWorkItemRepository(db).list_run_links(item.id)
    assert {link.run_id for link in links} == {"run-origin"}
    assert await SqliteArtifactClaimRepository(db).load_active(
        "artifact-scenes"
    ) is None


@pytest.mark.asyncio
async def test_continue_links_claims_and_injects_bounded_projection(
    screenplay_continuity,
) -> None:
    db, item, _ = screenplay_continuity
    claims = SqliteArtifactClaimRepository(db)
    provider = _provider(db, claims=claims)
    request = _request()
    task = _task(action="continue", run_id="run-current")
    task_claims = await provider.describe_task_context_demands(request, task)

    assert [claim.name for claim in task_claims] == [
        SCREENPLAY_ARTIFACT_CONTEXT
    ]
    bundle = await provider.build_task_context(
        request,
        await _budget(provider, request, task),
        task,
    )

    projection = next(
        block for block in bundle.blocks
        if block.name == SCREENPLAY_ARTIFACT_CONTEXT
    )
    assert "只从 nextSequence 继续" in projection.content
    assert '"id":"s1"' in projection.content
    assert projection.token_count <= task_claims[0].desired_tokens
    links = await SqliteWorkItemRepository(db).list_run_links(item.id)
    assert {
        (link.run_id, link.relation.value) for link in links
    } == {
        ("run-origin", "created"),
        ("run-current", "continuation"),
    }
    active = await claims.load_active("artifact-scenes")
    assert active is not None
    assert active.run_id == "run-current"


@pytest.mark.asyncio
async def test_reference_links_read_only_without_write_claim(
    screenplay_continuity,
) -> None:
    db, item, _ = screenplay_continuity
    claims = SqliteArtifactClaimRepository(db)
    provider = _provider(db, claims=claims)
    request = _request()
    task = _task(action="reference", run_id="run-reference")

    bundle = await provider.build_task_context(
        request,
        await _budget(provider, request, task),
        task,
    )

    projection = next(
        block for block in bundle.blocks
        if block.name == SCREENPLAY_ARTIFACT_CONTEXT
    )
    assert "不得修改或接续" in projection.content
    links = await SqliteWorkItemRepository(db).list_run_links(item.id)
    assert {
        (link.run_id, link.relation.value) for link in links
    } == {
        ("run-origin", "created"),
        ("run-reference", "reference"),
    }
    assert await claims.load_active("artifact-scenes") is None


@pytest.mark.asyncio
async def test_competing_writer_becomes_safe_model_visible_unavailable_state(
    screenplay_continuity,
) -> None:
    db, item, artifact = screenplay_continuity
    work_items = SqliteWorkItemRepository(db)
    await work_items.link_run(WorkItemRunLinkCommand(
        work_item_id=item.id,
        run_id="run-holder",
        relation=WorkItemRunRelation.CONTINUATION,
        expected_revision=item.revision,
    ))
    claims = SqliteArtifactClaimRepository(db)
    await claims.acquire(ArtifactWriteClaimCommand(
        artifact_id=artifact.id,
        work_item_id=item.id,
        run_id="run-holder",
        expected_revision=artifact.revision,
        lease_duration_ms=300_000,
    ))
    provider = _provider(db, claims=claims)
    request = _request()
    task = _task(action="continue", run_id="run-current")

    bundle = await provider.build_task_context(
        request,
        await _budget(provider, request, task),
        task,
    )

    unavailable = next(
        block for block in bundle.blocks
        if block.name == SCREENPLAY_ARTIFACT_CONTEXT
    )
    assert unavailable.untrusted is False
    assert "不得调用工具修改或接续" in unavailable.content
    assert bundle.diagnostics["artifactContinuity"] == {
        "action": "continue",
        "outcome": "unavailable",
        "reasonCode": "artifact_claim_conflict",
        "writeClaimAcquired": False,
    }
    active = await claims.load_active(artifact.id)
    assert active is not None
    assert active.run_id == "run-holder"
