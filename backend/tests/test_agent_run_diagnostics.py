from __future__ import annotations

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
    from agent_core.evaluation import evaluate_agent_run
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
    from agent_core.evaluation import evaluate_agent_run
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
    from agent_core.evaluation import evaluate_agent_run
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
