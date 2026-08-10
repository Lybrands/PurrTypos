"""Classify provider finish reasons before output can affect execution.

Provider adapters normalize their native finish reasons into
``ModelFinishReason``.  This module is the single Core authority that decides
whether the resulting model output is complete enough to authorize a tool
batch.  In particular, reaching an output limit is never a successful commit,
even when a provider has already streamed a tool-call id and function name.
"""

from __future__ import annotations

from dataclasses import dataclass

from purra.contracts.enums import ModelFinishReason
from purra.normalization import non_negative_int, optional_text


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
        count = non_negative_int(self.tool_call_count, "tool_call_count")
        object.__setattr__(self, "tool_call_count", count)
        object.__setattr__(self, "incomplete", bool(self.incomplete))
        object.__setattr__(
            self,
            "authorizes_tool_calls",
            bool(self.authorizes_tool_calls),
        )
        object.__setattr__(self, "retryable", bool(self.retryable))
        object.__setattr__(self, "error_code", optional_text(self.error_code))
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
    incomplete = reason in {
        ModelFinishReason.LENGTH,
        ModelFinishReason.FILTERED,
        ModelFinishReason.OTHER,
    }
    error_code = {
        ModelFinishReason.FILTERED: "model_output_filtered",
        ModelFinishReason.OTHER: "unsupported_model_finish_reason",
    }.get(reason)
    if reason is ModelFinishReason.LENGTH:
        error_code = "tool_call_truncated" if count else "model_output_truncated"
    return ModelTermination(
        finish_reason=reason,
        tool_call_count=count,
        incomplete=incomplete,
        authorizes_tool_calls=bool(
            not incomplete
            and count
            and reason in {
                ModelFinishReason.TOOL_CALLS,
                ModelFinishReason.STOP,
            }
        ),
        retryable=reason is ModelFinishReason.LENGTH,
        error_code=error_code,
    )


__all__ = ["ModelTermination", "classify_model_termination"]
