"""Completion evidence validator for replacement Screenplay Parts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from agents.screenplay.contracts import (
    SCREENPLAY_PART_REGISTRY,
    ScreenplayPartCompletion,
    ScreenplayPartKind,
)


class ScreenplayPartOutputError(ValueError):
    def __init__(self, code: str, *, retryable: bool) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class ScreenplayPartOutputEvidence:
    candidate_artifact_id: str | None = None
    host_capture: Mapping[str, Any] | None = None
    host_result: Mapping[str, Any] | None = None


def validate_screenplay_part_output(
    kind: ScreenplayPartKind | str,
    evidence: ScreenplayPartOutputEvidence,
) -> Mapping[str, Any]:
    definition = SCREENPLAY_PART_REGISTRY[ScreenplayPartKind(kind)]
    artifact_id = str(evidence.candidate_artifact_id or "").strip()
    capture = dict(evidence.host_capture or {})
    host_result = dict(evidence.host_result or {})

    if definition.completion is ScreenplayPartCompletion.CANDIDATE_TOOL:
        if not artifact_id:
            raise ScreenplayPartOutputError(
                "screenplay_candidate_missing",
                retryable=True,
            )
        if capture or host_result:
            raise ScreenplayPartOutputError(
                "screenplay_part_completion_conflict",
                retryable=False,
            )
        return {"completion": "candidate_tool", "candidateArtifactId": artifact_id}

    if definition.completion is ScreenplayPartCompletion.HOST_CAPTURE:
        if not capture:
            raise ScreenplayPartOutputError(
                "screenplay_structured_output_invalid",
                retryable=True,
            )
        if artifact_id or host_result:
            raise ScreenplayPartOutputError(
                "screenplay_part_completion_conflict",
                retryable=False,
            )
        return {"completion": "host_capture", "hostCapture": capture}

    if not host_result:
        raise ScreenplayPartOutputError(
            "screenplay_host_result_missing",
            retryable=False,
        )
    if artifact_id or capture:
        raise ScreenplayPartOutputError(
            "screenplay_part_completion_conflict",
            retryable=False,
        )
    return {"completion": "host_only", "hostResult": host_result}


__all__ = [
    "ScreenplayPartOutputError",
    "ScreenplayPartOutputEvidence",
    "validate_screenplay_part_output",
]
