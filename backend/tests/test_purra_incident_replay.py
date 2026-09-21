from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "purra_incidents"
    / "2026-08-10-failure-sequences.json"
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
