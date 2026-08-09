"""Business-neutral validation for declarative Planner constraints."""

from __future__ import annotations

from collections.abc import Sequence

from agent_core.contracts import (
    AgentAssignmentCoverage,
    StepExecutor,
    TaskPlan,
)


def agent_assignment_coverage_violations(
    plan: TaskPlan,
    coverages: Sequence[AgentAssignmentCoverage],
) -> tuple[str, ...]:
    """Return stable violations without interpreting assignment values."""

    violations: list[str] = []
    for coverage in coverages:
        role_steps = tuple(
            step
            for step in plan.steps
            if step.executor is StepExecutor.AGENT
            and step.agent_role == coverage.agent_role
        )
        if coverage.root_only and any(step.depends_on for step in role_steps):
            violations.append(
                f"all {coverage.agent_role} steps covered by "
                f"assignment.{coverage.assignment_field} must be "
                "dependency-free"
            )
            continue

        actual: list[str] = []
        malformed_step_ids: list[str] = []
        for step in role_steps:
            raw_values = step.assignment.get(coverage.assignment_field)
            if (
                not isinstance(raw_values, Sequence)
                or isinstance(raw_values, (str, bytes, bytearray))
            ):
                malformed_step_ids.append(step.id)
                continue
            values = [str(value or "").strip() for value in raw_values]
            if not values or any(not value for value in values):
                malformed_step_ids.append(step.id)
                continue
            actual.extend(values)
        if malformed_step_ids:
            violations.append(
                f"Agent steps {', '.join(malformed_step_ids)} must provide a "
                f"non-empty assignment.{coverage.assignment_field} array"
            )
            continue
        expected = list(coverage.required_values)
        if actual != expected:
            violations.append(
                f"{coverage.agent_role} steps must partition "
                f"assignment.{coverage.assignment_field} exactly once and in "
                f"this order: {', '.join(expected)}; received: "
                + (", ".join(actual) if actual else "none")
            )
    return tuple(violations)


__all__ = ["agent_assignment_coverage_violations"]
