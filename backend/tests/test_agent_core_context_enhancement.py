from __future__ import annotations

import json

import pytest

from agent_core.contracts import (
    AgentMessage,
    AgentRunRequest,
    DomainContext,
    MessageRole,
    ModelRequest,
    PlanningCapabilities,
    PlanningConstraints,
    StepExecutor,
    StepType,
    TaskSpec,
    TaskPlan,
    TaskStep,
    ToolBatchOutcome,
    ToolBatchResult,
    ToolCall,
    ToolCallResult,
    ToolContextContract,
    ToolExecutionMode,
    ToolHandlerResult,
    ToolPolicy,
    ToolResultProjection,
    ToolSchema,
)
from agent_core.evidence import RunEvidenceStore, ToolResultReceipt
from agent_core.errors import ContractViolationError
from agent_core.events import AgentEvent, CoreEventType
from agent_core.plan_compiler import compile_task_plan
from agent_core.planner import (
    AgentPlanner,
    build_execution_message,
    build_planner_messages,
    normalize_task_plan,
)
from agent_core.ports import ToolRegistration
from agent_core.runtime_context import project_intermediate_tool_context
from tests.test_agent_core_planner import FakeModelGateway, _request
from agent_core.runtime import AgentRuntime
from tests.test_agent_core_runtime import (
    RecordingObserver,
    ScriptedModelGateway,
    ScriptedToolGateway,
    _answer,
    _batch,
    _matching_budget,
    _schema,
    _tool_call,
)


async def _handler(state, arguments, signal=None):
    del state, arguments, signal
    return ToolHandlerResult("{}")


def _registration(
    name: str,
    *,
    prerequisites: tuple[str, ...] = (),
) -> ToolRegistration:
    return ToolRegistration(
        schema=ToolSchema(
            name=name,
            description=name,
            parameters={"type": "object", "properties": {}},
        ),
        handler=_handler,
        policy=ToolPolicy(ToolExecutionMode.READ, name),
        context_contract=ToolContextContract(
            prerequisite_tools=prerequisites,
            mandatory_context_keys=("taskSpec",),
            produces=(f"tool.{name}.result",),
        ),
    )


@pytest.mark.asyncio
async def test_planner_accepts_semantic_task_spec_but_rejects_authority_fields():
    gateway = FakeModelGateway(json.dumps({
        "needsTodos": True,
        "title": "Rewrite",
        "goal": "rewrite ending",
        "taskSpec": {
            "goal": "rewrite the final paragraph",
            "target": {"type": "chapter", "range": "last_paragraph"},
            "operation": "write",
            "instruction": "be restrained",
            "constraints": ["do not reveal the father"],
            "preserve": ["umbrella foreshadowing"],
            "deliverable": "updated prose",
        },
        "todos": [{
            "id": "edit",
            "title": "Edit",
            "type": "write",
            "executor": "tool",
            "expectedTools": ["edit"],
        }],
    }))
    result = await AgentPlanner(gateway).create_plan(
        _request(),
        PlanningCapabilities(available_tool_names=frozenset({"edit"})),
    )

    assert result.plan.task_spec == TaskSpec(
        goal="rewrite the final paragraph",
        target={"type": "chapter", "range": "last_paragraph"},
        operation="write",
        instruction="be restrained",
        constraints=("do not reveal the father",),
        preserve=("umbrella foreshadowing",),
        deliverable="updated prose",
    )

    invalid = FakeModelGateway(json.dumps({
        "needsTodos": True,
        "title": "Invalid",
        "taskSpec": {"goal": "edit", "requires": ["database.all"]},
        "todos": [{
            "id": "edit",
            "title": "Edit",
            "type": "write",
            "executor": "tool",
            "expectedTools": ["edit"],
        }],
    }))
    with pytest.raises(Exception, match="host-owned fields"):
        await AgentPlanner(invalid).create_plan(
            _request(),
            PlanningCapabilities(available_tool_names=frozenset({"edit"})),
        )


def test_legacy_task_brief_input_is_rejected_and_new_output_uses_task_spec():
    with pytest.raises(Exception, match="taskBrief is unsupported"):
        normalize_task_plan(
            {
                "needsTodos": True,
                "title": "Legacy input",
                "taskBrief": {"goal": "read the current chapter"},
                "todos": [{
                    "id": "read",
                    "title": "Read",
                    "type": "read",
                    "executor": "tool",
                    "expectedTools": ["read"],
                }],
            },
            PlanningCapabilities(available_tool_names=frozenset({"read"})),
        )

    result = normalize_task_plan(
        {
            "needsTodos": True,
            "title": "Current input",
            "taskSpec": {"goal": "read the current chapter"},
            "todos": [{
                "id": "read",
                "title": "Read",
                "type": "read",
                "executor": "tool",
                "expectedTools": ["read"],
            }],
        },
        PlanningCapabilities(available_tool_names=frozenset({"read"})),
    )

    assert result.plan.task_spec == TaskSpec(goal="read the current chapter")
    execution_payload = json.loads(
        build_execution_message(result.plan).content.split("\n", 1)[1]
    )
    assert execution_payload["taskSpec"] == {
        "goal": "read the current chapter"
    }
    assert "taskBrief" not in execution_payload


def test_planner_receives_bounded_recent_dialogue_without_repeating_latest_user():
    request = AgentRunRequest(
        messages=(
            AgentMessage(role=MessageRole.USER, content="Keep the umbrella clue."),
            AgentMessage(role=MessageRole.ASSISTANT, content="I will preserve it."),
            AgentMessage(role=MessageRole.USER, content="Now rewrite the ending."),
        ),
        model=ModelRequest(provider="test", model="model"),
        domain_context=DomainContext(namespace="test"),
        tools_enabled=True,
    )
    messages = build_planner_messages(
        request,
        PlanningCapabilities(available_tool_names=frozenset({"edit"})),
    )
    payload = json.loads(messages[1].content)
    assert payload["userText"] == "Now rewrite the ending."
    assert payload["recentConversation"] == [
        {"role": "user", "content": "Keep the umbrella clue."},
        {"role": "assistant", "content": "I will preserve it."},
    ]


def test_host_plan_compiler_expands_contract_prerequisites_and_preserves_spec():
    task_spec = TaskSpec(goal="edit current chapter")
    plan = TaskPlan(
        title="Edit",
        goal=task_spec.goal,
        task_spec=task_spec,
        steps=(TaskStep(
            id="edit",
            title="Edit",
            type=StepType.WRITE,
            executor=StepExecutor.TOOL,
            suggested_tools=("edit",),
        ),),
    )
    compiled = compile_task_plan(
        plan,
        (
            _registration("catalog"),
            _registration("read", prerequisites=("catalog",)),
            _registration("edit", prerequisites=("read",)),
        ),
    )

    assert [
        step.suggested_tools[0]
        for step in compiled.plan.steps
    ] == ["catalog", "read", "edit"]
    assert compiled.inserted_tool_names == ("catalog", "read")
    assert compiled.plan.task_spec is task_spec


def test_host_plan_compiler_honors_edge_scoped_dependency_waiver():
    plan = TaskPlan(
        title="Read",
        steps=(TaskStep(
            id="read",
            title="Read",
            type=StepType.READ,
            executor=StepExecutor.TOOL,
            suggested_tools=("read",),
        ),),
    )
    compiled = compile_task_plan(
        plan,
        (_registration("catalog"), _registration("read", prerequisites=("catalog",))),
        constraints=PlanningConstraints(
            satisfied_tool_dependency_edges=frozenset({("read", "catalog")}),
        ),
    )
    assert compiled.inserted_tool_names == ()
    assert len(compiled.plan.steps) == 1


def test_host_plan_compiler_rejects_contract_cycles():
    plan = TaskPlan(
        title="Cycle",
        steps=(TaskStep(
            id="a",
            title="A",
            type=StepType.READ,
            executor=StepExecutor.TOOL,
            suggested_tools=("a",),
        ),),
    )
    with pytest.raises(ContractViolationError, match="prerequisite cycle"):
        compile_task_plan(
            plan,
            (
                _registration("a", prerequisites=("b",)),
                _registration("b", prerequisites=("a",)),
            ),
        )


def test_evidence_store_keeps_full_result_and_projects_bounded_planner_receipt():
    call = ToolCall(id="call-1", name="read", arguments_json='{"id":"chapter-1"}')
    content = "x" * 8_000
    batch = ToolBatchResult(
        results=(ToolCallResult(
            tool_call_id=call.id,
            tool_name=call.name,
            content=content,
        ),),
        outcome=ToolBatchOutcome.COMPLETED,
    )
    store = RunEvidenceStore()
    receipts = store.record_batch((call,), batch)

    assert isinstance(receipts[0], ToolResultReceipt)
    assert store.tool_result_receipts() == receipts
    record = store.get(receipts[0].evidence_id)
    assert record is not None
    assert record.content == content
    projected = store.project_messages_for_planning((AgentMessage(
        role=MessageRole.TOOL,
        tool_call_id=call.id,
        content=content,
    ),))
    payload = json.loads(projected[0].content)
    assert payload["completeEvidenceStoredByHost"] is True
    assert payload["contentCharacters"] == len(content)
    assert len(payload["excerpt"]) == 4_001


def test_stage_context_projection_only_removes_contract_proven_optional_blocks():
    retrieval = AgentMessage(
        role=MessageRole.DEVELOPER,
        content="large retrieval",
        attributes={"context_name": "writing_retrieval"},
    )
    binding = AgentMessage(
        role=MessageRole.DEVELOPER,
        content="binding",
        attributes={"context_name": "writing_session_binding"},
    )
    user = AgentMessage(role=MessageRole.USER, content="continue")
    messages = (retrieval, binding, user)
    contracts = {
        "read": ToolContextContract(),
        "edit": ToolContextContract(
            required_context_blocks=("writing_retrieval",),
        ),
    }

    initial = project_intermediate_tool_context(
        messages,
        visible_tool_names=frozenset({"read"}),
        contracts=contracts,
        enabled=True,
        initial_round=True,
    )
    assert initial.messages == messages

    read_round = project_intermediate_tool_context(
        messages,
        visible_tool_names=frozenset({"read"}),
        contracts=contracts,
        enabled=True,
        initial_round=False,
    )
    assert read_round.messages == (binding, user)
    assert read_round.dropped_context_blocks == ("writing_retrieval",)
    assert read_round.saved_tokens > 0

    edit_round = project_intermediate_tool_context(
        messages,
        visible_tool_names=frozenset({"edit"}),
        contracts=contracts,
        enabled=True,
        initial_round=False,
    )
    assert edit_round.messages == messages

    final_round = project_intermediate_tool_context(
        messages,
        visible_tool_names=frozenset(),
        contracts=contracts,
        enabled=True,
        initial_round=False,
    )
    assert final_round.messages == messages


@pytest.mark.asyncio
async def test_runtime_projects_only_intermediate_round_and_restores_final_evidence():
    read_one = _schema("readOne")
    read_two = _schema("readTwo")
    model = ScriptedModelGateway([
        _tool_call("call-1", "readOne"),
        _tool_call("call-2", "readTwo"),
        _answer("done"),
    ])
    tools = ScriptedToolGateway([
        _batch("call-1", "readOne", content="x" * 8_000),
        _batch("call-2", "readTwo"),
    ])
    observer = RecordingObserver((
        {"readOne"},
        {"readTwo"},
        set(),
    ))
    retrieval = AgentMessage(
        role=MessageRole.DEVELOPER,
        content="large retrieval",
        attributes={"context_name": "writing_retrieval"},
    )
    request = AgentRunRequest(
        messages=(
            retrieval,
            AgentMessage(
                role=MessageRole.DEVELOPER,
                content='{"taskSpec":{"goal":"read in order"}}',
                attributes={"agent_core_plan": True},
            ),
            AgentMessage(role=MessageRole.USER, content="read in order"),
        ),
        model=ModelRequest(provider="test", model="model"),
        domain_context=DomainContext(namespace="test"),
        context_window=16_000,
        tools_enabled=True,
    )
    runtime = AgentRuntime(
        model_gateway=model,
        tool_execution_gateway=tools,
        observer=observer,
    )
    updates = [
        update
        async for update in runtime.run(
            request,
            tools=(read_one, read_two),
            context_budget=_matching_budget(
                window=16_000,
                tools=(read_one, read_two),
            ),
            force_tool_choice=True,
            require_tool_call=True,
            tool_context_contracts={
                "readOne": ToolContextContract(
                    result_projection=ToolResultProjection.RECEIPT,
                ),
                "readTwo": ToolContextContract(),
                "edit": ToolContextContract(
                    required_context_blocks=("writing_retrieval",),
                ),
            },
            stage_context_projection_enabled=True,
        )
    ]

    tool_result_events = [
        update
        for update in updates
        if isinstance(update, AgentEvent)
        and update.type == CoreEventType.TOOL_RESULTS
    ]
    assert len(tool_result_events) == 2
    assert tool_result_events[0].payload["toolResultReceipts"]
    assert "receipts" not in tool_result_events[0].payload
    assert retrieval in model.message_rounds[0]
    assert retrieval not in model.message_rounds[1]
    assert retrieval in model.message_rounds[2]
    second_tool_result = next(
        message
        for message in model.message_rounds[1]
        if message.role is MessageRole.TOOL
    )
    second_payload = json.loads(second_tool_result.content)
    assert second_payload["completeEvidenceStoredByHost"] is True
    assert second_payload["contentCharacters"] == 8_000
    final_first_result = next(
        message
        for message in model.message_rounds[2]
        if message.role is MessageRole.TOOL
        and message.tool_call_id == "call-1"
    )
    assert final_first_result.content == "x" * 8_000
    projection_traces = [
        trace
        for trace in observer.traces
        if trace.stage == "context_projection"
    ]
    assert len(projection_traces) == 1
    assert projection_traces[0].details["savedTokens"] > 0
    assert projection_traces[0].details["compactedToolResults"] == [
        "tool:call-1",
    ]
