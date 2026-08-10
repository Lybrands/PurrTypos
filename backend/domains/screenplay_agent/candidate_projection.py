"""Opaque Run-binding contract for screenplay candidate completion.

Core persists this mapping without interpreting it. The screenplay host uses
it to make candidate finalization part of the Run's terminal transaction.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


SCREENPLAY_CANDIDATE_PROJECTION_ATTRIBUTE = "candidateCompletionProjection"
SCREENPLAY_CANDIDATE_PROJECTION_PROTOCOL = (
    "purrtypos.screenplay.candidate-completion.v1"
)


def candidate_completion_projection(
    *,
    scope: Mapping[str, Any],
    host_candidate_template: Mapping[str, Any] | None = None,
    text_field: str = "sceneText",
) -> dict[str, Any]:
    projection: dict[str, Any] = {
        "protocol": SCREENPLAY_CANDIDATE_PROJECTION_PROTOCOL,
        "scope": dict(scope),
    }
    if host_candidate_template is not None:
        normalized_field = str(text_field or "").strip()
        if not normalized_field:
            raise ValueError("host candidate text field is required")
        projection["hostCapture"] = {
            "candidateTemplate": dict(host_candidate_template),
            "textField": normalized_field,
        }
    return {SCREENPLAY_CANDIDATE_PROJECTION_ATTRIBUTE: projection}


__all__ = [
    "SCREENPLAY_CANDIDATE_PROJECTION_ATTRIBUTE",
    "SCREENPLAY_CANDIDATE_PROJECTION_PROTOCOL",
    "candidate_completion_projection",
]
