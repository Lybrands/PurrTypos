"""One host-owned token budget for the complete model request.

The estimator is intentionally deterministic and provider agnostic.  It is
not a replacement for a model tokenizer; the separate safety reserve absorbs
normal tokenizer differences.  Its purpose is to make every context producer
spend from the same account instead of independently treating the whole model
window as its own budget.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any, Iterable


CONTEXT_WINDOW_TOKENS: dict[str, int] = {
    "32k": 32_000,
    "64k": 64_000,
    "128k": 128_000,
    "200k": 200_000,
    "300k": 300_000,
    "1m": 1_000_000,
}


def context_window_tokens(value: Any) -> int:
    key = str(value or "").strip().lower()
    if not key:
        return CONTEXT_WINDOW_TOKENS["200k"]
    if key not in CONTEXT_WINDOW_TOKENS:
        raise ValueError(f"不支持的上下文窗口配置：{value}")
    return CONTEXT_WINDOW_TOKENS[key]


def estimate_text_tokens(text: Any) -> int:
    """Conservative estimate for mixed CJK and non-CJK text.

    Non-ASCII scripts and emoji are charged at no less than one token per code
    point, while ordinary ASCII prose averages roughly four characters per
    token. JSON/messages use a denser estimate through ``estimate_json_tokens``.
    """
    return _estimate_units(str(text or ""), ascii_divisor=4)


def _estimate_units(value: str, *, ascii_divisor: int) -> int:
    ascii_count = sum(1 for char in value if ord(char) < 128)
    non_ascii_count = len(value) - ascii_count
    # Treat every non-ASCII code point as at least one token. This avoids the
    # previous undercount for kana, Hangul, emoji and other scripts.
    return non_ascii_count + math.ceil(ascii_count / max(1, ascii_divisor))


def _compact_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )


def estimate_json_tokens(value: Any) -> int:
    # Structured/high-entropy ASCII tokenizes more densely than normal prose.
    return _estimate_units(_compact_json(value), ascii_divisor=2)


def estimate_messages_tokens(messages: Iterable[dict] | None) -> int:
    # Per-message and reply priming overheads deliberately err high.
    return 2 + sum(estimate_json_tokens(message) + 4 for message in (messages or []))


def estimate_tool_schema_tokens(tools: Iterable[dict] | None) -> int:
    rows = list(tools or [])
    if not rows:
        return 0
    return estimate_json_tokens(rows) + 8 * len(rows)


@dataclass(frozen=True)
class ContextBudgetAllocation:
    window_tokens: int
    output_reserve_tokens: int
    safety_reserve_tokens: int
    runtime_reserve_tokens: int
    tool_schema_tokens: int
    provider_input_tokens: int
    memory_context_tokens: int
    associated_context_tokens: int
    minimum_message_tokens: int

    @property
    def round_input_tokens(self) -> int:
        """Maximum input after tool results begin consuming runtime reserve."""
        return self.provider_input_tokens + self.runtime_reserve_tokens

    def diagnostics(
        self,
        messages: list[dict],
        *,
        dropped_messages: int = 0,
        memory_tokens: int = 0,
        associated_tokens: int = 0,
    ) -> dict[str, int]:
        actual_input = estimate_messages_tokens(messages)
        projected = (
            actual_input
            + self.tool_schema_tokens
            + self.output_reserve_tokens
            + self.safety_reserve_tokens
            + self.runtime_reserve_tokens
        )
        return {
            "windowTokens": self.window_tokens,
            "estimatedInputTokens": actual_input,
            "toolSchemaTokens": self.tool_schema_tokens,
            "outputReserveTokens": self.output_reserve_tokens,
            "safetyReserveTokens": self.safety_reserve_tokens,
            "runtimeReserveTokens": self.runtime_reserve_tokens,
            "memoryTokens": max(0, int(memory_tokens)),
            "associatedTokens": max(0, int(associated_tokens)),
            "droppedMessages": max(0, int(dropped_messages)),
            "projectedTotalTokens": projected,
            "overflowTokens": max(0, projected - self.window_tokens),
        }


def allocate_context_budget(
    *,
    context_window: Any,
    output_reserve_tokens: Any,
    tools: Iterable[dict] | None,
    has_memory: bool,
    has_associated_context: bool,
) -> ContextBudgetAllocation:
    window = context_window_tokens(context_window)
    try:
        requested_output = int(output_reserve_tokens)
    except (TypeError, ValueError):
        requested_output = 8192
    if requested_output <= 0:
        requested_output = 8192

    safety = min(64_000, max(4_096, math.ceil(window * 0.05)))
    tool_tokens = estimate_tool_schema_tokens(tools)
    has_tools = tool_tokens > 0
    runtime = (
        min(64_000, max(4_096, window // 10))
        if has_tools
        else min(16_000, max(2_048, window // 25))
    )
    provider_input = max(
        0,
        window - requested_output - safety - runtime - tool_tokens,
    )

    minimum_messages = min(8_192, max(1_024, window // 100))
    context_pool = max(0, provider_input - minimum_messages)
    desired_memory = (
        min(40_000, max(6_000, window // 25)) if has_memory else 0
    )
    desired_associated = (
        min(120_000, max(24_000, window // 5))
        if has_associated_context
        else 0
    )
    desired_total = desired_memory + desired_associated
    if desired_total <= context_pool:
        memory_budget = desired_memory
        associated_budget = desired_associated
    elif desired_total > 0:
        ratio = context_pool / desired_total
        memory_budget = math.floor(desired_memory * ratio)
        associated_budget = max(0, context_pool - memory_budget)
    else:
        memory_budget = 0
        associated_budget = 0

    return ContextBudgetAllocation(
        window_tokens=window,
        output_reserve_tokens=requested_output,
        safety_reserve_tokens=safety,
        runtime_reserve_tokens=runtime,
        tool_schema_tokens=tool_tokens,
        provider_input_tokens=provider_input,
        memory_context_tokens=memory_budget,
        associated_context_tokens=associated_budget,
        minimum_message_tokens=minimum_messages,
    )


@dataclass(frozen=True)
class TrimmedMessages:
    messages: list[dict]
    dropped_count: int
    token_estimate: int
    overflow_tokens: int


def trim_messages_by_turn(
    messages: Iterable[dict] | None,
    token_budget: int,
) -> TrimmedMessages:
    """Keep system messages and the newest complete conversation turns.

    A turn begins with a user message and contains every following assistant
    and tool message until the next user message.  Treating the turn as one
    atom prevents an assistant tool call from being separated from its tool
    result when old history is removed.
    """
    rows = [dict(message) for message in (messages or []) if isinstance(message, dict)]
    systems: list[tuple[int, dict]] = []
    turns: list[list[tuple[int, dict]]] = []
    current: list[tuple[int, dict]] = []

    for index, message in enumerate(rows):
        role = str(message.get("role") or "")
        if role in {"system", "developer"}:
            systems.append((index, message))
            continue
        if role == "user" and current:
            turns.append(current)
            current = []
        current.append((index, message))
    if current:
        turns.append(current)

    fixed_cost = 2 + sum(estimate_json_tokens(row) + 4 for _, row in systems)
    selected_indices = {index for index, _ in systems}
    used = fixed_cost

    # The newest turn contains the active user request and is mandatory.  If
    # it alone is too large, report overflow rather than silently truncating it.
    for reverse_index, turn in enumerate(reversed(turns)):
        turn_cost = sum(estimate_json_tokens(row) + 4 for _, row in turn)
        required = reverse_index == 0
        if required or used + turn_cost <= max(0, token_budget):
            selected_indices.update(index for index, _ in turn)
            used += turn_cost
            continue
        break

    selected = [row for index, row in enumerate(rows) if index in selected_indices]
    actual = estimate_messages_tokens(selected)
    return TrimmedMessages(
        messages=selected,
        dropped_count=max(0, len(rows) - len(selected)),
        token_estimate=actual,
        overflow_tokens=max(0, actual - max(0, int(token_budget))),
    )
