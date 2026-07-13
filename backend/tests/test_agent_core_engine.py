from __future__ import annotations

import ast
import asyncio
import json
from dataclasses import replace
from pathlib import Path
from typing import Sequence

import pytest

from agent_core.contracts import (
    AgentMessage,
    AgentRunRequest,
    AgentRunResult,
    ApprovalDecision,
    ApprovalStatus,
    ContextBlock,
    ContextBudgetClaim,
    ContextBundle,
    DomainContext,
    DomainEffect,
    ExecutionState,
    MessageRole,
    ModelCompletion,
    ModelFinishReason,
    ModelRequest,
    ModelStream,
    ModelStreamChunk,
    RunCreateParams,
    RunStatus,
    StepStatus,
    TaskStep,
    TaskStepUpdate,
    ToolCallDelta,
    ToolExecutionMode,
    ToolHandlerResult,
    ToolPolicy,
    ToolRiskLevel,
    ToolSchema,
    TraceRecord,
)
from agent_core.engine import AgentCore, AgentCoreRunOptions
from agent_core.events import AgentEvent, CoreEventType
from agent_core.ports import RunBeginResult, RunCommit, ToolRegistration
from agent_core.tools import InMemoryToolCatalog


PLAN = {
    "needsTodos": True,
    "title": "Update a resource",
    "goal": "Read, propose, approve, and report",
    "todos": [
        {
            "id": "read",
            "title": "Read resource",
            "type": "read",
            "executor": "tool",
            "expectedTools": ["read_resource"],
            "riskLevel": "read",
        },
        {
            "id": "propose",
            "title": "Propose change",
            "type": "write",
            "executor": "tool",
            "expectedTools": ["propose_change"],
            "riskLevel": "write",
        },
        {
            "id": "apply",
            "title": "Apply approved change",
            "type": "write",
            "executor": "tool",
            "expectedTools": ["apply_change"],
            "riskLevel": "destructive",
        },
        {
            "id": "respond",
            "title": "Report result",
            "type": "review",
            "executor": "model",
            "expectedTools": [],
            "riskLevel": "read",
        },
    ],
}


class MemoryRunRepository:
    """A domain-free repository implementing the atomic Core port."""

    def __init__(self):
        self.runs: dict[str, dict] = {}
        self.events: list[AgentEvent] = []
        self.traces: list[TraceRecord] = []
        self._counter = 0
        self.terminal_committed = asyncio.Event()

    async def begin(
        self,
        params: RunCreateParams,
        started_event: AgentEvent,
    ) -> RunBeginResult:
        self._counter += 1
        run_id = f"run-{self._counter}"
        event = AgentEvent(
            type=started_event.type,
            run_id=run_id,
            payload=started_event.payload,
        )
        self.runs[run_id] = {
            "params": params,
            "status": RunStatus.RUNNING,
            "steps": [],
            "final_response": "",
            "error": None,
        }
        self.events.append(event)
        return RunBeginResult(run_id=run_id, event=event)

    async def commit(
        self,
        run_id: str,
        commit: RunCommit,
    ) -> tuple[AgentEvent, ...]:
        current = self.runs[run_id]
        next_steps = list(current["steps"])
        if commit.replace_steps is not None:
            next_steps = list(commit.replace_steps)
        for update in commit.step_updates:
            next_steps = [
                replace(
                    step,
                    status=update.status,
                    result_summary=(
                        update.result_summary
                        if update.result_summary is not None
                        else step.result_summary
                    ),
                    error=(update.error if update.error is not None else step.error),
                )
                if step.id == update.step_id
                else step
                for step in next_steps
            ]
        next_status = commit.terminal_status or current["status"]
        next_run = {
            **current,
            "steps": next_steps,
            "status": next_status,
            "final_response": (
                commit.final_response
                if commit.final_response is not None
                else current["final_response"]
            ),
            "error": commit.error,
        }
        # Commit state and outbox together from the controller's perspective.
        self.runs[run_id] = next_run
        self.events.extend(commit.events)
        if commit.terminal_status is not None:
            self.terminal_committed.set()
        return commit.events

    async def append_event(self, run_id: str, event: AgentEvent) -> None:
        assert event.run_id == run_id
        self.events.append(event)

    async def append_trace(self, run_id: str, trace: TraceRecord) -> None:
        assert run_id in self.runs
        self.traces.append(trace)

    async def bind_conversation(self, run_id: str, conversation_id: int) -> None:
        self.runs[run_id]["conversation_id"] = conversation_id


class DelayedBeginRepository(MemoryRunRepository):
    """Expose the instant after the atomic begin commit but before return."""

    def __init__(self):
        super().__init__()
        self.committed = asyncio.Event()
        self.release = asyncio.Event()

    async def begin(self, params, started_event):
        result = await super().begin(params, started_event)
        self.committed.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            # Cancellation-linearizable port: the durable begin already won,
            # so cancellation returns its receipt instead of claiming that no
            # write occurred.
            return result
        return result


class BlockedBeforeBeginRepository(MemoryRunRepository):
    """Block before the atomic begin write to test prompt cancellation."""

    def __init__(self):
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def begin(self, params, started_event):
        self.entered.set()
        await self.release.wait()
        return await super().begin(params, started_event)


class FailingTerminalRepository(MemoryRunRepository):
    async def commit(self, run_id, commit):
        if commit.terminal_status is not None:
            raise RuntimeError("terminal commit failed")
        return await super().commit(run_id, commit)


class AlwaysPlan:
    def should_plan(self, request, capabilities):
        assert request.tools_enabled
        assert capabilities.available_tool_names == {
            "read_resource",
            "propose_change",
            "apply_change",
        }
        return True


class FixtureContextProvider:
    async def build_context(self, request, budget, signal=None):
        assert budget.allocation_for("fixture") == 1_000
        return ContextBundle(
            blocks=(ContextBlock(
                name="fixture",
                content="The current resource is host-owned test data.",
                untrusted=True,
            ),),
            diagnostics={"source": "fixture"},
        )


class SharedStateFactory:
    def __init__(self):
        self.state = ExecutionState(domain={
            "value": "before",
            "proposal": None,
            "handler_order": [],
            "value_seen_after_propose": None,
        })

    def create(self, request):
        return self.state


class ScriptedModelGateway:
    def __init__(self, state: ExecutionState, *, invalid_plan: bool = False):
        self.state = state
        self.invalid_plan = invalid_plan
        self.completions = []
        self.invocations = []

    async def complete(self, messages, invocation, signal=None):
        self.completions.append((tuple(messages), invocation))
        content = "not valid JSON" if self.invalid_plan else json.dumps(PLAN)
        return ModelCompletion(
            message=AgentMessage(role=MessageRole.ASSISTANT, content=content),
            model="planner-model",
        )

    async def stream(self, messages, invocation, signal=None):
        self.invocations.append(invocation)
        visible = tuple(schema.name for schema in invocation.tools)
        if visible == ("read_resource",):
            return ModelStream(
                chunks=_tool_chunks("call-read", "read_resource", "{}"),
                model="runtime-model",
            )
        if visible == ("propose_change",):
            return ModelStream(
                chunks=_tool_chunks(
                    "call-propose",
                    "propose_change",
                    '{"value":"after"}',
                ),
                model="runtime-model",
            )
        if visible == ("apply_change",):
            return ModelStream(
                chunks=_tool_chunks("call-apply", "apply_change", "{}"),
                model="runtime-model",
            )
        if visible == ():
            final = (
                "Applied the approved change."
                if self.state.domain["value"] == "after"
                else "The proposed change was not applied."
            )
            return ModelStream(chunks=_final_chunks(final), model="runtime-model")
        raise AssertionError(f"unexpected visible tools: {visible}")


async def _tool_chunks(call_id: str, name: str, arguments: str):
    yield ModelStreamChunk(
        tool_call_deltas=(ToolCallDelta(
            index=0,
            id=call_id,
            type="function",
            name=name,
            arguments_fragment=arguments,
        ),),
        finish_reason=ModelFinishReason.TOOL_CALLS,
    )


async def _final_chunks(content: str):
    yield ModelStreamChunk(
        content_delta=content,
        finish_reason=ModelFinishReason.STOP,
    )


def _registration(name, handler, *, mode, risk):
    return ToolRegistration(
        schema=ToolSchema(
            name=name,
            description=f"Generic test capability {name}",
            parameters={
                "type": "object",
                "properties": {
                    "value": {"type": "string"},
                },
            },
        ),
        handler=handler,
        policy=ToolPolicy(
            mode=mode,
            title=f"Use {name}",
            risk_level=risk,
        ),
    )


def _core_fixture(
    *,
    invalid_plan: bool = False,
    repository: MemoryRunRepository | None = None,
    proposed_effect_type: str = "test.change_proposed",
):
    state_factory = SharedStateFactory()

    async def read_resource(state, arguments, signal=None):
        state.domain["handler_order"].append("read")
        return ToolHandlerResult(json.dumps({"value": state.domain["value"]}))

    async def propose_change(state, arguments, signal=None):
        state.domain["handler_order"].append("propose")
        state.domain["proposal"] = arguments["value"]
        state.domain["value_seen_after_propose"] = state.domain["value"]
        return ToolHandlerResult(
            json.dumps({"proposed": arguments["value"]}),
            effects=(DomainEffect(
                type=proposed_effect_type,
                payload={"value": arguments["value"]},
            ),),
        )

    async def apply_change(state, arguments, signal=None):
        state.domain["handler_order"].append("apply")
        state.domain["value"] = state.domain["proposal"]
        return ToolHandlerResult(json.dumps({"value": state.domain["value"]}))

    catalog = InMemoryToolCatalog((
        _registration(
            "read_resource",
            read_resource,
            mode=ToolExecutionMode.READ,
            risk=ToolRiskLevel.READ,
        ),
        _registration(
            "propose_change",
            propose_change,
            mode=ToolExecutionMode.PROPOSE,
            risk=ToolRiskLevel.WRITE,
        ),
        _registration(
            "apply_change",
            apply_change,
            mode=ToolExecutionMode.CONFIRM,
            risk=ToolRiskLevel.DESTRUCTIVE,
        ),
    ))
    repository = repository or MemoryRunRepository()
    model = ScriptedModelGateway(
        state_factory.state,
        invalid_plan=invalid_plan,
    )
    core = AgentCore(
        model_gateway=model,
        run_repository=repository,
        planning_policy=AlwaysPlan(),
        context_provider=FixtureContextProvider(),
        execution_state_factory=state_factory,
        tool_catalog=catalog,
    )
    request = AgentRunRequest(
        messages=(AgentMessage(
            role=MessageRole.USER,
            content="Update the generic resource safely.",
        ),),
        model=ModelRequest(provider="fixture", model="scripted"),
        domain_context=DomainContext(
            namespace="test.fixture",
            payload={"resource": "alpha"},
        ),
        session_id="fixture-session",
        mode="agent",
        context_window=32_000,
        tools_enabled=True,
    )
    options = AgentCoreRunOptions(
        context_claims=(ContextBudgetClaim("fixture", 1_000),),
        output_reserve_tokens=1_024,
    )
    return core, request, options, repository, model, state_factory.state


@pytest.mark.asyncio
@pytest.mark.parametrize("approve", [True, False], ids=["approved", "rejected"])
async def test_standalone_core_runs_read_propose_confirm_and_terminal_flow(approve):
    core, request, options, repository, model, state = _core_fixture()
    updates = []
    approval_id = None

    async for update in core.run(request, options=options):
        updates.append(update)
        if (
            isinstance(update, AgentEvent)
            and update.type == CoreEventType.APPROVAL_REQUESTED
        ):
            approval_id = str(update.payload["approvalId"])
            assert state.domain["handler_order"] == ["read", "propose"]
            assert state.domain["value"] == "before"
            assert core.resolve_approval(
                "wrong-run",
                approval_id,
                ApprovalDecision.APPROVE,
            ) is None
            decision = (
                ApprovalDecision.APPROVE
                if approve
                else ApprovalDecision.REJECT
            )
            expected = (
                ApprovalStatus.APPROVED
                if approve
                else ApprovalStatus.REJECTED
            )
            assert core.resolve_approval(
                update.run_id,
                approval_id,
                decision,
            ) is expected
            assert core.resolve_approval(
                update.run_id,
                approval_id,
                decision,
            ) is None

    assert approval_id is not None
    assert isinstance(updates[-1], AgentRunResult)
    assert sum(isinstance(item, AgentRunResult) for item in updates) == 1
    result = updates[-1]
    assert result.status is RunStatus.DONE
    assert result.final_response == (
        "Applied the approved change."
        if approve
        else "The proposed change was not applied."
    )
    assert [tuple(schema.name for schema in call.tools) for call in model.invocations] == [
        ("read_resource",),
        ("propose_change",),
        ("apply_change",),
        (),
    ]
    assert all(call.max_output_tokens == 1_024 for call in model.invocations)
    assert state.domain["value_seen_after_propose"] == "before"
    assert state.domain["handler_order"] == (
        ["read", "propose", "apply"] if approve else ["read", "propose"]
    )
    assert state.domain["value"] == ("after" if approve else "before")
    run = repository.runs[result.run_id]
    assert run["status"] is RunStatus.DONE
    assert all(step.status is StepStatus.DONE for step in run["steps"])
    assert CoreEventType.RUN_STARTED in [item.type for item in updates[:-1]]
    assert CoreEventType.RUN_TODOS_UPDATED in [item.type for item in updates[:-1]]
    assert CoreEventType.CONTEXT_BUDGETED in [item.type for item in updates[:-1]]
    assert updates[-2].type == CoreEventType.RUN_COMPLETED
    assert core.cancel_pending_approvals(result.run_id) == 0


@pytest.mark.asyncio
async def test_standalone_core_cancels_during_approval_without_running_confirm_handler():
    core, request, options, repository, model, state = _core_fixture()
    signal = asyncio.Event()
    updates = []

    async for update in core.run(request, options=options, signal=signal):
        updates.append(update)
        if (
            isinstance(update, AgentEvent)
            and update.type == CoreEventType.APPROVAL_REQUESTED
        ):
            signal.set()

    result = updates[-1]
    assert isinstance(result, AgentRunResult)
    assert result.status is RunStatus.CANCELED
    assert state.domain["handler_order"] == ["read", "propose"]
    assert state.domain["value"] == "before"
    assert repository.runs[result.run_id]["status"] is RunStatus.CANCELED
    assert core.cancel_pending_approvals(result.run_id) == 0


@pytest.mark.asyncio
async def test_closing_public_stream_persists_canceled_run_and_cleans_approval():
    core, request, options, repository, model, state = _core_fixture()
    stream = core.run(request, options=options)
    run_id = None
    while True:
        update = await anext(stream)
        if isinstance(update, AgentEvent):
            run_id = update.run_id or run_id
            if update.type == CoreEventType.APPROVAL_REQUESTED:
                break

    await stream.aclose()

    assert run_id is not None
    assert repository.runs[run_id]["status"] is RunStatus.CANCELED
    assert state.domain["handler_order"] == ["read", "propose"]
    assert core.cancel_pending_approvals(run_id) == 0


@pytest.mark.asyncio
async def test_closing_immediately_after_run_started_never_leaves_a_running_run():
    core, request, options, repository, model, state = _core_fixture()
    stream = core.run(request, options=options)
    started = await anext(stream)
    assert isinstance(started, AgentEvent)
    assert started.type == CoreEventType.RUN_STARTED

    await stream.aclose()

    assert started.run_id is not None
    assert repository.runs[started.run_id]["status"] is RunStatus.CANCELED
    assert model.completions == []
    assert state.domain["handler_order"] == []


@pytest.mark.asyncio
async def test_outer_cancellation_after_atomic_begin_commit_closes_the_run():
    repository = DelayedBeginRepository()
    core, request, options, _, model, state = _core_fixture(
        repository=repository
    )
    stream = core.run(request, options=options)
    next_update = asyncio.create_task(anext(stream))
    await repository.committed.wait()

    next_update.cancel()
    with pytest.raises(asyncio.CancelledError):
        await next_update
    repository.release.set()

    assert len(repository.runs) == 1
    run = next(iter(repository.runs.values()))
    assert run["status"] is RunStatus.CANCELED
    assert model.completions == []
    assert state.domain["handler_order"] == []


@pytest.mark.asyncio
async def test_outer_cancellation_does_not_wait_for_precommit_begin_block():
    repository = BlockedBeforeBeginRepository()
    core, request, options, _, model, state = _core_fixture(
        repository=repository
    )
    stream = core.run(request, options=options)
    next_update = asyncio.create_task(anext(stream))
    await repository.entered.wait()

    next_update.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(next_update, timeout=0.2)
    assert repository.runs == {}

    # No retained background operation may turn a definitely canceled begin
    # into a later write.
    repository.release.set()
    await asyncio.sleep(0)
    assert repository.runs == {}
    assert model.completions == []
    assert state.domain["handler_order"] == []


@pytest.mark.asyncio
async def test_disconnect_surfaces_terminal_commit_failure_to_aclose_caller():
    repository = FailingTerminalRepository()
    core, request, options, _, model, state = _core_fixture(
        repository=repository
    )
    stream = core.run(request, options=options)
    started = await anext(stream)
    assert isinstance(started, AgentEvent)
    assert started.type == CoreEventType.RUN_STARTED

    with pytest.raises(RuntimeError, match="terminal commit failed"):
        await stream.aclose()

    assert started.run_id is not None
    assert repository.runs[started.run_id]["status"] is RunStatus.RUNNING
    assert model.completions == []
    assert state.domain["handler_order"] == []


@pytest.mark.asyncio
async def test_planner_failure_still_has_a_traceable_failed_run_and_one_result():
    core, request, options, repository, model, state = _core_fixture(
        invalid_plan=True
    )
    updates = [item async for item in core.run(request, options=options)]

    assert [
        item.type for item in updates if isinstance(item, AgentEvent)
    ] == [CoreEventType.RUN_STARTED, CoreEventType.RUN_FAILED]
    result = updates[-1]
    assert isinstance(result, AgentRunResult)
    assert result.status is RunStatus.FAILED
    assert result.error == "planning_invalid"
    assert repository.runs[result.run_id]["status"] is RunStatus.FAILED
    assert model.invocations == []
    assert state.domain["handler_order"] == []


@pytest.mark.asyncio
async def test_runtime_domain_effect_cannot_inject_controller_owned_lifecycle_event():
    core, request, options, repository, _, _ = _core_fixture(
        proposed_effect_type=CoreEventType.RUN_COMPLETED,
    )

    updates = [update async for update in core.run(request, options=options)]

    result = updates[-1]
    assert isinstance(result, AgentRunResult)
    assert result.status is RunStatus.FAILED
    assert result.error == "tool_execution_error"
    persisted_types = [event.type for event in repository.events]
    assert CoreEventType.RUN_COMPLETED not in persisted_types
    assert [
        event_type
        for event_type in persisted_types
        if event_type in {
            CoreEventType.RUN_COMPLETED,
            CoreEventType.RUN_BLOCKED,
            CoreEventType.RUN_FAILED,
            CoreEventType.RUN_CANCELED,
        }
    ] == [CoreEventType.RUN_FAILED]


def test_standalone_engine_test_has_no_application_or_domain_imports():
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    banned = {
        "application",
        "database",
        "domains",
        "infrastructure",
        "routers",
        "services",
    }
    imported_roots = {
        alias.name.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        (node.module or "").split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert imported_roots.isdisjoint(banned)
