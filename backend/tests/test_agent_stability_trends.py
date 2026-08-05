from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from agent_core.evaluation import (
    StabilityTrendPolicy,
    evaluate_agent_run_stability,
    evaluate_agent_run_stability_trend,
    evaluate_stability_regression_gate,
)
from database.connection import DatabaseConnection
from dependencies import set_db


@pytest_asyncio.fixture
async def temp_db(tmp_path: Path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    set_db(db)
    try:
        yield db
    finally:
        await db.close()


def _sample(
    run_id: str,
    *,
    run_status: str = "done",
    verdict: str = "pass",
    **metrics,
) -> dict:
    defaults = {
        "toolCalls": 0,
        "toolProtocolFailures": 0,
        "incompleteToolCalls": 0,
        "contextOverflows": 0,
        "compactionFailures": 0,
        "modelAttempts": 1,
        "retryAttempts": 0,
        "toolErrorCodes": {},
    }
    defaults.update(metrics)
    return {
        "runId": run_id,
        "runStatus": run_status,
        "createTime": f"2026-08-03 00:00:0{run_id[-1:]}",
        "stability": {"verdict": verdict, "metrics": defaults},
    }


def _check(report: dict, name: str) -> dict:
    return next(item for item in report["checks"] if item["name"] == name)


def test_trend_requires_a_minimum_sample_before_alerting():
    report = evaluate_agent_run_stability_trend([
        _sample("run-1", run_status="failed", verdict="fail"),
    ])

    assert report["verdict"] == "insufficient_data"
    assert report["sampleSize"] == 1
    assert report["alerts"] == []
    assert all(
        check["status"] == "insufficient_data"
        for check in report["checks"]
        if check["name"] != "failureStreak"
    )
    assert _check(report, "failureStreak")["status"] == "pass"


def test_consecutive_failures_alert_even_before_rate_sample_is_large_enough():
    report = evaluate_agent_run_stability_trend([
        _sample("run-3", run_status="failed", verdict="fail"),
        _sample("run-2", run_status="failed", verdict="fail"),
        _sample("run-1", run_status="failed", verdict="fail"),
    ])

    assert report["sampleSize"] == 3
    assert report["verdict"] == "fail"
    assert _check(report, "runFailureRate")["status"] == "insufficient_data"
    assert _check(report, "failureStreak")["status"] == "fail"
    assert report["alerts"] == [{
        "code": "failureStreak",
        "severity": "fail",
        "value": 3,
        "threshold": 3,
    }]


def test_trend_alerts_at_configured_boundaries_and_tracks_failure_streak():
    report = evaluate_agent_run_stability_trend([
        _sample(
            "run-5",
            verdict="fail",
            toolCalls=1,
            toolProtocolFailures=1,
            toolErrorCodes={"invalid_tool_arguments_json": 1},
        ),
        _sample("run-4", run_status="failed", verdict="fail"),
        _sample("run-3"),
        _sample("run-2"),
        _sample("run-1"),
    ])

    assert report["verdict"] == "fail"
    assert report["metrics"]["runFailureRate"] == 0.2
    assert report["metrics"]["stabilityFailureRate"] == 0.4
    assert report["metrics"]["currentFailureStreak"] == 2
    assert report["metrics"]["topToolErrorCodes"] == [{
        "code": "invalid_tool_arguments_json",
        "count": 1,
    }]
    assert _check(report, "runFailureRate")["status"] == "warn"
    assert _check(report, "stabilityFailureRate")["status"] == "fail"
    assert _check(report, "toolProtocolRunRate")["status"] == "fail"
    assert _check(report, "failureStreak")["status"] == "warn"


def test_trend_policy_is_injectable_and_report_does_not_copy_content():
    policy = StabilityTrendPolicy(
        minimum_sample_size=2,
        run_failure_rate_warn=0.5,
        run_failure_rate_fail=0.75,
    )
    first = _sample("run-2", run_status="failed", verdict="fail")
    first["prompt"] = "secret prompt"
    first["stability"]["generatedContent"] = "private result"

    report = evaluate_agent_run_stability_trend(
        [first, _sample("run-1")],
        policy=policy,
    )

    assert _check(report, "runFailureRate")["status"] == "warn"
    assert "secret prompt" not in str(report)
    assert "private result" not in str(report)


def test_regression_gate_fails_when_recent_rates_exceed_the_prior_window():
    baseline = evaluate_agent_run_stability_trend([
        _sample(f"baseline-{index}") for index in range(5)
    ])
    candidate = evaluate_agent_run_stability_trend([
        _sample("candidate-5", run_status="failed", verdict="fail"),
        *[_sample(f"candidate-{index}") for index in range(4)],
    ])

    gate = evaluate_stability_regression_gate(candidate, baseline)

    assert gate["verdict"] == "fail"
    assert gate["metricDeltas"]["runFailureRate"] == 0.2
    run_rate = next(
        check
        for check in gate["checks"]
        if check["name"] == "rateRegression:runFailureRate"
    )
    assert run_rate["status"] == "fail"


def test_regression_gate_detects_a_new_error_code_with_a_small_candidate():
    baseline = evaluate_agent_run_stability_trend([
        _sample(f"baseline-{index}") for index in range(5)
    ])
    candidate = evaluate_agent_run_stability_trend([
        _sample(
            "candidate-1",
            verdict="fail",
            toolCalls=1,
            toolProtocolFailures=1,
            toolErrorCodes={"invalid_tool_arguments_json": 1},
        ),
    ])

    gate = evaluate_stability_regression_gate(candidate, baseline)

    assert gate["verdict"] == "fail"
    assert gate["newToolErrorCodes"] == ["invalid_tool_arguments_json"]
    error_codes = next(
        check for check in gate["checks"] if check["name"] == "newToolErrorCodes"
    )
    assert error_codes["status"] == "fail"


def test_regression_gate_detects_one_new_incomplete_tool_before_rate_sampling():
    baseline = evaluate_agent_run_stability_trend([
        _sample(f"baseline-{index}") for index in range(5)
    ])
    candidate = evaluate_agent_run_stability_trend([
        _sample(
            "candidate-1",
            verdict="fail",
            toolCalls=1,
            incompleteToolCalls=1,
        ),
    ])

    gate = evaluate_stability_regression_gate(candidate, baseline)

    assert gate["verdict"] == "fail"
    assert "incompleteToolRuns" in gate["newCriticalSignals"]
    critical = next(
        check for check in gate["checks"] if check["name"] == "newCriticalSignals"
    )
    assert critical["status"] == "fail"


def test_regression_gate_reports_insufficient_data_without_a_baseline():
    candidate = evaluate_agent_run_stability_trend([_sample("candidate-1")])
    baseline = evaluate_agent_run_stability_trend([])

    gate = evaluate_stability_regression_gate(candidate, baseline)

    assert gate["verdict"] == "insufficient_data"
    assert gate["alerts"] == []


@pytest.mark.asyncio
async def test_stability_evidence_query_projects_away_large_tool_content(
    temp_db: DatabaseConnection,
):
    from infrastructure.persistence.run_store import append_event, create_run
    from infrastructure.persistence.stability_query import (
        list_recent_run_stability_evidence,
    )

    session_id = await temp_db.execute_and_get_id(
        "INSERT INTO ai_sessions (title) VALUES ('trend')"
    )
    run_id = await create_run(
        temp_db,
        session_id=session_id,
        prompt="secret prompt",
        mode="agent",
    )
    await append_event(
        temp_db,
        run_id,
        "tool.calls_started",
        {"calls": [{
            "id": "call-1",
            "name": "readA",
            "arguments_json": "secret arguments",
        }]},
    )
    await append_event(
        temp_db,
        run_id,
        "tool.results",
        {"results": [{
            "tool_call_id": "call-1",
            "tool_name": "readA",
            "content": "private tool result",
        }]},
    )
    await temp_db.execute(
        "UPDATE ai_agent_runs SET status = 'done' WHERE id = ?",
        [run_id],
    )

    evidence = await list_recent_run_stability_evidence(
        temp_db,
        session_id=session_id,
    )
    stability = evaluate_agent_run_stability(evidence[0]["events"])

    assert stability["metrics"]["completedToolCalls"] == 1
    assert "secret arguments" not in str(evidence)
    assert "private tool result" not in str(evidence)
    assert "secret prompt" not in str(evidence)


@pytest.mark.asyncio
async def test_auto_scope_aggregates_screenplay_project_and_excludes_others(
    temp_db: DatabaseConnection,
):
    from infrastructure.persistence.run_store import create_run
    from routers.ai import get_agent_run_stability_trend

    project_a = "screenplay-a"
    project_b = "screenplay-b"
    session_a1 = await temp_db.execute_and_get_id(
        "INSERT INTO ai_sessions (title, scope, screenplay_project_id) "
        "VALUES ('A1', 'screenplay', ?)",
        [project_a],
    )
    session_a2 = await temp_db.execute_and_get_id(
        "INSERT INTO ai_sessions (title, scope, screenplay_project_id) "
        "VALUES ('A2', 'screenplay', ?)",
        [project_a],
    )
    session_b = await temp_db.execute_and_get_id(
        "INSERT INTO ai_sessions (title, scope, screenplay_project_id) "
        "VALUES ('B', 'screenplay', ?)",
        [project_b],
    )
    run_a1 = await create_run(
        temp_db, session_id=session_a1, prompt="a1", mode="agent"
    )
    run_a2 = await create_run(
        temp_db, session_id=session_a2, prompt="a2", mode="agent"
    )
    run_b = await create_run(
        temp_db, session_id=session_b, prompt="b", mode="agent"
    )
    run_canceled = await create_run(
        temp_db, session_id=session_a1, prompt="canceled", mode="agent"
    )
    for run_id in (run_a1, run_a2, run_b):
        await temp_db.execute(
            "UPDATE ai_agent_runs SET status = 'done' WHERE id = ?",
            [run_id],
        )
    await temp_db.execute(
        "UPDATE ai_agent_runs SET status = 'canceled' WHERE id = ?",
        [run_canceled],
    )

    response = await get_agent_run_stability_trend(
        run_a1,
        scope="auto",
        limit=20,
    )

    assert response["success"] is True
    report = response["data"]
    assert report["scope"] == {
        "type": "screenplay_project",
        "id": project_a,
    }
    assert report["sampleSize"] == 2
    assert report["comparisonWindowSize"] == 1
    assert report["regressionGate"]["verdict"] == "insufficient_data"
    assert {item["runId"] for item in report["recentRuns"]} == {
        run_a1,
        run_a2,
    }
    assert run_b not in str(report)
    assert run_canceled not in str(report)


@pytest.mark.asyncio
async def test_recent_evidence_window_stays_bounded_across_many_runs(
    temp_db: DatabaseConnection,
):
    from infrastructure.persistence.run_store import create_run
    from infrastructure.persistence.stability_query import (
        list_recent_run_stability_evidence,
    )

    session_id = await temp_db.execute_and_get_id(
        "INSERT INTO ai_sessions (title) VALUES ('stress')"
    )
    run_ids: list[str] = []
    for index in range(120):
        run_id = await create_run(
            temp_db,
            session_id=session_id,
            prompt=f"large prompt {index} " + ("x" * 20_000),
            mode="agent",
        )
        run_ids.append(run_id)
        await temp_db.execute(
            "UPDATE ai_agent_runs SET status = 'done', "
            "update_time = '2026-08-03 12:00:00' WHERE id = ?",
            [run_id],
        )

    evidence = await list_recent_run_stability_evidence(
        temp_db,
        session_id=session_id,
        limit=20,
    )

    assert [item["runId"] for item in evidence] == list(
        reversed(run_ids[-20:])
    )
    assert len(str(evidence)) < 20_000


@pytest.mark.asyncio
async def test_scoped_trend_endpoint_compares_recent_and_baseline_windows(
    temp_db: DatabaseConnection,
):
    from infrastructure.persistence.run_store import append_event, create_run
    from routers.ai import get_agent_run_stability_trend

    session_id = await temp_db.execute_and_get_id(
        "INSERT INTO ai_sessions (title) VALUES ('rolling gate')"
    )
    for index in range(5):
        run_id = await create_run(
            temp_db,
            session_id=session_id,
            prompt=f"baseline {index}",
            mode="agent",
        )
        await temp_db.execute(
            "UPDATE ai_agent_runs SET status = 'done', "
            "update_time = '2026-08-03 10:00:00' WHERE id = ?",
            [run_id],
        )
    for index in range(4):
        run_id = await create_run(
            temp_db,
            session_id=session_id,
            prompt=f"candidate {index}",
            mode="agent",
        )
        await temp_db.execute(
            "UPDATE ai_agent_runs SET status = 'done', "
            "update_time = '2026-08-03 11:00:00' WHERE id = ?",
            [run_id],
        )
    failed_run_id = await create_run(
        temp_db,
        session_id=session_id,
        prompt="candidate failure",
        mode="agent",
    )
    await append_event(
        temp_db,
        failed_run_id,
        "tool.calls_started",
        {"calls": [{"id": "call-json", "name": "persistArtifactBatch"}]},
    )
    await append_event(
        temp_db,
        failed_run_id,
        "tool.results",
        {"results": [{
            "tool_call_id": "call-json",
            "tool_name": "persistArtifactBatch",
            "error": "invalid_tool_arguments_json",
        }]},
    )
    await temp_db.execute(
        "UPDATE ai_agent_runs SET status = 'failed', "
        "update_time = '2026-08-03 11:00:00' WHERE id = ?",
        [failed_run_id],
    )

    response = await get_agent_run_stability_trend(
        failed_run_id,
        scope="session",
        limit=10,
    )

    assert response["success"] is True
    report = response["data"]
    assert report["comparisonWindowSize"] == 5
    assert report["regressionGate"]["verdict"] == "fail"
    assert report["regressionGate"]["newToolErrorCodes"] == [
        "invalid_tool_arguments_json"
    ]
