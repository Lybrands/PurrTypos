from __future__ import annotations

from purra.contracts import AgentRunResult, RunStatus
from application.sse_mapping import core_update_to_sse_chunk


def test_transport_result_exposes_stable_error_code_without_authored_copy():
    chunk = core_update_to_sse_chunk(
        AgentRunResult(
            run_id="run-constraint",
            status=RunStatus.FAILED,
            error="response_constraint_violation",
        ),
        model="model",
    )

    assert chunk == {
        "done": True,
        "model": "model",
        "runResult": {
            "runId": "run-constraint",
            "status": "failed",
            "errorCode": "response_constraint_violation",
        },
    }
    assert "error" not in chunk


def test_transport_result_keeps_provider_resolved_model():
    assert core_update_to_sse_chunk(
        AgentRunResult(
            run_id="run-done",
            status=RunStatus.DONE,
            model="provider-model",
        ),
        model="request-model",
    ) == {
        "done": True,
        "model": "provider-model",
        "runResult": {
            "runId": "run-done",
            "status": "done",
            "errorCode": None,
        },
    }
