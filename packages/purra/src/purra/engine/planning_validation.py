"""Host-owned validation for plans and planning constraints."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from purra.contracts import (
    AgentRunRequest,
    PlanningCapabilities,
    PlanningConstraints,
    StepExecutor,
    StepType,
    TaskPlan,
)
from purra.errors import ContractViolationError
from purra.plan_constraints import agent_assignment_coverage_violations
from purra.ports import ToolCatalog, ToolRegistration


def effective_registrations(
    catalog: ToolCatalog,
    registrations: Sequence[ToolRegistration],
    request: AgentRunRequest,
    *,
    model_supports_tools: bool,
) -> tuple[tuple[ToolRegistration, ...], frozenset[str]]:
    registrations = tuple(registrations)
    names = [registration.schema.name for registration in registrations]
    if len(names) != len(set(names)):
        raise ContractViolationError("tool catalog contains duplicate names")
    registered_names = frozenset(names)
    if not request.tools_enabled or not model_supports_tools:
        return registrations, frozenset()
    enabled = frozenset(catalog.enabled_names(request))
    unknown = enabled - registered_names
    if unknown:
        raise ContractViolationError(
            "tool catalog enabled unregistered names: "
            + ", ".join(sorted(unknown))
        )
    return registrations, enabled


def validate_plan_authority(
    plan: TaskPlan,
    enabled_names: frozenset[str],
    *,
    available_agent_roles: frozenset[str] = frozenset(),
    constraints: PlanningConstraints = PlanningConstraints(),
    max_tool_steps: int,
) -> None:
    tool_step_count = 0
    for step in plan.steps:
        if step.executor in constraints.planning_excluded_executors:
            raise ContractViolationError(
                "plan selects an executor excluded by the request: "
                + step.executor.value
            )
        if step.type is StepType.CONFIRM:
            raise ContractViolationError(
                "approval belongs to tool policy, not a plan step"
            )
        if step.executor is StepExecutor.MODEL and step.suggested_tools:
            raise ContractViolationError("model plan steps cannot grant tools")
        if step.executor is StepExecutor.AGENT:
            if step.agent_role is None:
                raise ContractViolationError(
                    "agent plan step requires an agent role"
                )
            if step.agent_role in constraints.planning_excluded_agent_roles:
                raise ContractViolationError(
                    "plan selects an Agent role excluded by the request: "
                    + step.agent_role
                )
            if step.agent_role not in available_agent_roles:
                raise ContractViolationError(
                    "plan selects an Agent role outside request scope: "
                    + step.agent_role
                )
            if step.suggested_tools:
                raise ContractViolationError(
                    "agent plan steps cannot grant tools"
                )
        if step.executor is StepExecutor.TOOL:
            if len(step.suggested_tools) != 1:
                raise ContractViolationError(
                    "tool plan step must grant exactly one expected tool"
                )
            tool_step_count += 1
            unknown = frozenset(step.suggested_tools) - enabled_names
            if unknown:
                raise ContractViolationError(
                    "plan grants tools outside request scope: "
                    + ", ".join(sorted(unknown))
                )
            satisfied = (
                frozenset(step.suggested_tools)
                & constraints.context_satisfied_tool_names
            )
            if satisfied:
                raise ContractViolationError(
                    "plan redundantly grants tools already satisfied by context: "
                    + ", ".join(sorted(satisfied))
                )
            planning_excluded = (
                frozenset(step.suggested_tools)
                & constraints.planning_excluded_tool_names
            )
            if planning_excluded:
                raise ContractViolationError(
                    "plan grants tools excluded by the request's planning scope: "
                    + ", ".join(sorted(planning_excluded))
                )
    if tool_step_count > max_tool_steps:
        raise ContractViolationError(
            f"plan requires {tool_step_count} tool rounds but runtime permits "
            f"at most {max_tool_steps} while reserving correction and final "
            "response rounds"
        )
    assignment_violations = agent_assignment_coverage_violations(
        plan,
        constraints.agent_assignment_coverages,
    )
    if assignment_violations:
        raise ContractViolationError("; ".join(assignment_violations))


def validate_planning_constraints(
    capabilities: PlanningCapabilities,
    constraints: PlanningConstraints,
    *,
    runtime_tool_names: frozenset[str] | None = None,
) -> None:
    if not isinstance(constraints, PlanningConstraints):
        raise ContractViolationError(
            "planning policy must return PlanningConstraints"
        )
    unknown = (
        constraints.context_satisfied_tool_names
        | constraints.planning_excluded_tool_names
        | constraints.required_any_tool_names
    ) - capabilities.available_tool_names
    if unknown:
        raise ContractViolationError(
            "planning constraints name unavailable tools: "
            + ", ".join(sorted(unknown))
        )
    unknown_execution = constraints.execution_satisfied_tool_names - (
        runtime_tool_names
        if runtime_tool_names is not None
        else capabilities.available_tool_names
    )
    if unknown_execution:
        raise ContractViolationError(
            "planning constraints name unavailable private runtime tools: "
            + ", ".join(sorted(unknown_execution))
        )
    unknown_roles = (
        constraints.planning_excluded_agent_roles
        | constraints.required_any_agent_roles
        | frozenset(
            coverage.agent_role
            for coverage in constraints.agent_assignment_coverages
        )
    ) - capabilities.available_agent_roles
    if unknown_roles:
        raise ContractViolationError(
            "planning constraints name unavailable Agent roles: "
            + ", ".join(sorted(unknown_roles))
        )
    unavailable_required_roles = (
        constraints.required_any_agent_roles
        & constraints.planning_excluded_agent_roles
    )
    if unavailable_required_roles:
        raise ContractViolationError(
            "required Agent roles must remain selectable: "
            + ", ".join(sorted(unavailable_required_roles))
        )
    if (
        constraints.required_any_agent_roles
        and StepExecutor.AGENT in constraints.planning_excluded_executors
    ):
        raise ContractViolationError(
            "required Agent roles need the Agent executor to remain selectable"
        )
    if (
        constraints.required_any_tool_names
        and StepExecutor.TOOL in constraints.planning_excluded_executors
    ):
        raise ContractViolationError(
            "required tools need the tool executor to remain selectable"
        )
    if constraints.minimum_root_agent_count > capabilities.max_parallel_agents:
        raise ContractViolationError(
            "minimum initial Agent frontier exceeds the host parallel limit"
        )
    if (
        constraints.minimum_root_agent_count > 0
        and not capabilities.available_agent_roles
    ):
        raise ContractViolationError(
            "minimum initial Agent frontier requires available Agent roles"
        )
    overlap = (
        constraints.context_satisfied_tool_names
        & constraints.planning_excluded_tool_names
    )
    if overlap:
        raise ContractViolationError(
            "planning constraints cannot mark tools both context-satisfied "
            "and planning-excluded: "
            + ", ".join(sorted(overlap))
        )
    unavailable_required = constraints.required_any_tool_names & (
        constraints.context_satisfied_tool_names
        | constraints.planning_excluded_tool_names
    )
    if unavailable_required:
        raise ContractViolationError(
            "required planning tools must remain selectable: "
            + ", ".join(sorted(unavailable_required))
        )
    for tool_name, dependency_name in sorted(
        constraints.satisfied_tool_dependency_edges
    ):
        unavailable = {
            tool_name,
            dependency_name,
        } - capabilities.available_tool_names
        if unavailable:
            raise ContractViolationError(
                "planning dependency waiver names unavailable tools: "
                + ", ".join(sorted(unavailable))
            )
        raw_guidance = capabilities.tool_guidance.get(tool_name)
        requires = (
            raw_guidance.get("requires")
            if isinstance(raw_guidance, Mapping)
            else None
        )
        declared_dependencies: set[str] = set()
        if (
            isinstance(requires, Sequence)
            and not isinstance(requires, (str, bytes, bytearray))
        ):
            declared_dependencies = {
                str(value).strip()
                for value in requires
                if str(value).strip()
            }
        if dependency_name not in declared_dependencies:
            raise ContractViolationError(
                "planning dependency waiver names an undeclared edge: "
                f"{tool_name} -> {dependency_name}"
            )
    effective_tools = (
        capabilities.available_tool_names
        - constraints.context_satisfied_tool_names
        - constraints.planning_excluded_tool_names
    )
    for tool_name in sorted(effective_tools):
        raw_guidance = capabilities.tool_guidance.get(tool_name)
        if not isinstance(raw_guidance, Mapping):
            continue
        requires = raw_guidance.get("requires")
        if not (
            isinstance(requires, Sequence)
            and not isinstance(requires, (str, bytes, bytearray))
        ):
            continue
        blocked_dependencies = {
            str(value).strip()
            for value in requires
            if str(value).strip()
            in constraints.planning_excluded_tool_names
            and (
                tool_name,
                str(value).strip(),
            ) not in constraints.satisfied_tool_dependency_edges
        }
        if blocked_dependencies:
            raise ContractViolationError(
                "planning constraints exclude dependencies still required by "
                f"available tool {tool_name}: "
                + ", ".join(sorted(blocked_dependencies))
            )


def validate_task_constraint_refinement(
    base: PlanningConstraints,
    refined: PlanningConstraints,
) -> None:
    if not isinstance(refined, PlanningConstraints):
        raise ContractViolationError(
            "task planning policy must return PlanningConstraints"
        )
    if (
        base.context_satisfied_tool_names
        - refined.context_satisfied_tool_names
        or base.planning_excluded_tool_names
        - refined.planning_excluded_tool_names
        or base.satisfied_tool_dependency_edges
        - refined.satisfied_tool_dependency_edges
        or base.required_any_tool_names - refined.required_any_tool_names
        or (
            base.execution_satisfied_tool_names
            - refined.execution_satisfied_tool_names
        )
        or (
            base.planning_excluded_agent_roles
            - refined.planning_excluded_agent_roles
        )
        or base.required_any_agent_roles - refined.required_any_agent_roles
        or any(
            coverage not in refined.agent_assignment_coverages
            for coverage in base.agent_assignment_coverages
        )
        or refined.minimum_root_agent_count < base.minimum_root_agent_count
        or (
            base.planning_excluded_executors
            - refined.planning_excluded_executors
        )
        or (
            not base.allow_model_only_fallback
            and refined.allow_model_only_fallback
        )
    ):
        raise ContractViolationError(
            "task planning constraints cannot weaken request constraints"
        )
