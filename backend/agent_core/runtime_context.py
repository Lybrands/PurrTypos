"""Stage-specific projection of canonical Run messages."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from agent_core.context_budget import estimate_agent_messages_tokens
from agent_core.contracts import (
    AgentMessage,
    MessageRole,
    ToolContextContract,
    ToolResultProjection,
)
from agent_core.evidence import RunEvidenceStore


@dataclass(frozen=True, slots=True)
class RuntimeContextProjection:
    messages: tuple[AgentMessage, ...]
    dropped_context_blocks: tuple[str, ...] = ()
    compacted_tool_results: tuple[str, ...] = ()
    saved_tokens: int = 0


def project_intermediate_tool_context(
    messages: Sequence[AgentMessage],
    *,
    visible_tool_names: frozenset[str],
    contracts: Mapping[str, ToolContextContract],
    evidence_store: RunEvidenceStore | None = None,
    enabled: bool,
    initial_round: bool,
) -> RuntimeContextProjection:
    """Remove optional blocks proved unnecessary for one intermediate step.

    The first execution round and every final-response round retain the full
    canonical context. Projection is enabled only when a validated TaskSpec
    exists, and only host-labelled optional blocks declared by tool contracts
    are eligible for removal.
    """

    rows = tuple(messages)
    if not enabled or initial_round or not visible_tool_names:
        return RuntimeContextProjection(rows)
    current_contracts = tuple(
        contracts[name]
        for name in visible_tool_names
        if name in contracts
    )
    if len(current_contracts) != len(visible_tool_names):
        return RuntimeContextProjection(rows)
    optional_blocks = {
        block
        for contract in contracts.values()
        for block in contract.required_context_blocks
    }
    required_blocks = {
        block
        for contract in current_contracts
        for block in contract.required_context_blocks
    }
    removable = optional_blocks - required_blocks
    selected_context = tuple(
        message
        for message in rows
        if str(message.attributes.get("context_name") or "") not in removable
    )
    required_result_tools = {
        dependency
        for contract in current_contracts
        for dependency in contract.prerequisite_tools
    }
    selected: list[AgentMessage] = []
    compacted: list[str] = []
    for message in selected_context:
        if (
            evidence_store is None
            or message.role is not MessageRole.TOOL
            or not message.tool_call_id
        ):
            selected.append(message)
            continue
        receipt = next(
            (
                item
                for item in evidence_store.tool_result_receipts()
                if item.tool_call_id == message.tool_call_id
            ),
            None,
        )
        producer_contract = (
            contracts.get(receipt.tool_name)
            if receipt is not None
            else None
        )
        replacement = (
            evidence_store.receipt_message(message)
            if (
                receipt is not None
                and receipt.tool_name not in required_result_tools
                and producer_contract is not None
                and producer_contract.result_projection
                is ToolResultProjection.RECEIPT
            )
            else None
        )
        if (
            replacement is not None
            and estimate_agent_messages_tokens((replacement,))
            < estimate_agent_messages_tokens((message,))
        ):
            selected.append(replacement)
            compacted.append(receipt.evidence_id)
        else:
            selected.append(message)
    selected_rows = tuple(selected)
    if selected_rows == rows:
        return RuntimeContextProjection(rows)
    return RuntimeContextProjection(
        messages=selected_rows,
        dropped_context_blocks=tuple(sorted(removable)),
        compacted_tool_results=tuple(compacted),
        saved_tokens=max(
            0,
            estimate_agent_messages_tokens(rows)
            - estimate_agent_messages_tokens(selected_rows),
        ),
    )


__all__ = ["RuntimeContextProjection", "project_intermediate_tool_context"]
