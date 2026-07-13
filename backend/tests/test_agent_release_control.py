from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from database.connection import DatabaseConnection
from dependencies import set_db
from services.agent_release_control import evaluate_release_gate, evaluate_rollout


def _snapshot(
    index: int,
    *,
    verdict: str = "pass",
    governance: str = "pass",
    latency: int = 1_000,
    quality: float | None = 0.8,
) -> dict:
    return {
        "runId": f"run-{index}",
        "release": {"version": "candidate", "cohort": "canary"},
        "runStatus": "done",
        "operationalVerdict": verdict,
        "toolGovernance": governance,
        "toolReliability": "pass",
        "recordedTotalMs": latency,
        "humanQuality": quality,
        "reviewCount": 1 if quality is not None else 0,
    }


def test_release_gate_approves_only_complete_safe_reviewed_pilot():
    report = evaluate_release_gate(
        {"summary": {"total": 7, "passed": 7, "failed": 0}},
        [_snapshot(index) for index in range(5)],
    )

    assert report["decision"] == "approve_canary"
    assert report["metrics"]["meanHumanQuality"] == 0.8


def test_release_gate_holds_insufficient_evidence_and_rejects_safety_failure():
    hold = evaluate_release_gate(
        {"summary": {"failed": 0}},
        [_snapshot(1)],
    )
    rejected = evaluate_release_gate(
        {"summary": {"failed": 0}},
        [_snapshot(index) for index in range(5)],
        missing_run_ids=["run-missing"],
    )

    assert hold["decision"] == "hold"
    assert rejected["decision"] == "reject"


def test_release_gate_rejects_sufficient_evidence_of_low_quality():
    report = evaluate_release_gate(
        {"summary": {"failed": 0}},
        [_snapshot(index, quality=0.6) for index in range(5)],
    )

    assert report["decision"] == "reject"
    quality = next(check for check in report["checks"] if check["name"] == "humanQuality")
    assert quality["status"] == "fail"


def test_release_gate_rejects_a_security_redteam_regression():
    report = evaluate_release_gate(
        {"summary": {"failed": 0}},
        [_snapshot(index) for index in range(5)],
        security_suite={"summary": {"failed": 1}},
    )

    assert report["decision"] == "reject"
    security = next(check for check in report["checks"] if check["name"] == "securityRedTeam")
    assert security["status"] == "fail"


def test_rollout_promotes_healthy_candidate_and_holds_latency_regression():
    baseline = [_snapshot(index, latency=1_000) for index in range(5)]
    healthy = [_snapshot(index + 10, latency=1_100) for index in range(5)]
    slow = [_snapshot(index + 20, latency=1_300) for index in range(5)]

    assert evaluate_rollout(baseline, healthy)["decision"] == "promote"
    slow_report = evaluate_rollout(baseline, slow)
    assert slow_report["decision"] == "hold"
    assert "latency_regression" in slow_report["reasons"]


def test_rollout_recommends_rollback_for_governance_or_quality_regression():
    baseline = [_snapshot(index, quality=0.85) for index in range(5)]
    unsafe = [_snapshot(index + 10) for index in range(5)]
    unsafe[0]["toolGovernance"] = "fail"
    low_quality = [_snapshot(index + 20, quality=0.7) for index in range(5)]

    assert evaluate_rollout(baseline, unsafe)["decision"] == "rollback"
    quality_report = evaluate_rollout(baseline, low_quality)
    assert quality_report["decision"] == "rollback"
    assert "human_quality_regression" in quality_report["reasons"]


@pytest_asyncio.fixture
async def temp_db(tmp_path: Path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    set_db(db)
    try:
        yield db
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_runs_persist_release_identity_and_diagnostics_expose_it(
    temp_db: DatabaseConnection,
):
    from routers.ai import get_agent_run_diagnostics
    from services.agent_run_store import append_trace, create_run, update_run_status

    run_id = await create_run(
        temp_db,
        session_id=1,
        prompt="pilot",
        mode="agent",
        release_version="v2-canary",
        rollout_cohort="candidate",
    )
    await append_trace(temp_db, run_id, stage="planner", outcome="model_plan")
    await append_trace(temp_db, run_id, stage="context_budget", outcome="within_budget")
    await append_trace(temp_db, run_id, stage="terminal", outcome="done")
    await update_run_status(temp_db, run_id, "done")

    response = await get_agent_run_diagnostics(run_id)

    assert response["data"]["release"] == {
        "version": "v2-canary",
        "cohort": "candidate",
    }


@pytest.mark.asyncio
async def test_rollout_endpoint_rejects_overlapping_cohorts():
    from routers.ai import evaluate_agent_rollout
    from schemas.ai import EvaluateAgentRolloutRequest

    response = await evaluate_agent_rollout(EvaluateAgentRolloutRequest(
        baselineRunIds=["run-same"],
        candidateRunIds=["run-same"],
    ))

    assert response["success"] is False
    assert response["data"]["overlappingRunIds"] == ["run-same"]
