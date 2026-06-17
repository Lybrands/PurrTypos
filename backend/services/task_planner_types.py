"""Shared task-plan step types for planner and future executors."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


TaskStepExecutor = Literal["model", "tool"]
TaskStepType = Literal["read", "analyze", "write", "review", "confirm"]
TaskStepStatus = Literal["pending", "running", "done", "blocked", "failed"]
TaskRiskLevel = Literal["read", "write", "destructive"]

VALID_EXECUTORS = {"model", "tool"}
VALID_STEP_TYPES = {"read", "analyze", "write", "review", "confirm"}
VALID_STEP_STATUSES = {"pending", "running", "done", "blocked", "failed"}
VALID_RISK_LEVELS = {"read", "write", "destructive"}


@dataclass(frozen=True)
class TaskStepSpec:
    id: str
    title: str
    type: TaskStepType
    status: TaskStepStatus
    executor: TaskStepExecutor
    description: str | None = None
    risk_level: TaskRiskLevel | None = None
    suggested_tools: tuple[str, ...] = ()
    result_summary: str | None = None
    error: str | None = None


def parse_task_step(raw: dict[str, Any]) -> TaskStepSpec:
    """Normalize frontend/planner step dictionaries into an internal spec."""

    step_id = _required_str(raw, "id")
    title = _required_str(raw, "title")
    step_type = _one_of(_required_str(raw, "type"), VALID_STEP_TYPES, "type")
    status = _one_of(raw.get("status") or "pending", VALID_STEP_STATUSES, "status")
    executor = _one_of(
        raw.get("executor") or _default_executor_for_type(step_type),
        VALID_EXECUTORS,
        "executor",
    )
    risk_level_raw = raw.get("riskLevel", raw.get("risk_level"))
    risk_level = (
        _one_of(risk_level_raw, VALID_RISK_LEVELS, "riskLevel")
        if risk_level_raw
        else None
    )
    suggested_tools_raw = raw.get("suggestedTools", raw.get("suggested_tools")) or []
    if not isinstance(suggested_tools_raw, list):
        raise ValueError("suggestedTools must be a list")

    return TaskStepSpec(
        id=step_id,
        title=title,
        description=_optional_str(raw.get("description")),
        type=step_type,  # type: ignore[arg-type]
        status=status,  # type: ignore[arg-type]
        risk_level=risk_level,  # type: ignore[arg-type]
        suggested_tools=tuple(
            tool for tool in (_optional_str(item) for item in suggested_tools_raw) if tool
        ),
        executor=executor,  # type: ignore[arg-type]
        result_summary=_optional_str(raw.get("resultSummary", raw.get("result_summary"))),
        error=_optional_str(raw.get("error")),
    )


def _default_executor_for_type(step_type: str) -> str:
    if step_type == "read":
        return "tool"
    return "model"


def _required_str(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} is required")
    return value.strip()


def _optional_str(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _one_of(value: Any, allowed: set[str], field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    normalized = value.strip()
    if normalized not in allowed:
        raise ValueError(f"{field} must be one of {sorted(allowed)}")
    return normalized
