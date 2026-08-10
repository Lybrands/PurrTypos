from __future__ import annotations

import json
from pathlib import Path

import pytest
import pytest_asyncio

from database.connection import DatabaseConnection
from dependencies import set_db

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def temp_db(tmp_path: Path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    set_db(db)
    try:
        yield db
    finally:
        await db.close()


async def test_diagnostics_marks_budget_overflow_or_rejected_tools_as_failure(
    temp_db: DatabaseConnection,
):
    from purra.evaluation import evaluate_agent_run
    from infrastructure.persistence.run_store import (
        append_trace,
        create_run,
        get_run,
        get_run_events,
    )

    run_id = await create_run(temp_db, session_id=1, prompt="p", mode="agent")
    await append_trace(temp_db, run_id, stage="planner", outcome="model_plan")
    await append_trace(temp_db, run_id, stage="context_budget", outcome="overflow")
    await append_trace(temp_db, run_id, stage="tool_round", outcome="rejected")
    await append_trace(temp_db, run_id, stage="terminal", outcome="failed")
    await temp_db.execute("UPDATE ai_agent_runs SET status = 'failed' WHERE id = ?", [run_id])

    report = evaluate_agent_run(
        await get_run(temp_db, run_id) or {},
        await get_run_events(temp_db, run_id),
    )

    assert report["verdict"] == "fail"
    failed_checks = {check["name"] for check in report["checks"] if check["status"] == "fail"}
    assert {"contextSafety", "toolGovernance"}.issubset(failed_checks)


async def test_diagnostics_marks_missing_required_tool_call_as_failure(
    temp_db: DatabaseConnection,
):
    from purra.evaluation import evaluate_agent_run
    from infrastructure.persistence.run_store import (
        append_trace,
        create_run,
        get_run,
        get_run_events,
    )

    run_id = await create_run(temp_db, session_id=1, prompt="p", mode="agent")
    await append_trace(temp_db, run_id, stage="planner", outcome="model_plan")
    await append_trace(temp_db, run_id, stage="context_budget", outcome="within_budget")
    await append_trace(temp_db, run_id, stage="tool_round", outcome="missing_required_call")
    await append_trace(temp_db, run_id, stage="terminal", outcome="failed")
    await temp_db.execute("UPDATE ai_agent_runs SET status = 'failed' WHERE id = ?", [run_id])

    report = evaluate_agent_run(
        await get_run(temp_db, run_id) or {},
        await get_run_events(temp_db, run_id),
    )

    assert report["verdict"] == "fail"
    assert report["metrics"]["missingRequiredToolCalls"] == 1
    governance = next(check for check in report["checks"] if check["name"] == "toolGovernance")
    assert governance["status"] == "fail"


async def test_diagnostics_rejects_historical_silent_planner_fallback(
    temp_db: DatabaseConnection,
):
    from purra.evaluation import evaluate_agent_run
    from infrastructure.persistence.run_store import (
        append_trace,
        create_run,
        get_run,
        get_run_events,
    )

    run_id = await create_run(temp_db, session_id=1, prompt="p", mode="agent")
    await append_trace(temp_db, run_id, stage="planner", outcome="fallback_after_error")
    await append_trace(temp_db, run_id, stage="context_budget", outcome="within_budget")
    await append_trace(temp_db, run_id, stage="terminal", outcome="done")
    await temp_db.execute("UPDATE ai_agent_runs SET status = 'done' WHERE id = ?", [run_id])

    report = evaluate_agent_run(
        await get_run(temp_db, run_id) or {},
        await get_run_events(temp_db, run_id),
    )

    assert report["verdict"] == "fail"
    planner = next(check for check in report["checks"] if check["name"] == "plannerHealth")
    assert planner == {
        "name": "plannerHealth",
        "status": "fail",
        "detail": "fallback_after_error",
    }


async def test_diagnostics_endpoint_includes_stability_and_artifact_metrics(
    temp_db: DatabaseConnection,
):
    from infrastructure.persistence.run_store import (
        append_event,
        append_trace,
        create_run,
    )
    from routers.ai import get_agent_run_diagnostics

    run_id = await create_run(
        temp_db,
        session_id=1,
        prompt="p",
        mode="agent",
    )
    await append_trace(temp_db, run_id, stage="planner", outcome="model_plan")
    await append_trace(
        temp_db,
        run_id,
        stage="context_budget",
        outcome="within_budget",
    )
    await append_event(
        temp_db,
        run_id,
        "tool.calls_started",
        {"calls": [{"id": "call-1", "name": "readA"}]},
    )
    await append_event(
        temp_db,
        run_id,
        "tool.results",
        {"results": [{
            "tool_call_id": "call-1",
            "tool_name": "readA",
            "content": "not included in metrics",
        }]},
    )
    await append_event(temp_db, run_id, "run.completed", {"status": "done"})
    await temp_db.execute(
        "UPDATE ai_agent_runs SET status = 'done' WHERE id = ?",
        [run_id],
    )

    response = await get_agent_run_diagnostics(run_id)

    assert response["success"] is True
    assert response["data"]["stability"]["verdict"] == "pass"
    assert response["data"]["stability"]["metrics"]["toolCalls"] == 1
    assert response["data"]["recovery"] == {
        "summary": {
            "decisionCount": 0,
            "allowedCount": 0,
            "deniedCount": 0,
            "safetyProtectedCount": 0,
            "causes": {},
            "allowedActions": {},
            "deniedReasons": {},
        },
        "decisions": [],
    }
    assert response["data"]["failureClassification"] == {
        "verdict": "pass",
        "primaryFinding": None,
        "findings": [],
        "summary": {"findingCount": 0, "categoryCounts": {}},
    }
    assert response["data"]["artifacts"] == {
        "artifactCount": 0,
        "openArtifacts": 0,
        "finalizedArtifacts": 0,
        "abortedArtifacts": 0,
        "batchCount": 0,
        "committedItemCount": 0,
        "expectedItemCount": 0,
        "completionRate": None,
        "artifacts": [],
    }
    maintenance = response["data"]["artifactMaintenance"]
    assert maintenance["scopeRunId"] == run_id
    assert maintenance["workItemCount"] == 0
    assert maintenance["artifactCount"] == 0
    assert maintenance["claimCount"] == 0
    assert maintenance["reclaimableClaims"] == 0
    assert maintenance["consistencyIssues"] == 0
    assert maintenance["requiresAttention"] is False


async def test_diagnostics_aggregates_durable_child_run_evidence(
    temp_db: DatabaseConnection,
):
    from infrastructure.persistence.run_store import (
        append_event,
        append_trace,
        create_run,
    )
    from routers.ai import get_agent_run_diagnostics

    root_run_id = await create_run(
        temp_db,
        session_id=1,
        prompt="连续创作后续场景",
        mode="agent",
    )
    await append_trace(
        temp_db,
        root_run_id,
        stage="planning",
        outcome="host_plan",
    )
    await append_event(
        temp_db,
        root_run_id,
        "long_task.dispatched",
        {"taskId": "task-workflow"},
    )
    await append_event(
        temp_db,
        root_run_id,
        "run.completed",
        {"status": "done"},
    )
    await temp_db.execute(
        "UPDATE ai_agent_runs SET status = 'done' WHERE id = ?",
        [root_run_id],
    )
    await temp_db.execute(
        "INSERT INTO ai_agent_long_tasks "
        "(id, work_item_id, namespace, kind, owner_id, created_by_run_id, "
        "status, total_units, completed_units) VALUES "
        "('task-workflow', 'work-1', 'test', 'screenplay_draft_generation', "
        "'project-1', ?, 'completed', 2, 2)",
        [root_run_id],
    )

    child_run_id = await create_run(
        temp_db,
        session_id=1,
        prompt="创作第一批",
        mode="agent",
        parent_run_id=root_run_id,
        root_run_id=root_run_id,
        agent_role="screenplay_batch_writer",
        run_depth=1,
    )
    await append_trace(
        temp_db,
        child_run_id,
        stage="planner",
        outcome="model_plan",
    )
    await append_trace(
        temp_db,
        child_run_id,
        stage="context_budget",
        outcome="within_budget",
    )
    await append_trace(
        temp_db,
        child_run_id,
        stage="model_round",
        outcome="completed",
    )
    await append_event(
        temp_db,
        child_run_id,
        "tool.calls_started",
        {"calls": [{"id": "call-child", "name": "readSceneList"}]},
    )
    await append_event(
        temp_db,
        child_run_id,
        "tool.results",
        {"results": [{
            "tool_call_id": "call-child",
            "tool_name": "readSceneList",
        }]},
    )
    await append_event(
        temp_db,
        child_run_id,
        "run.completed",
        {"status": "done"},
    )
    await temp_db.execute(
        "UPDATE ai_agent_runs SET status = 'done' WHERE id = ?",
        [child_run_id],
    )

    response = await get_agent_run_diagnostics(root_run_id)

    assert response["success"] is True
    report = response["data"]
    assert report["metrics"]["modelRounds"] == 1
    assert report["stability"]["metrics"]["toolCalls"] == 1
    assert report["workflow"]["rootRunId"] == root_run_id
    assert report["workflow"]["status"] == "completed"
    assert report["workflow"]["runCount"] == 2
    assert report["workflow"]["childRunCount"] == 1
    assert report["workflow"]["childRuns"][0]["runId"] == child_run_id


async def test_manual_artifact_maintenance_only_reaps_safe_claims(
    temp_db: DatabaseConnection,
):
    from infrastructure.persistence.run_store import create_run
    from routers.ai import maintain_agent_artifacts

    run_id = await create_run(
        temp_db,
        session_id=None,
        prompt="expired Artifact lease",
        mode="agent",
    )
    await temp_db.execute(
        "INSERT INTO ai_agent_work_items "
        "(id, namespace, kind, owner_id, created_by_run_id) "
        "VALUES ('manual-item', 'test', 'draft', 'owner', ?)",
        [run_id],
    )
    await temp_db.execute(
        "INSERT INTO ai_agent_work_item_runs "
        "(work_item_id, run_id, relation, work_item_revision) "
        "VALUES ('manual-item', ?, 'created', 1)",
        [run_id],
    )
    await temp_db.execute(
        "INSERT INTO ai_agent_artifacts "
        "(id, namespace, kind, owner_id, run_id, artifact_scope, "
        "work_item_id, created_by_run_id) VALUES "
        "('manual-artifact', 'test', 'draft', 'owner', ?, 'work_item', "
        "'manual-item', ?)",
        [run_id, run_id],
    )
    await temp_db.execute(
        "INSERT INTO ai_agent_artifact_claims "
        "(artifact_id, work_item_id, run_id, claim_token, "
        "acquired_revision, expires_at_ms) VALUES "
        "('manual-artifact', 'manual-item', ?, 'secret-token', 1, 1)",
        [run_id],
    )

    response = await maintain_agent_artifacts()

    assert response["success"] is True
    assert response["data"]["report"]["expiredClaimsReleased"] == 1
    assert response["data"]["report"]["releasedClaims"] == 1
    assert response["data"]["report"]["purgedArtifacts"] == 0
    assert response["data"]["report"]["purgedWorkItems"] == 0
    assert response["data"]["snapshot"]["claimCount"] == 0
    assert await temp_db.fetch_one(
        "SELECT id FROM ai_agent_artifacts WHERE id = 'manual-artifact'"
    ) == {"id": "manual-artifact"}
    wire = json.dumps(response, ensure_ascii=False)
    assert "secret-token" not in wire


async def test_diagnostics_exposes_work_item_lineage_without_claim_secret(
    temp_db: DatabaseConnection,
):
    from purra.artifacts import ArtifactCreateCommand
    from infrastructure.persistence.run_store import create_run
    from infrastructure.persistence.sqlite_work_item_artifact_lifecycle import (
        SqliteWorkItemArtifactLifecycle,
    )
    from routers.ai import get_agent_run_diagnostics

    run_id = await create_run(
        temp_db,
        session_id=None,
        prompt="inspect Artifact lineage",
        mode="agent",
    )
    started = await SqliteWorkItemArtifactLifecycle(
        temp_db,
        token_factory=lambda: "diagnostic-secret-token",
    ).begin(ArtifactCreateCommand(
        namespace="purrtypos.screenplay",
        kind="scene_list_batches",
        owner_id="diagnostic-project",
        run_id=run_id,
    ))

    response = await get_agent_run_diagnostics(run_id)

    assert response["success"] is True
    artifact = response["data"]["artifacts"]["artifacts"][0]
    assert artifact["scope"] == "work_item"
    assert artifact["workItemId"] == started.work_item.id
    assert artifact["workItemStatus"] == "open"
    assert artifact["runRelation"] == "created"
    maintenance = response["data"]["artifactMaintenance"]
    assert maintenance["workItemCount"] == 1
    assert maintenance["artifactCount"] == 1
    assert maintenance["activeClaims"] == 1
    assert maintenance["reclaimableClaims"] == 0
    wire = json.dumps(response, ensure_ascii=False)
    assert "diagnostic-secret-token" not in wire
    assert "claimToken" not in wire
