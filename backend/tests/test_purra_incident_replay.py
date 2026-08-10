from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

from domains.screenplay_agent.recovery import classify_screenplay_run_failure
from purra.recovery import FailureDisposition, RecoveryEffectState, decide_failure


FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "purra_incidents"
    / "2026-08-10-failure-sequences.json"
)
ALLOWED_FIELDS = frozenset({
    "id",
    "errorCode",
    "declaredRetryable",
    "effectState",
    "checkpointAvailable",
    "attemptsRemaining",
    "expectedCategory",
    "expectedDisposition",
    "expectedTaskStatus",
    "completedOutputRefs",
    "requestedReasoningMode",
    "observedReasoningModes",
    "finalizationCount",
})


def _incidents() -> tuple[dict[str, object], ...]:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert payload["schemaVersion"] == 1
    return tuple(payload["incidents"])


def _task_status(disposition: FailureDisposition) -> str:
    if disposition in {
        FailureDisposition.RETRY_ATTEMPT,
        FailureDisposition.RESUME_CHECKPOINT,
    }:
        return "running"
    if disposition is FailureDisposition.PAUSE_RECOVERABLE:
        return "paused"
    if disposition is FailureDisposition.CANCEL:
        return "canceled"
    return "failed"


def test_historical_failure_fixture_is_sanitized_and_complete():
    incidents = _incidents()

    assert len(incidents) == 9
    assert len({item["errorCode"] for item in incidents}) == 7
    assert Counter(item["errorCode"] for item in incidents) == {
        "tool_execution_failed": 2,
        "provider_bad_request": 2,
        "model_output_truncated": 1,
        "invalid_tool_results": 1,
        "max_model_rounds": 1,
        "invalid_tool_arguments_json": 1,
        "tool_call_truncated": 1,
    }
    assert all(set(item) <= ALLOWED_FIELDS for item in incidents)


def test_historical_failures_replay_without_terminal_amplification():
    for incident in _incidents():
        completed_before = tuple(incident["completedOutputRefs"])
        signal = classify_screenplay_run_failure(SimpleNamespace(
            code=incident["errorCode"],
            retryable=incident["declaredRetryable"],
        ))
        signal = type(signal)(
            category=signal.category,
            code=signal.code,
            retryable=signal.retryable,
            effect_state=RecoveryEffectState(incident["effectState"]),
            checkpoint_available=incident["checkpointAvailable"],
        )
        decision = decide_failure(
            signal,
            attempts_remaining=incident["attemptsRemaining"],
        )

        assert signal.category.value == incident["expectedCategory"], incident["id"]
        assert decision.disposition.value == incident["expectedDisposition"], incident["id"]
        assert _task_status(decision.disposition) == incident["expectedTaskStatus"], incident["id"]
        assert tuple(incident["completedOutputRefs"]) == completed_before
        assert all(
            mode == incident["requestedReasoningMode"]
            for mode in incident["observedReasoningModes"]
        ), incident["id"]
        assert incident["finalizationCount"] == 0
