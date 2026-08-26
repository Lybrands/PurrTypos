from __future__ import annotations

import json
from collections import Counter
from inspect import signature
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
SCREENPLAY_DEADLINE_FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "purra_incidents"
    / "2026-08-25-screenplay-series-arc-deadline.json"
)
ALLOWED_FIELDS = frozenset({
    "id",
    "errorCode",
    "termination",
    "requestFingerprint",
    "repeatedRequestFingerprints",
    "declaredRetryable",
    "effectState",
    "checkpointAvailable",
    "partSplittable",
    "attemptsRemaining",
    "expectedCategory",
    "expectedDisposition",
    "expectedOperationStatus",
    "completedPartRefs",
    "requestedReasoningMode",
    "observedReasoningModes",
    "reviewIssueCount",
    "verdictCount",
    "finalizationCount",
})


def _incidents() -> tuple[dict[str, object], ...]:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert payload["schemaVersion"] == 2
    return tuple(payload["incidents"])


def _operation_status(disposition: FailureDisposition) -> str:
    if disposition.value in {
        "retry_attempt",
        "resume_checkpoint",
        "split_part",
    }:
        return "running"
    if disposition is FailureDisposition.PAUSE_RECOVERABLE:
        return "paused"
    if disposition is FailureDisposition.CANCEL:
        return "canceled"
    return "failed"


def _screenplay_deadline_fixture() -> dict[str, object]:
    payload = json.loads(SCREENPLAY_DEADLINE_FIXTURE.read_text(encoding="utf-8"))
    assert payload["schemaVersion"] == 1
    return payload


def test_series_arc_deadline_fixture_is_sanitized_and_reproducible():
    payload = _screenplay_deadline_fixture()
    incident = payload["incident"]

    assert isinstance(incident, dict)
    assert set(incident).isdisjoint({
        "runId",
        "taskId",
        "invocationId",
        "prompt",
        "reasoning",
        "content",
        "payload",
    })
    assert incident == {
        "incidentClass": "screenplay_structure_series_arc_deadline",
        "unitSemanticKey": "section:structure:series_arc",
        "errorCode": "model_invocation_deadline_exceeded",
        "initialUnitCount": 8,
        "completedPartSemanticKeys": ["document:evidence"],
        "episodeIndexStarted": False,
        "modelAttemptCount": 4,
        "reportedUsageAttempts": 3,
        "inputTokens": 92_285,
        "outputTokens": 878,
        "reasoningTokens": 443,
        "providerOutputEvents": 469,
        "failedInvocationDeltaBatchEvents": 442,
        "failedInvocationPayloadChars": 1_513_586,
        "providerOutputBytes": 1_628_472,
        "providerInvocationTimeoutMs": 120_000,
        "elapsedMs": 129_000,
        "delegationCount": 0,
        "containsPrivateReasoning": False,
    }


def test_series_arc_deadline_is_not_replayed_as_a_silent_or_retryable_wait():
    payload = _screenplay_deadline_fixture()
    incident = payload["incident"]
    assert isinstance(incident, dict)

    assert int(incident["providerOutputEvents"]) > 0
    assert int(incident["failedInvocationDeltaBatchEvents"]) > 0
    assert int(incident["failedInvocationPayloadChars"]) > 0
    assert incident["episodeIndexStarted"] is False
    assert incident["completedPartSemanticKeys"] == ["document:evidence"]

    signal = classify_screenplay_run_failure(SimpleNamespace(
        code=incident["errorCode"],
        retryable=False,
    ))
    decision = decide_failure(signal, attempts_remaining=3)
    assert signal.category.value == "business_invariant"
    assert signal.retryable is False
    assert decision.disposition.value == "fail_permanent"


def test_series_arc_overreach_fixture_names_the_phase_three_rejection():
    payload = _screenplay_deadline_fixture()
    candidate = payload["invalidSeriesArcCandidate"]
    expected = payload["expectedValidation"]

    assert isinstance(candidate, dict)
    assert isinstance(expected, dict)
    phases = candidate["contentJson"]["seriesArc"]["phases"]
    assert phases[0]["episodes"] == [{"number": 1, "id": "ep01"}]
    assert expected == {
        "outcome": "reject",
        "reasonCode": "series_arc_contains_episode_expansion",
    }


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
    assert all(set(item) == ALLOWED_FIELDS for item in incidents)


def test_length_terminations_never_repeat_the_same_request_fingerprint():
    incidents = [
        incident
        for incident in _incidents()
        if incident["termination"] == "length"
    ]

    assert incidents
    for incident in incidents:
        assert incident["requestFingerprint"] not in incident[
            "repeatedRequestFingerprints"
        ], incident["id"]
        assert len(incident["observedReasoningModes"]) == 1, incident["id"]


def test_system_failures_do_not_become_review_findings_or_finalization():
    for incident in _incidents():
        completed_before = tuple(incident["completedPartRefs"])

        assert incident["reviewIssueCount"] == 0, incident["id"]
        assert incident["verdictCount"] == 0, incident["id"]
        assert incident["finalizationCount"] == 0, incident["id"]
        assert tuple(incident["completedPartRefs"]) == completed_before


def test_historical_failures_replay_without_terminal_amplification():
    for incident in _incidents():
        completed_before = tuple(incident["completedPartRefs"])
        signal = classify_screenplay_run_failure(SimpleNamespace(
            code=incident["errorCode"],
            retryable=incident["declaredRetryable"],
        ))
        signal_kwargs = {
            "category": signal.category,
            "code": signal.code,
            "retryable": signal.retryable,
            "effect_state": RecoveryEffectState(incident["effectState"]),
            "checkpoint_available": incident["checkpointAvailable"],
        }
        if "part_splittable" in signature(type(signal)).parameters:
            signal_kwargs["part_splittable"] = incident["partSplittable"]
        signal = type(signal)(**signal_kwargs)
        decision = decide_failure(
            signal,
            attempts_remaining=incident["attemptsRemaining"],
        )

        assert signal.category.value == incident["expectedCategory"], incident["id"]
        assert decision.disposition.value == incident["expectedDisposition"], incident["id"]
        assert _operation_status(decision.disposition) == incident[
            "expectedOperationStatus"
        ], incident["id"]
        assert tuple(incident["completedPartRefs"]) == completed_before
        assert all(
            mode == incident["requestedReasoningMode"]
            for mode in incident["observedReasoningModes"]
        ), incident["id"]
        assert incident["reviewIssueCount"] == 0
        assert incident["verdictCount"] == 0
        assert incident["finalizationCount"] == 0
