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
    from purra.observability import evaluate_agent_run
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
    from purra.observability import evaluate_agent_run
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
    from purra.observability import evaluate_agent_run
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
    assert maintenance["artifactCount"] == 0
    assert maintenance["claimCount"] == 0
    assert maintenance["reclaimableClaims"] == 0
    assert maintenance["consistencyIssues"] == 0
    assert maintenance["requiresAttention"] is False


async def test_planner_diagnostics_returns_exact_model_content_without_reasoning(
    temp_db: DatabaseConnection,
):
    from infrastructure.persistence.run_store import create_run
    from routers.ai import get_agent_run_planner_diagnostics

    run_id = await create_run(
        temp_db,
        session_id=1,
        prompt="分析小说",
        mode="novel_analysis",
    )
    invocation_id = "planner-invocation"
    stream_id = "planner-output"

    async def insert_event(
        sequence: int,
        kind: str,
        payload: dict,
        *,
        invocation: str = invocation_id,
        output_stream: str = stream_id,
        visibility: str = "private",
    ) -> None:
        await temp_db.execute(
            "INSERT INTO ai_agent_run_events "
            "(run_id, event_type, payload_json, event_id, invocation_id, "
            "output_stream_id, sequence, source, kind, channel, visibility, "
            "source_event_key) VALUES (?, ?, ?, ?, ?, ?, ?, 'provider', ?, "
            "'diagnostic', ?, ?)",
            [
                run_id,
                kind,
                json.dumps(payload, ensure_ascii=False),
                f"event-{sequence}",
                invocation,
                output_stream,
                sequence,
                kind,
                visibility,
                f"source-{sequence}",
            ],
        )

    await insert_event(1, "stream.opened", {
        "outputProtocol": "purra.planning-stream/v1",
        "planningScope": {
            "runId": run_id,
            "operationId": "planning-operation",
            "revision": 0,
        },
        "planningAttempt": 0,
        "model": "planner-model",
    })
    await insert_event(2, "provider.delta_batch", {
        "entries": [{
            "sourceChunkIndex": 1,
            "sourcePartIndex": 1,
            "kind": "provider.reasoning_delta",
            "payload": {"delta": "private chain of thought"},
        }, {
            "sourceChunkIndex": 3,
            "sourcePartIndex": 0,
            "kind": "provider.content_delta",
            "payload": {"delta": "\n{\"v\":1,\"type\":\"plan\"}"},
        }],
    })
    await insert_event(3, "planning.progress", {
        "recordIndex": 1,
        "revision": 0,
        "attempt": 0,
        "text": "模型正在组织分析步骤",
        "sourceStart": 0,
        "sourceEnd": 37,
    }, visibility="public")
    await insert_event(4, "provider.delta_batch", {
        "entries": [{
            "sourceChunkIndex": 2,
            "sourcePartIndex": 0,
            "kind": "provider.content_delta",
            "payload": {"delta": "{\"v\":1,\"type\":\"progress\"}"},
        }],
    })
    await insert_event(5, "model.diagnostics", {
        "firstPublicProgressMs": 120,
        "planReceivedMs": 180,
        "gatewayStartedAtMs": 999,
    })
    await insert_event(6, "stream.committed", {"finishReason": "stop"})

    # A normal model stream in the same Run must not enter Planner diagnostics.
    await insert_event(
        7,
        "stream.opened",
        {"outputProtocol": "text", "model": "runtime-model"},
        invocation="runtime-invocation",
        output_stream="runtime-output",
    )
    await insert_event(
        8,
        "provider.delta_batch",
        {"entries": [{
            "sourceChunkIndex": 1,
            "sourcePartIndex": 0,
            "kind": "provider.content_delta",
            "payload": {"delta": "normal answer"},
        }]},
        invocation="runtime-invocation",
        output_stream="runtime-output",
    )

    response = await get_agent_run_planner_diagnostics(run_id)

    assert response["success"] is True
    outputs = response["data"]["outputs"]
    assert len(outputs) == 1
    output = outputs[0]
    assert output["model"] == "planner-model"
    assert output["status"] == "committed"
    assert output["finishReason"] == "stop"
    assert output["rawContent"] == (
        '{"v":1,"type":"progress"}\n'
        '{"v":1,"type":"plan"}'
    )
    assert output["contentDeltaCount"] == 2
    assert output["contentDeltaConflict"] is False
    assert output["progressRecords"][0]["text"] == "模型正在组织分析步骤"
    assert output["timing"] == {
        "firstPublicProgressMs": 120,
        "planReceivedMs": 180,
    }
    wire = json.dumps(response, ensure_ascii=False)
    assert "private chain of thought" not in wire
    assert "gatewayStartedAtMs" not in wire
    assert "normal answer" not in wire


async def test_model_input_diagnostics_returns_captured_provider_messages(
    temp_db: DatabaseConnection,
    monkeypatch,
):
    import config
    from infrastructure.persistence.run_store import create_run
    from routers.ai import get_agent_run_model_input_diagnostics

    monkeypatch.setattr(config, "DEV_DIAGNOSTICS_ENABLED", True)
    run_id = await create_run(
        temp_db,
        session_id=1,
        prompt="用户原始输入",
        mode="novel_analysis",
    )
    await temp_db.execute(
        "INSERT INTO ai_agent_run_events "
        "(run_id, event_type, payload_json) VALUES (?, ?, ?)",
        [
            run_id,
            "stream.opened",
            json.dumps({
                "planningScope": {"revision": 0},
                "planningAttempt": 0,
                "callParameters": [{
                    "provider": "openai",
                    "model": "model",
                    "inputMessages": [
                        {
                            "role": "system",
                            "content": "内置小说分析方法",
                            "apiKey": "must-not-leak",
                        },
                        {"role": "user", "content": "用户原始输入"},
                    ],
                }],
            }, ensure_ascii=False),
        ],
    )

    response = await get_agent_run_model_input_diagnostics(run_id)

    assert response["success"] is True
    calls = response["data"]["calls"]
    assert len(calls) == 1
    assert calls[0]["phase"] == "planning"
    assert calls[0]["captured"] is True
    assert calls[0]["messages"] == [
        {
            "role": "system",
            "content": "内置小说分析方法",
            "apiKey": "<redacted>",
        },
        {"role": "user", "content": "用户原始输入"},
    ]


async def test_diagnostics_aggregates_durable_task_run_evidence(
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
    await temp_db.execute(
        "INSERT INTO ai_agent_long_tasks "
        "(id, namespace, kind, owner_id, created_by_run_id, "
        "status, total_units, completed_units) VALUES "
        "('task-workflow', 'test', 'screenplay_draft_generation', "
        "'project-1', ?, 'completed', 2, 2)",
        [root_run_id],
    )
    await temp_db.execute(
        "INSERT INTO ai_agent_long_task_runs (task_id, run_id, relation) "
        "VALUES ('task-workflow', ?, 'created')",
        [root_run_id],
    )

    continuation_run_id = await create_run(
        temp_db,
        session_id=1,
        prompt="创作第一批",
        mode="agent",
    )
    await temp_db.execute(
        "INSERT INTO ai_agent_long_task_runs (task_id, run_id, relation) "
        "VALUES ('task-workflow', ?, 'continuation')",
        [continuation_run_id],
    )
    await append_trace(
        temp_db,
        continuation_run_id,
        stage="planner",
        outcome="model_plan",
    )
    await append_trace(
        temp_db,
        continuation_run_id,
        stage="context_budget",
        outcome="within_budget",
    )
    await append_trace(
        temp_db,
        continuation_run_id,
        stage="model_round",
        outcome="completed",
    )
    await append_event(
        temp_db,
        continuation_run_id,
        "tool.calls_started",
        {"calls": [{"id": "call-child", "name": "readSceneList"}]},
    )
    await append_event(
        temp_db,
        continuation_run_id,
        "tool.results",
        {"results": [{
            "tool_call_id": "call-child",
            "tool_name": "readSceneList",
        }]},
    )
    await append_event(
        temp_db,
        continuation_run_id,
        "run.completed",
        {"status": "done"},
    )
    await temp_db.execute(
        "UPDATE ai_agent_runs SET status = 'done' WHERE id = ?",
        [continuation_run_id],
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

    response = await get_agent_run_diagnostics(root_run_id)

    assert response["success"] is True
    report = response["data"]
    assert report["metrics"]["modelRounds"] == 1
    assert report["stability"]["metrics"]["toolCalls"] == 1
    assert report["workflow"]["rootRunId"] == root_run_id
    assert report["workflow"]["status"] == "completed"
    assert report["workflow"]["runCount"] == 2
    assert report["workflow"]["relatedRunCount"] == 1
    assert report["workflow"]["runBindings"][0]["runId"] == continuation_run_id
    assert report["workflow"]["runBindings"][0]["relation"] == "continuation"


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
        "INSERT INTO ai_agent_artifacts "
        "(id, namespace, kind, owner_id, owner_ref_kind, owner_ref_id, "
        "created_by_run_id) VALUES "
        "('manual-artifact', 'test', 'draft', 'owner', 'agent_run', ?, ?)",
        [run_id, run_id],
    )
    await temp_db.execute(
        "INSERT INTO ai_agent_artifact_claims "
        "(artifact_id, run_id, claim_token, "
        "acquired_revision, expires_at_ms) VALUES "
        "('manual-artifact', ?, 'secret-token', 1, 1)",
        [run_id],
    )

    response = await maintain_agent_artifacts()

    assert response["success"] is True
    assert response["data"]["report"]["expiredClaimsReleased"] == 1
    assert response["data"]["report"]["releasedClaims"] == 1
    assert response["data"]["report"]["purgedArtifacts"] == 0
    assert response["data"]["snapshot"]["claimCount"] == 0
    assert await temp_db.fetch_one(
        "SELECT id FROM ai_agent_artifacts WHERE id = 'manual-artifact'"
    ) == {"id": "manual-artifact"}
    wire = json.dumps(response, ensure_ascii=False)
    assert "secret-token" not in wire


async def test_diagnostics_exposes_owner_reference_without_claim_secret(
    temp_db: DatabaseConnection,
):
    from purra.artifacts import ArtifactCreateCommand, ArtifactOwnerRef
    from purra.artifacts.continuity import ArtifactWriteClaimCommand
    from infrastructure.persistence.run_store import create_run
    from infrastructure.persistence.sqlite_artifact_repository import (
        SqliteArtifactRepository,
    )
    from infrastructure.persistence.sqlite_artifact_claim_repository import (
        SqliteArtifactClaimRepository,
    )
    from routers.ai import get_agent_run_diagnostics

    run_id = await create_run(
        temp_db,
        session_id=None,
        prompt="inspect Artifact lineage",
        mode="agent",
    )
    artifact = await SqliteArtifactRepository(temp_db).create(
        "diagnostic-artifact",
        ArtifactCreateCommand(
            namespace="purrtypos.screenplay",
            kind="scene_list_batches",
            owner_id="diagnostic-project",
            owner_ref=ArtifactOwnerRef("agent_run", run_id),
            created_by_run_id=run_id,
        ),
    )
    await SqliteArtifactClaimRepository(
        temp_db,
        token_factory=lambda: "diagnostic-secret-token",
    ).acquire(ArtifactWriteClaimCommand(
        artifact_id=artifact.id,
        run_id=run_id,
        expected_revision=artifact.revision,
        lease_duration_ms=300_000,
    ))

    response = await get_agent_run_diagnostics(run_id)

    assert response["success"] is True
    artifact = response["data"]["artifacts"]["artifacts"][0]
    assert artifact["ownerRef"] == {"kind": "agent_run", "id": run_id}
    maintenance = response["data"]["artifactMaintenance"]
    assert maintenance["artifactCount"] == 1
    assert maintenance["activeClaims"] == 1
    assert maintenance["reclaimableClaims"] == 0
    wire = json.dumps(response, ensure_ascii=False)
    assert "diagnostic-secret-token" not in wire
    assert "claimToken" not in wire
