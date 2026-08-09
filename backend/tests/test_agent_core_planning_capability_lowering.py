from __future__ import annotations

from agent_core.contracts import (
    PlanningConstraints,
    StepExecutor,
    StepStatus,
    StepType,
    TaskPlan,
    TaskStep,
    ToolContextContract,
    ToolExecutionMode,
    ToolHandlerResult,
    ToolPolicy,
    ToolRiskLevel,
    ToolSchema,
)
from agent_core.plan_compiler import (
    compile_task_plan,
    project_completed_steps_for_planning,
    projected_planning_tool_names,
    runtime_tool_names_for_planning_names,
)
from agent_core.ports import ToolRegistration


async def _handler(state, arguments, signal=None):
    del state, arguments, signal
    return ToolHandlerResult(content="{}")


def _schema(name: str, description: str = "runtime") -> ToolSchema:
    return ToolSchema(
        name=name,
        description=description,
        parameters={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    )


def _registrations() -> tuple[ToolRegistration, ...]:
    capability = _schema(
        "generateDeliverable",
        "Generate the current business deliverable.",
    )
    names = ("beginArtifact", "appendBatch", "finalizeRevision")
    dependencies = {
        "beginArtifact": (),
        "appendBatch": ("beginArtifact",),
        "finalizeRevision": ("appendBatch",),
    }
    return tuple(
        ToolRegistration(
            schema=_schema(name),
            handler=_handler,
            policy=ToolPolicy(
                ToolExecutionMode.PROPOSE,
                name,
                ToolRiskLevel.WRITE,
            ),
            context_contract=ToolContextContract(
                prerequisite_tools=dependencies[name],
            ),
            planning_capability=capability,
        )
        for name in names
    )


def _plan(tool_name: str) -> TaskPlan:
    return TaskPlan(
        title="Generate",
        steps=(TaskStep(
            id="generate",
            title="Generate deliverable",
            type=StepType.WRITE,
            executor=StepExecutor.TOOL,
            risk_level=ToolRiskLevel.WRITE,
            suggested_tools=(tool_name,),
        ),),
    )


def test_planner_sees_one_business_capability_for_private_tool_chain():
    registrations = _registrations()
    enabled = frozenset(item.schema.name for item in registrations)

    assert projected_planning_tool_names(
        registrations,
        enabled,
    ) == frozenset({"generateDeliverable"})
    assert runtime_tool_names_for_planning_names(
        registrations,
        enabled,
        frozenset({"generateDeliverable"}),
    ) == enabled


def test_business_capability_lowers_to_private_runtime_protocol():
    registrations = _registrations()
    enabled = frozenset(item.schema.name for item in registrations)

    compiled = compile_task_plan(
        _plan("generateDeliverable"),
        registrations,
        enabled_tool_names=enabled,
    )

    assert [step.suggested_tools[0] for step in compiled.plan.steps] == [
        "beginArtifact",
        "appendBatch",
        "finalizeRevision",
    ]
    assert [step.protocol_private for step in compiled.plan.steps] == [
        True,
        True,
        False,
    ]
    assert compiled.plan.steps[-1].id == "generate"
    assert all(
        step.planning_capability == "generateDeliverable"
        for step in compiled.plan.steps
    )
    assert compiled.lowered_tool_names == (
        "beginArtifact",
        "appendBatch",
        "finalizeRevision",
    )


def test_lowering_resumes_after_host_confirmed_private_progress():
    registrations = _registrations()
    enabled = frozenset(item.schema.name for item in registrations)
    constraints = PlanningConstraints(
        execution_satisfied_tool_names=frozenset({
            "beginArtifact",
            "appendBatch",
        }),
    )

    compiled = compile_task_plan(
        _plan("generateDeliverable"),
        registrations,
        constraints=constraints,
        enabled_tool_names=enabled,
    )

    assert len(compiled.plan.steps) == 1
    assert compiled.plan.steps[0].suggested_tools == ("finalizeRevision",)
    assert compiled.plan.steps[0].protocol_private is False


def test_legacy_runtime_tool_plan_remains_executable_without_rewriting():
    registrations = _registrations()
    enabled = frozenset(item.schema.name for item in registrations)

    compiled = compile_task_plan(
        _plan("finalizeRevision"),
        registrations,
        constraints=PlanningConstraints(
            execution_satisfied_tool_names=frozenset({
                "beginArtifact",
                "appendBatch",
            }),
        ),
        enabled_tool_names=enabled,
    )

    assert compiled.plan.steps == _plan("finalizeRevision").steps
    assert compiled.lowered_tool_names == ()


def test_dynamic_planner_receives_only_completed_business_capability():
    registrations = _registrations()
    enabled = frozenset(item.schema.name for item in registrations)
    compiled = compile_task_plan(
        _plan("generateDeliverable"),
        registrations,
        enabled_tool_names=enabled,
    )
    completed = tuple(
        TaskStep(
            id=step.id,
            title=step.title,
            type=step.type,
            executor=step.executor,
            status=StepStatus.DONE,
            risk_level=step.risk_level,
            suggested_tools=step.suggested_tools,
            depends_on=step.depends_on,
            protocol_private=step.protocol_private,
            planning_capability=step.planning_capability,
        )
        for step in compiled.plan.steps
    )

    projected = project_completed_steps_for_planning(
        completed,
        registrations,
        enabled,
    )

    assert len(projected) == 1
    assert projected[0].suggested_tools == ("generateDeliverable",)
    assert projected[0].status is StepStatus.DONE
