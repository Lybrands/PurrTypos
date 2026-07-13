"""Fail-closed startup validation for Core tool registrations."""

from __future__ import annotations

import inspect
import json
from dataclasses import dataclass
from typing import Iterable

from agent_core.contracts import ToolPolicy
from agent_core.errors import ContractViolationError
from agent_core.json_values import thaw_json_mapping
from agent_core.ports import ToolRegistration


@dataclass(frozen=True, slots=True)
class ToolContractReport:
    """Stable registration diagnostics produced before a catalog is used."""

    registered_names: frozenset[str]
    duplicate_names: frozenset[str] = frozenset()
    violations: tuple[str, ...] = ()

    @property
    def is_valid(self) -> bool:
        return not self.duplicate_names and not self.violations

    @property
    def tool_count(self) -> int:
        return len(self.registered_names)

    def describe_violations(self) -> str:
        rows = list(self.violations)
        if self.duplicate_names:
            rows.append(
                "duplicate tool names: " + ", ".join(sorted(self.duplicate_names))
            )
        return "; ".join(rows) or "tool contract is valid"


def inspect_tool_contract(
    registrations: Iterable[ToolRegistration],
) -> ToolContractReport:
    """Inspect a detached registration snapshot without importing adapters."""

    items = tuple(registrations)
    names = tuple(str(item.schema.name or "").strip() for item in items)
    duplicate_names = frozenset(
        name for name in names if name and names.count(name) > 1
    )
    violations: list[str] = []

    for index, registration in enumerate(items):
        name = names[index]
        label = name or f"registration[{index}]"
        if not name:
            violations.append(f"{label}: tool name is required")
        if not str(registration.schema.description or "").strip():
            violations.append(f"{label}: tool description is required")
        if not isinstance(registration.policy, ToolPolicy):
            violations.append(f"{label}: explicit tool policy is required")

        parameters = thaw_json_mapping(registration.schema.parameters)
        if parameters.get("type") != "object":
            violations.append(f"{label}: parameters schema type must be object")
        properties = parameters.get("properties")
        if properties is not None and not isinstance(properties, dict):
            violations.append(f"{label}: schema properties must be an object")
        try:
            json.dumps(parameters, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as error:
            violations.append(
                f"{label}: parameters schema is not JSON serializable "
                f"({type(error).__name__})"
            )

        if not _is_async_callable(registration.handler):
            violations.append(f"{label}: handler must be async callable")
        if (
            registration.scope_validator is not None
            and not _is_async_callable(registration.scope_validator)
        ):
            violations.append(f"{label}: scope validator must be async callable")
        if registration.cache_probe is not None:
            probe = getattr(registration.cache_probe, "will_hit", None)
            if probe is None or not callable(probe) or inspect.iscoroutinefunction(probe):
                violations.append(f"{label}: cache probe will_hit must be synchronous")

    return ToolContractReport(
        registered_names=frozenset(name for name in names if name),
        duplicate_names=duplicate_names,
        violations=tuple(violations),
    )


def validate_tool_contract(
    registrations: Iterable[ToolRegistration],
) -> tuple[ToolRegistration, ...]:
    """Return a validated immutable snapshot or stop startup."""

    snapshot = tuple(registrations)
    report = inspect_tool_contract(snapshot)
    if not report.is_valid:
        raise ContractViolationError(report.describe_violations())
    return snapshot


def _is_async_callable(value: object) -> bool:
    return inspect.iscoroutinefunction(value) or inspect.iscoroutinefunction(
        getattr(value, "__call__", None)
    )
