"""Validation hook between task-plan steps and future executors."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from services.task_planner_types import TaskStepExecutor, TaskStepSpec, parse_task_step


class TaskStepValidationError(ValueError):
    """Raised when a task-plan step cannot be mapped to a known executor."""


@dataclass(frozen=True)
class TaskStepExecutionHook:
    step: TaskStepSpec
    executor: TaskStepExecutor
    allowed_tools: set[str] = field(default_factory=set)


def validate_task_step_executor(step: TaskStepSpec | dict[str, Any]) -> TaskStepExecutionHook:
    """Validate one task-plan step and expose tool allow-list metadata."""

    spec = step if isinstance(step, TaskStepSpec) else _parse_step(step)
    return TaskStepExecutionHook(
        step=spec,
        executor=spec.executor,
        allowed_tools=set(spec.suggested_tools) if spec.executor == "tool" else set(),
    )


def validate_task_plan_steps(steps: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> list[TaskStepExecutionHook]:
    """Validate all steps in their declared order.

    Keeping this function order-preserving makes the current phase sequential by
    construction; any future parallel runner should be a separate explicit layer.
    """

    return [validate_task_step_executor(step) for step in steps]


def _parse_step(raw: dict[str, Any]) -> TaskStepSpec:
    try:
        return parse_task_step(raw)
    except ValueError as exc:
        raise TaskStepValidationError(str(exc)) from exc
