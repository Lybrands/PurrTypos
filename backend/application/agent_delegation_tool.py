"""Request-scoped host tool that runs bounded role-scoped child Agents."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from agent_core.contracts import (
    AgentRunResult,
    ExecutionState,
    RunLineage,
    ToolExecutionMode,
    ToolHandlerResult,
    ToolPlanningDisposition,
    ToolPolicy,
    ToolRiskLevel,
    ToolSchema,
)
from agent_core.errors import ContractViolationError
from agent_core.events import AgentEvent, CoreEventType
from agent_core.ports import CancellationSignal, ToolRegistration
from application.agent_delegation_service import AgentDelegationService
from application.agent_orchestrator import AgentOrchestrator
from domains.agent_roles import AgentRoleRegistry


ChildRunner = Callable[[dict, RunLineage], Awaitable[AgentRunResult]]
EventPublisher = Callable[[AgentEvent], Awaitable[None]]

_MAX_DELEGATIONS_PER_CALL = 3


def build_delegation_tool_registration(
    *,
    service: AgentDelegationService,
    role_registry: AgentRoleRegistry,
    worker_id: str,
    runner: ChildRunner,
    publish: EventPublisher,
    max_parallel_children: int = 3,
) -> ToolRegistration:
    """Create the parent-only delegation tool for one live chat request."""

    async def handle(
        state: ExecutionState,
        arguments: dict[str, Any],
        signal: CancellationSignal | None = None,
    ) -> ToolHandlerResult:
        parent_run_id = str(state.run_id or "").strip()
        if not parent_run_id:
            raise ContractViolationError("delegation tool requires a bound parent run")
        raw_items = arguments.get("delegations")
        if (
            not isinstance(raw_items, Sequence)
            or isinstance(raw_items, (str, bytes, bytearray))
            or not raw_items
        ):
            return ToolHandlerResult(
                '{"error":"delegations must contain at least one task"}',
                error_code="invalid_delegation_request",
            )
        if len(raw_items) > _MAX_DELEGATIONS_PER_CALL:
            return ToolHandlerResult(
                '{"error":"too many delegations"}',
                error_code="delegation_batch_too_large",
            )

        created: list[dict[str, Any]] = []
        for raw in raw_items:
            if not isinstance(raw, Mapping):
                return ToolHandlerResult(
                    '{"error":"delegation task must be an object"}',
                    error_code="invalid_delegation_request",
                )
            role = str(raw.get("agentRole") or "").strip()
            objective = str(raw.get("objective") or "").strip()
            if role_registry.get(role) is None or not objective:
                return ToolHandlerResult(
                    '{"error":"unsupported agent role or empty objective"}',
                    error_code="invalid_delegation_request",
                )
            view = await service.delegate(
                parent_run_id=parent_run_id,
                agent_role=role,
                objective=objective,
                input_payload=(
                    dict(raw.get("input"))
                    if isinstance(raw.get("input"), Mapping)
                    else {}
                ),
                required=bool(raw.get("required", True)),
                priority=int(raw.get("priority") or 0),
            )
            created.append(view)
            await publish(AgentEvent(
                type=CoreEventType.DELEGATION_CREATED,
                run_id=parent_run_id,
                payload=view,
            ))

        async def observe(event_type: CoreEventType, payload: dict) -> None:
            await publish(AgentEvent(
                type=event_type,
                run_id=parent_run_id,
                payload=payload,
            ))

        snapshot = await AgentOrchestrator(service).run_queued(
            parent_run_id=parent_run_id,
            # The child Run repository attaches lineage under this same lease
            # owner, so the delegation claim and child creation must agree.
            worker_id=worker_id,
            max_parallel_children=min(
                max(1, int(max_parallel_children)),
                len(created),
            ),
            runner=runner,
            observer=observe,
            signal=signal,
        )
        aggregate = snapshot["aggregate"]
        content = json.dumps(
            {
                "state": aggregate["state"],
                "counts": aggregate["counts"],
                "requiredFailures": aggregate["requiredFailures"],
                "results": aggregate["results"],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return ToolHandlerResult(
            content,
            error_code=(
                "required_subagent_failed"
                if aggregate["state"] == "blocked"
                else None
            ),
            # Child results are new semantic evidence whose contents can make
            # tentative parent steps unnecessary or select a different branch.
            # Unlike ordinary reads/appends, delegation therefore opts into a
            # single dynamic plan revision explicitly.
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
                "and wait for their results. Use this "
                "when parallel independent perspectives materially improve the "
                "answer; do not delegate simple sequential work. Available "
                "product roles: "
                + "; ".join(
                    f"{item.id} ({item.delegation_description})"
                    for item in role_registry.definitions
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
                                    "enum": sorted(role_registry.role_ids),
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
        # Delegations and child lifecycle transitions are already durable in
        # the host repository. The parent must not hold Core's SQLite receipt
        # transaction while concurrently waiting for child Runs to write.
        host_managed_durability=True,
    )
