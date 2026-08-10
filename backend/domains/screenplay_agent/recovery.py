"""Recovery policy for bounded screenplay generation fragments.

The model Run and the durable unit are two recovery layers around the same
fragment. Keep their classification in one place so an inner protocol failure
cannot silently bypass the unit's persisted attempt budget.
"""

from __future__ import annotations


_RETRYABLE_RUN_CODES = frozenset({
    # Provider/transmission failures.
    "upstream_stream_interrupted",
    "model_gateway_error",
    "provider_unavailable",
    "provider_rate_limited",
    # Bounded model-output/protocol failures. Retrying is safe because a
    # fragment is checkpointed only after its candidate Artifact validates.
    "model_output_truncated",
    "tool_call_truncated",
    "invalid_tool_arguments_json",
    "tool_input_invalid",
    "invalid_tool_results",
    "max_model_rounds",
    "missing_required_tool_call",
    "unstructured_tool_protocol",
    "empty_model_response",
    "model_candidate_invalid",
    "candidate_validation_failed",
    "candidate_commit_failed",
    "candidate_commit_missing",
    "completion_projection_failed",
})


def is_retryable_screenplay_run_error(code: object) -> bool:
    return str(code or "").strip() in _RETRYABLE_RUN_CODES


__all__ = ["is_retryable_screenplay_run_error"]
