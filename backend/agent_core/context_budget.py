"""Provider-neutral message estimation and complete-turn trimming."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from agent_core.contracts import (
    AgentMessage,
    ContextBudget,
    ContextBudgetClaim,
    MessageRole,
    ToolSchema,
)
from agent_core.errors import ContextOverflowError
from agent_core.json_values import thaw_json_mapping, thaw_json_value


def _estimate_units(value: str, *, ascii_divisor: int) -> int:
    ascii_count = sum(1 for char in value if ord(char) < 128)
    non_ascii_count = len(value) - ascii_count
    return non_ascii_count + math.ceil(ascii_count / max(1, ascii_divisor))


def estimate_json_tokens(value: Any) -> int:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return _estimate_units(encoded, ascii_divisor=2)


def _budget_message_mapping(message: AgentMessage) -> dict[str, Any]:
    """Return a conservative, provider-neutral representation for budgeting.

    ``AgentMessage.to_mapping`` intentionally exposes compact Core field names.
    A model adapter must expand tool calls into a nested wire protocol, though,
    so estimating the compact form can undercount later tool-continuation
    rounds.  The descriptive keys below account for that structural overhead
    without making Core depend on any one provider's request schema.
    """

    value = thaw_json_mapping(message.attributes)
    value.update({
        "role": message.role.value,
        "content": thaw_json_value(message.content),
    })
    if message.thinking is not None:
        value["structured_thinking_content"] = message.thinking
    if message.tool_calls:
        value["structured_tool_calls"] = [
            {
                "tool_call_identifier": call.id,
                "tool_protocol_type": "function",
                "function_descriptor": {
                    "tool_function_name": call.name,
                    "serialized_arguments_json": call.arguments_json,
                },
            }
            for call in message.tool_calls
        ]
    if message.tool_call_id is not None:
        value["tool_call_identifier"] = message.tool_call_id
    return value


def estimate_tool_schema_tokens(tools: Iterable[ToolSchema]) -> int:
    rows = [
        {
            "tool_protocol_type": "function",
            "function_descriptor": {
                "tool_function_name": schema.name,
                "tool_description": schema.description,
                "tool_parameters_schema": thaw_json_mapping(schema.parameters),
            },
        }
        for schema in tools
    ]
    return 0 if not rows else estimate_json_tokens(rows) + 8 * len(rows)


def allocate_context_budget(
    *,
    window_tokens: int,
    output_reserve_tokens: int = 8_192,
    tools: Sequence[ToolSchema] = (),
    claims: Sequence[ContextBudgetClaim] = (),
    safety_reserve_tokens: int | None = None,
    runtime_reserve_tokens: int | None = None,
    minimum_message_tokens: int | None = None,
) -> ContextBudget:
    """Allocate one deterministic provider-neutral context account.

    Claim names are opaque to Core.  A domain adapter may request any named
    partitions without adding business concepts to the budget contract.
    """

    window = int(window_tokens)
    output = int(output_reserve_tokens)
    if window <= 0:
        raise ValueError("context window must be positive")
    if output <= 0:
        raise ValueError("output reserve must be positive")

    schemas = tuple(tools)
    schema_tokens = estimate_tool_schema_tokens(schemas)
    safety = (
        int(safety_reserve_tokens)
        if safety_reserve_tokens is not None
        else min(64_000, max(4_096, math.ceil(window * 0.05)))
    )
    runtime = (
        int(runtime_reserve_tokens)
        if runtime_reserve_tokens is not None
        else (
            min(64_000, max(4_096, window // 10))
            if schemas
            else min(16_000, max(2_048, window // 25))
        )
    )
    minimum = (
        int(minimum_message_tokens)
        if minimum_message_tokens is not None
        else min(8_192, max(1_024, window // 100))
    )
    if min(safety, runtime, minimum) < 0:
        raise ValueError("context reserves must be non-negative")

    provider_input = window - output - safety - runtime - schema_tokens
    if provider_input < minimum:
        raise ContextOverflowError(
            "fixed model, tool and safety reserves leave no message budget"
        )

    normalized_claims: list[ContextBudgetClaim] = []
    seen: set[str] = set()
    for claim in claims:
        normalized = (
            claim
            if isinstance(claim, ContextBudgetClaim)
            else ContextBudgetClaim(  # type: ignore[unreachable]
                name=getattr(claim, "name", ""),
                desired_tokens=getattr(claim, "desired_tokens", 0),
            )
        )
        if normalized.name in seen:
            raise ValueError(f"duplicate context budget claim: {normalized.name}")
        seen.add(normalized.name)
        normalized_claims.append(normalized)

    allocations = _allocate_claims(
        normalized_claims,
        max(0, provider_input - minimum),
    )
    return ContextBudget(
        window_tokens=window,
        output_reserve_tokens=output,
        safety_reserve_tokens=safety,
        runtime_reserve_tokens=runtime,
        tool_schema_tokens=schema_tokens,
        provider_input_tokens=provider_input,
        minimum_message_tokens=minimum,
        context_allocations=allocations,
    )


def _allocate_claims(
    claims: Sequence[ContextBudgetClaim],
    available_tokens: int,
) -> dict[str, int]:
    if not claims:
        return {}
    desired_total = sum(claim.desired_tokens for claim in claims)
    if desired_total <= available_tokens:
        return {claim.name: claim.desired_tokens for claim in claims}
    if desired_total <= 0 or available_tokens <= 0:
        return {claim.name: 0 for claim in claims}

    quotients_and_remainders = [
        divmod(claim.desired_tokens * available_tokens, desired_total)
        for claim in claims
    ]
    base = [quotient for quotient, _ in quotients_and_remainders]
    remaining = available_tokens - sum(base)
    order = sorted(
        range(len(claims)),
        key=lambda index: (-quotients_and_remainders[index][1], index),
    )
    for index in order[:remaining]:
        base[index] += 1
    return {claim.name: base[index] for index, claim in enumerate(claims)}


def estimate_agent_messages_tokens(messages: Iterable[AgentMessage]) -> int:
    return 2 + sum(
        estimate_json_tokens(_budget_message_mapping(message)) + 4
        for message in messages
    )


@dataclass(frozen=True, slots=True)
class TrimmedAgentMessages:
    messages: tuple[AgentMessage, ...]
    dropped_count: int
    token_estimate: int
    overflow_tokens: int


def trim_agent_messages_by_turn(
    messages: Sequence[AgentMessage],
    token_budget: int,
) -> TrimmedAgentMessages:
    """Keep system messages and newest complete user/assistant/tool turns."""

    rows = list(messages)
    systems: list[tuple[int, AgentMessage]] = []
    turns: list[list[tuple[int, AgentMessage]]] = []
    current: list[tuple[int, AgentMessage]] = []

    for index, message in enumerate(rows):
        if message.role in {MessageRole.SYSTEM, MessageRole.DEVELOPER}:
            systems.append((index, message))
            continue
        if message.role is MessageRole.USER and current:
            turns.append(current)
            current = []
        current.append((index, message))
    if current:
        turns.append(current)

    selected_indices = {index for index, _ in systems}
    used = 2 + sum(
        estimate_json_tokens(_budget_message_mapping(message)) + 4
        for _, message in systems
    )
    for reverse_index, turn in enumerate(reversed(turns)):
        turn_cost = sum(
            estimate_json_tokens(_budget_message_mapping(message)) + 4
            for _, message in turn
        )
        required = reverse_index == 0
        if required or used + turn_cost <= max(0, int(token_budget)):
            selected_indices.update(index for index, _ in turn)
            used += turn_cost
            continue
        break

    selected = tuple(
        message
        for index, message in enumerate(rows)
        if index in selected_indices
    )
    actual = estimate_agent_messages_tokens(selected)
    return TrimmedAgentMessages(
        messages=selected,
        dropped_count=max(0, len(rows) - len(selected)),
        token_estimate=actual,
        overflow_tokens=max(0, actual - max(0, int(token_budget))),
    )
