"""Application-level composition of generic Planner constraints."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import replace

from purra.contracts import (
    AgentRunRequest,
    PlanningCapabilities,
    PlanningConstraints,
    TaskSpec,
)
from purra.ports import WorkPlanningConstraintProvider


class RequiredToolPlanningPolicy:
    """Narrow an injected policy with required registered capabilities."""

    def __init__(self, delegate, required_tool_names: Collection[str]) -> None:
        self._delegate = delegate
        self._required_tool_names = frozenset(
            str(name).strip()
            for name in required_tool_names
            if str(name).strip()
        )

    def should_plan(
        self,
        request: AgentRunRequest,
        capabilities: PlanningCapabilities,
    ) -> bool:
        return self._delegate.should_plan(request, capabilities)

    def planning_constraints(
        self,
        request: AgentRunRequest,
        capabilities: PlanningCapabilities,
    ) -> PlanningConstraints:
        return self._require(self._delegate.planning_constraints(
            request,
            capabilities,
        ))

    def planning_constraints_for_task(
        self,
        request: AgentRunRequest,
        capabilities: PlanningCapabilities,
        task_spec: TaskSpec,
    ) -> PlanningConstraints:
        base = (
            self._delegate.planning_constraints_for_task(
                request,
                capabilities,
                task_spec,
            )
            if isinstance(
                self._delegate,
                WorkPlanningConstraintProvider,
            )
            else capabilities.constraints
        )
        return self._require(base)

    def _require(self, constraints: PlanningConstraints) -> PlanningConstraints:
        return replace(
            constraints,
            required_any_tool_names=(
                constraints.required_any_tool_names
                | self._required_tool_names
            ),
            allow_model_only_fallback=False,
        )


__all__ = ["RequiredToolPlanningPolicy"]
