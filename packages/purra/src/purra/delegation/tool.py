"""Generic delegation tool backed by the canonical coordinator."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from typing import Any

from purra.cancellation import OperationCanceled, await_with_cancellation
from purra.contracts import (
    ExecutionState,
    ToolExecutionMode,
    ToolHandlerResult,
    ToolPlanningDisposition,
    ToolPolicy,
    ToolRiskLevel,
    ToolSchema,
)
from purra.errors import ContractViolationError
from purra.ports import CancellationSignal, ToolRegistration
from purra.delegation.coordinator import AgentDelegationCoordinator


_MAX_DELEGATIONS_PER_CALL = 3


def build_delegation_tool_registration(
    coordinator: AgentDelegationCoordinator,
    *,
    role_guidance: Mapping[str, Mapping[str, str]],
) -> ToolRegistration:
    if not isinstance(coordinator, AgentDelegationCoordinator):
        raise TypeError("delegation tool requires the canonical coordinator")
    roles = {
        str(role_id).strip(): {
            "title": str(value.get("title") or role_id).strip(),
            "description": str(value.get("description") or "").strip(),
        }
        for role_id, value in role_guidance.items()
        if str(role_id).strip()
    }
    if not roles:
        raise ValueError("delegation tool requires at least one agent role")

    async def handle(
        state: ExecutionState,
        arguments: dict[str, Any],
        signal: CancellationSignal | None = None,
    ) -> ToolHandlerResult:
        parent_run_id = str(state.run_id or "").strip()
        if not parent_run_id:
            raise ContractViolationError(
                "delegation tool requires a bound parent run"
            )
        items = _validated_items(arguments, roles)
        created = [
            await coordinator.create(
                parent_run_id=parent_run_id,
                agent_role=item["agentRole"],
                objective=item["objective"],
                input_payload=item["input"],
                required=item["required"],
                priority=item["priority"],
            )
            for item in items
        ]

        async def run_children() -> None:
            handles = await asyncio.gather(*(
                coordinator.claim_and_submit(
                    delegation.id,
                    parent_run_id=parent_run_id,
                )
                for delegation in created
            ))
            await asyncio.gather(*(handle.wait() for handle in handles))

        try:
            await await_with_cancellation(run_children(), signal)
        except OperationCanceled:
            await coordinator.cancel_children(parent_run_id)
            raise

        aggregate = await coordinator.aggregate(parent_run_id)
        return ToolHandlerResult(
            json.dumps(
                {
                    "state": aggregate.state,
                    "counts": dict(aggregate.counts),
                    "requiredFailures": list(aggregate.required_failures),
                    "results": [dict(item) for item in aggregate.results],
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            error_code=(
                "required_subagent_failed"
                if aggregate.state == "blocked"
                else None
            ),
            planning_disposition=ToolPlanningDisposition.REPLAN,
        )

    return ToolRegistration(
        schema=ToolSchema(
            name="delegateToAgents",
            display_names={
                "zh-CN": "委派子 Agent 协作",
                "en-US": "Delegate to Sub-agents",
            },
            description=(
                "Delegate 1-3 independent tasks to role-scoped child agents "
                "and wait for their results. Available roles: "
                + "; ".join(
                    f"{role_id} ({value['description']})"
                    for role_id, value in roles.items()
                )
            ),
            parameters={
                "type": "object",
                "properties": {
                    "delegations": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": _MAX_DELEGATIONS_PER_CALL,
                        "items": {
                            "type": "object",
                            "properties": {
                                "agentRole": {
                                    "type": "string",
                                    "enum": sorted(roles),
                                },
                                "objective": {"type": "string"},
                                "input": {"type": "object"},
                                "required": {"type": "boolean"},
                                "priority": {"type": "integer"},
                            },
                            "required": ["agentRole", "objective"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["delegations"],
                "additionalProperties": False,
            },
        ),
        handler=handle,
        policy=ToolPolicy(
            mode=ToolExecutionMode.PROPOSE,
            title="调用子 Agent",
            risk_level=ToolRiskLevel.WRITE,
        ),
        host_managed_durability=True,
    )


def _validated_items(
    arguments: Mapping[str, Any],
    roles: Mapping[str, Mapping[str, str]],
) -> list[dict[str, Any]]:
    raw_items = arguments.get("delegations")
    if (
        not isinstance(raw_items, Sequence)
        or isinstance(raw_items, (str, bytes, bytearray))
        or not 1 <= len(raw_items) <= _MAX_DELEGATIONS_PER_CALL
    ):
        raise ContractViolationError(
            "delegations must contain between one and three tasks"
        )
    items: list[dict[str, Any]] = []
    for raw in raw_items:
        if not isinstance(raw, Mapping):
            raise ContractViolationError("delegation task must be an object")
        role = str(raw.get("agentRole") or "").strip()
        objective = str(raw.get("objective") or "").strip()
        if role not in roles or not objective:
            raise ContractViolationError(
                "delegation task has an unsupported role or empty objective"
            )
        items.append({
            "agentRole": role,
            "objective": objective,
            "input": (
                dict(raw.get("input"))
                if isinstance(raw.get("input"), Mapping)
                else {}
            ),
            "required": bool(raw.get("required", True)),
            "priority": int(raw.get("priority") or 0),
        })
    return items


__all__ = ["build_delegation_tool_registration"]
