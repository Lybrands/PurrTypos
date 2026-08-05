"""Classify provider finish reasons before output can affect execution.

Provider adapters normalize their native finish reasons into
``ModelFinishReason``.  This module is the single Core authority that decides
whether the resulting model output is complete enough to authorize a tool
batch.  In particular, reaching an output limit is never a successful commit,
even when a provider has already streamed a tool-call id and function name.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent_core.contracts import ModelFinishReason


@dataclass(frozen=True, slots=True)
class ModelTermination:
    """Execution-facing interpretation of one terminal model stream."""

    finish_reason: ModelFinishReason
    tool_call_count: int
    incomplete: bool
    authorizes_tool_calls: bool
    retryable: bool = False
    error_code: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "finish_reason",
            ModelFinishReason(self.finish_reason),
        )
        count = int(self.tool_call_count)
        if count < 0:
            raise ValueError("tool_call_count must be non-negative")
        object.__setattr__(self, "tool_call_count", count)
        object.__setattr__(self, "incomplete", bool(self.incomplete))
        object.__setattr__(
            self,
            "authorizes_tool_calls",
            bool(self.authorizes_tool_calls),
        )
        object.__setattr__(self, "retryable", bool(self.retryable))
        normalized_error = str(self.error_code or "").strip() or None
        object.__setattr__(self, "error_code", normalized_error)
        if self.incomplete and self.authorizes_tool_calls:
            raise ValueError("incomplete output cannot authorize tool calls")
        if self.retryable and not self.incomplete:
            raise ValueError("only incomplete output can be retryable")
        if self.authorizes_tool_calls and count == 0:
            raise ValueError("tool authorization requires at least one call")


def classify_model_termination(
    finish_reason: ModelFinishReason,
    *,
    tool_call_count: int,
) -> ModelTermination:
    """Return the only execution-safe interpretation of a finish reason.

    Some OpenAI-compatible providers report ``stop`` while returning complete
    native tool calls, so Core keeps that compatibility path.  ``length`` is
    different: the provider explicitly says generation did not complete, and
    therefore no streamed arguments may be parsed, executed, or committed to
    continuation history.
    """

    reason = ModelFinishReason(finish_reason)
    count = int(tool_call_count)
    if count < 0:
        raise ValueError("tool_call_count must be non-negative")
    if reason is ModelFinishReason.LENGTH:
        return ModelTermination(
            finish_reason=reason,
            tool_call_count=count,
            incomplete=True,
            authorizes_tool_calls=False,
            retryable=True,
            error_code=(
                "tool_call_truncated" if count else "model_output_truncated"
            ),
        )
    if reason is ModelFinishReason.FILTERED:
        return ModelTermination(
            finish_reason=reason,
            tool_call_count=count,
            incomplete=True,
            authorizes_tool_calls=False,
            error_code="model_output_filtered",
        )
    if reason is ModelFinishReason.OTHER:
        return ModelTermination(
            finish_reason=reason,
            tool_call_count=count,
            incomplete=True,
            authorizes_tool_calls=False,
            error_code="unsupported_model_finish_reason",
        )
    return ModelTermination(
        finish_reason=reason,
        tool_call_count=count,
        incomplete=False,
        authorizes_tool_calls=bool(
            count
            and reason in {
                ModelFinishReason.TOOL_CALLS,
                ModelFinishReason.STOP,
            }
        ),
    )


__all__ = ["ModelTermination", "classify_model_termination"]
