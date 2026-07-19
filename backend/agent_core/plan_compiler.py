"""Deterministically expand planner actions from host-owned tool contracts."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Sequence

from agent_core.contracts import (
    PlanningConstraints,
    StepExecutor,
    StepStatus,
    StepType,
    TaskPlan,
    TaskStep,
    ToolExecutionMode,
)
from agent_core.errors import ContractViolationError
from agent_core.ports import ToolRegistration


@dataclass(frozen=True, slots=True)
class CompiledTaskPlan:
    plan: TaskPlan
    inserted_tool_names: tuple[str, ...] = ()


def compile_task_plan(
    plan: TaskPlan,
    registrations: Sequence[ToolRegistration],
    *,
    constraints: PlanningConstraints = PlanningConstraints(),
    satisfied_tool_names: frozenset[str] = frozenset(),
) -> CompiledTaskPlan:
    """Insert missing prerequisite tools without trusting planner ordering.

    Explicit planner steps are preserved.  Only prerequisite steps absent from
    earlier execution are synthesized, and every synthesized step remains
    subject to the normal request scope and runtime authorization checks.
    """

    by_name = {item.schema.name: item for item in registrations}
    existing_ids = {step.id for step in plan.steps}
    completed = set(satisfied_tool_names)
    completed.update(constraints.context_satisfied_tool_names)
    inserted: list[str] = []
    expanded: list[TaskStep] = []
    sequence = 0

    def append_prerequisites(tool_name: str, path: tuple[str, ...]) -> None:
        nonlocal sequence
        if tool_name in path:
            raise ContractViolationError(
                "tool context contract contains a prerequisite cycle: "
                + " -> ".join((*path, tool_name))
            )
        registration = by_name.get(tool_name)
        if registration is None:
            raise ContractViolationError(
                f"tool context contract names unavailable tool {tool_name!r}"
            )
        for dependency in registration.prerequisite_tools:
            if dependency in completed or (
                tool_name,
                dependency,
            ) in constraints.satisfied_tool_dependency_edges:
                continue
            if dependency in constraints.planning_excluded_tool_names:
                raise ContractViolationError(
                    f"tool {tool_name!r} requires planning-excluded tool "
                    f"{dependency!r}"
                )
            append_prerequisites(dependency, (*path, tool_name))
            if dependency in completed:
                continue
            dependency_registration = by_name[dependency]
            sequence += 1
            step_id = _unique_step_id(
                f"host-prerequisite-{dependency}-{sequence}",
                existing_ids,
            )
            existing_ids.add(step_id)
            expanded.append(TaskStep(
                id=step_id,
                title=f"Prepare {dependency}",
                type=(
                    StepType.READ
                    if dependency_registration.policy.mode is ToolExecutionMode.READ
                    else StepType.ANALYZE
                ),
                executor=StepExecutor.TOOL,
                status=StepStatus.PENDING,
                risk_level=dependency_registration.policy.risk_level,
                suggested_tools=(dependency,),
                description=(
                    f"Host-inserted prerequisite for {tool_name}; derived from "
                    "the registered tool context contract."
                ),
            ))
            completed.add(dependency)
            inserted.append(dependency)

    for step in plan.steps:
        if step.executor is StepExecutor.TOOL:
            if len(step.suggested_tools) != 1:
                raise ContractViolationError(
                    "host plan compilation requires exactly one tool per step"
                )
            tool_name = step.suggested_tools[0]
            append_prerequisites(tool_name, ())
            completed.add(tool_name)
        expanded.append(step)

    return CompiledTaskPlan(
        plan=replace(plan, steps=tuple(expanded)),
        inserted_tool_names=tuple(inserted),
    )


def _unique_step_id(candidate: str, existing: set[str]) -> str:
    normalized = candidate[:48]
    if normalized not in existing:
        return normalized
    index = 2
    while True:
        suffix = f"-{index}"
        value = normalized[: 48 - len(suffix)] + suffix
        if value not in existing:
            return value
        index += 1


__all__ = ["CompiledTaskPlan", "compile_task_plan"]
