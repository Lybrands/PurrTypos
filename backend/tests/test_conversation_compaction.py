from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from purra.context_orchestration import (
    ContextCompressionCoordinator,
)
from purra.context_orchestration.contracts import (
    ContextCompressionSettings,
    ConversationCompactionResult,
)
from purra.context_orchestration.ledger import (
    ContextCompactionBudget,
    ContextCompactionPhase,
)
from purra.contracts import (
    AgentMessage,
    AgentRunRequest,
    DomainContext,
    MessageOrigin,
    MessageRole,
    ModelCompletion,
    ModelFinishReason,
    ModelRequest,
    PlanningCapabilities,
    ToolCall,
)
from application.conversation_compaction_contracts import (
    ConversationSummary,
    ConversationTurn,
)
from purra.errors import ContextOverflowError, ContractViolationError
from purra.api import AgentModelTaskRunner
from purra.model_invocation import AgentModelInvocationManager, ModelInvocationContext
from purra.model_protocol import generic_capability_snapshot
from purra.operations import (
    AgentOperationController,
    OperationKind,
    OperationScope,
    OperationStatus,
)
from purra.planner import build_planner_messages
from purra.testing import assert_context_compression_hook_conforms
from application.conversation_compaction import (
    ConversationCompactionService,
    ConversationSummaryCompressionPolicy,
)
from database.connection import DatabaseConnection
from infrastructure.models.model_conversation_summarizer import (
    ModelBackedConversationSummarizer,
)
from infrastructure.persistence.sqlite_conversation_compaction_repository import (
    SqliteConversationCompactionRepository,
)


class _Repository:
    def __init__(
        self,
        turns: tuple[ConversationTurn, ...],
        summary: ConversationSummary | None = None,
    ) -> None:
        self.turns = turns
        self.summary = summary
        self.deleted = False

    async def load_summary(self, session_id):
        return self.summary

    async def list_turns(self, session_id, *, after_conversation_id=0):
        return tuple(turn for turn in self.turns if turn.id > after_conversation_id)

    async def save_summary(self, summary):
        self.summary = summary

    async def delete_summary(self, session_id):
        self.summary = None
        self.deleted = True


class _Gateway:
    def __init__(self, *responses: str | Exception) -> None:
        self.responses = list(responses)
        self.calls = []

    async def complete(self, messages, invocation, signal=None):
        self.calls.append((tuple(messages), invocation, signal))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return ModelCompletion(
            applied_output_limit=invocation.max_call_output_tokens,
            message=AgentMessage(
                role=MessageRole.ASSISTANT,
                content=response,
            ),
            model="summary-model",
            finish_reason=ModelFinishReason.STOP,
        )

    async def stream(self, messages, invocation, signal=None):  # pragma: no cover
        raise AssertionError("conversation compaction must not stream")


class _OperationOutput:
    def __init__(self):
        self.events = []

    async def accept_operation_event(self, event):
        self.events.append(event)
        return event


def _turns(count: int) -> tuple[ConversationTurn, ...]:
    return tuple(
        ConversationTurn(
            id=index,
            prompt=f"u{index}|" + ("甲" * 1_000),
            response=f"a{index}|" + ("乙" * 1_000),
        )
        for index in range(1, count + 1)
    )


def _request(
    turns: tuple[ConversationTurn, ...],
    *,
    context_window: int = 32_000,
    tools_enabled: bool = True,
) -> AgentRunRequest:
    history = tuple(
        message
        for turn in turns
        for message in (
            AgentMessage(role=MessageRole.USER, content=turn.prompt),
            AgentMessage(role=MessageRole.ASSISTANT, content=turn.response),
        )
    )
    return AgentRunRequest(
        messages=(
            *history,
            AgentMessage(role=MessageRole.USER, content="current"),
        ),
        model=ModelRequest(
            provider="test",
            model="model",
            capability_snapshot=replace(
                generic_capability_snapshot(),
                profile_id="test:model",
                max_call_output_tokens=4_096,
            ),
        ),
        domain_context=DomainContext(namespace="test"),
        session_id=7,
        tools_enabled=tools_enabled,
        context_window=context_window,
    )


_SUMMARY_JSON = json.dumps({
    "activeGoal": "finish the edit",
    "targets": [{"type": "chapter", "id": "chapter-1"}],
    "decisions": ["keep the ending"],
    "constraints": ["do not change names"],
    "unresolvedItems": ["check continuity"],
    "completedActions": ["read outline"],
    "summary": "The user is revising chapter one.",
})


async def _record_started(target: list, payload) -> None:
    target.append(dict(payload))


def _coordinator(
    repository,
    gateway,
    *,
    settings: ContextCompressionSettings = ContextCompressionSettings(),
) -> ContextCompressionCoordinator:
    return ContextCompressionCoordinator(
        ConversationCompactionService(
            repository,
            ModelBackedConversationSummarizer(_model_tasks(gateway)),
        ),
        settings,
    )


def _model_tasks(gateway) -> AgentModelTaskRunner:
    return AgentModelTaskRunner(
        AgentModelInvocationManager(gateway),
        ModelInvocationContext(run_id="compaction-test-run"),
    )


@pytest.mark.asyncio
async def test_core_compactor_depends_only_on_application_compression_hook():
    class _Hook:
        def __init__(self):
            self.calls = []

        async def compress(self, compression, signal=None):
            self.calls.append((compression, signal))
            return ConversationCompactionResult(
                compression.request,
                "application_no_change",
            )

    turns = _turns(8)
    hook = _Hook()

    result = await ContextCompressionCoordinator(hook).prepare(
        _request(turns, context_window=200_000)
    )

    assert result.outcome == "application_no_change"
    assert len(hook.calls) == 1
    compression = hook.calls[0][0]
    assert compression.compression_required is False
    assert compression.trigger_reason == "below_threshold"
    assert compression.available_message_tokens > 0


@pytest.mark.asyncio
async def test_application_compression_passes_shared_hook_conformance():
    await assert_context_compression_hook_conforms(
        hook=ConversationCompactionService(
            _Repository(()),
            ModelBackedConversationSummarizer(_model_tasks(_Gateway())),
        ),
        request=_request((), context_window=32_000),
    )


@pytest.mark.asyncio
async def test_core_default_uses_recent_twenty_message_window_only_without_hook():
    turns = _turns(30)

    result = await ContextCompressionCoordinator().prepare(_request(turns))

    assert result.outcome == "compacted_default_trim"
    caller_messages = [
        message
        for message in result.request.messages
        if message.origin is MessageOrigin.CALLER
    ]
    assert len(caller_messages) <= 20
    assert result.request.messages[-1].content == "current"
    assert result.compression_state_version is None
    assert result.diagnostics["strategy"] == "recent_messages"


@pytest.mark.asyncio
async def test_compaction_has_one_authoritative_operation_lifecycle():
    turns = _turns(30)
    output = _OperationOutput()
    coordinator = ContextCompressionCoordinator(
        operation_controller=AgentOperationController(output),
    )

    result = await coordinator.prepare(
        _request(turns),
        operation_scope=OperationScope(run_id="run-1"),
    )

    assert result.outcome == "compacted_default_trim"
    assert len(output.events) == 2
    started, finished = output.events
    assert started.kind is OperationKind.CONTEXT_COMPACTION
    assert finished.operation_id == started.operation_id
    assert finished.status is OperationStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_below_threshold_check_does_not_create_compaction_operation():
    class _Hook:
        def __init__(self):
            self.calls = 0

        async def compress(self, compression, signal=None):
            self.calls += 1
            return ConversationCompactionResult(
                compression.request,
                "application_no_change",
            )

    output = _OperationOutput()
    hook = _Hook()
    result = await ContextCompressionCoordinator(
        hook,
        operation_controller=AgentOperationController(output),
    ).prepare(
        _request(_turns(8), context_window=200_000),
        operation_scope=OperationScope(run_id="run-1"),
    )

    assert result.outcome == "application_no_change"
    assert hook.calls == 1
    assert output.events == []


@pytest.mark.asyncio
async def test_application_hook_replaces_instead_of_chaining_default_trim():
    class _Hook:
        async def compress(self, compression, signal=None):
            current = compression.request.messages[-1]
            return ConversationCompactionResult(
                replace(compression.request, messages=(current,)),
                "application_reduced",
            )

    turns = _turns(30)
    result = await ContextCompressionCoordinator(_Hook()).prepare(
        _request(turns)
    )

    assert result.outcome == "application_reduced"
    assert [message.content for message in result.request.messages] == [
        "current"
    ]
    assert result.diagnostics["strategy"] == "application_hook"


@pytest.mark.asyncio
async def test_application_owns_stateless_fallback_when_session_is_missing():
    turns = _turns(30)
    original = replace(_request(turns), session_id=None)
    gateway = _Gateway()

    result = await _coordinator(
        _Repository(turns),
        gateway,
    ).prepare(original)

    assert result.outcome == "compacted_application_fallback"
    assert result.diagnostics["fallbackCause"] == "no_session"
    assert result.diagnostics["strategy"] == "application_hook"
    assert result.request.messages[-1].content == "current"
    assert len(result.request.messages) < len(original.messages)
    assert gateway.calls == []


@pytest.mark.asyncio
async def test_application_bounds_semantic_passes_before_emergency_projection():
    turns = _turns(30)
    repository = _Repository(turns)
    gateway = _Gateway(_SUMMARY_JSON)
    policy = ConversationSummaryCompressionPolicy(
        max_input_characters=3_000,
        max_compaction_passes=1,
    )
    coordinator = ContextCompressionCoordinator(
        ConversationCompactionService(
            repository,
            ModelBackedConversationSummarizer(_model_tasks(gateway)),
            policy,
        )
    )

    result = await coordinator.prepare(_request(turns))

    assert result.outcome == "compacted_application_fallback"
    assert result.diagnostics["fallbackCause"] == (
        "semantic_pass_limit_reached"
    )
    assert result.diagnostics["semanticPassCount"] == 1
    assert result.diagnostics["maxSemanticPasses"] == 1
    assert result.request.messages[-1].content == "current"
    assert repository.summary is not None
    assert repository.summary.covered_turn_count == 1
    assert len(gateway.calls) == 1


@pytest.mark.asyncio
async def test_generation_failure_without_summary_uses_application_fallback():
    turns = _turns(5)
    repository = _Repository(turns)

    result = await _coordinator(
        repository,
        _Gateway(RuntimeError("provider unavailable")),
    ).prepare(_request(turns, context_window=24_000))

    assert result.outcome == "compacted_application_fallback"
    assert result.diagnostics["failureStage"] == "generation"
    assert result.diagnostics["fallbackCause"] == "generation_failed"
    assert result.request.messages[-1].content == "current"
    assert repository.summary is None


@pytest.mark.asyncio
async def test_persistence_failure_uses_transient_application_projection():
    class _FailingSaveRepository(_Repository):
        async def save_summary(self, summary):
            raise RuntimeError("database unavailable")

    turns = _turns(30)
    repository = _FailingSaveRepository(turns)

    result = await _coordinator(
        repository,
        _Gateway(_SUMMARY_JSON),
    ).prepare(_request(turns))

    assert result.outcome == "persistence_failed_transient"
    assert result.diagnostics["failureStage"] == "persistence"
    assert result.compression_state_version == 1
    assert result.request.messages[-1].content == "current"
    assert repository.summary is None


@pytest.mark.asyncio
async def test_core_rejects_hook_that_removes_current_user_request():
    class _Hook:
        async def compress(self, compression, signal=None):
            return ConversationCompactionResult(
                replace(compression.request, messages=()),
                "invalid",
            )

    with pytest.raises(ContractViolationError, match="current user"):
        await ContextCompressionCoordinator(_Hook()).prepare(
            _request(_turns(30))
        )


@pytest.mark.asyncio
async def test_core_rejects_partial_tool_exchange_from_application_hook():
    call = ToolCall(id="call-1", name="read", arguments_json="{}")
    request = replace(
        _request((), context_window=200_000),
        messages=(
            AgentMessage(role=MessageRole.USER, content="older"),
            AgentMessage(
                role=MessageRole.ASSISTANT,
                content="",
                tool_calls=(call,),
            ),
            AgentMessage(
                role=MessageRole.TOOL,
                content="result",
                tool_call_id=call.id,
            ),
            AgentMessage(role=MessageRole.ASSISTANT, content="done"),
            AgentMessage(role=MessageRole.USER, content="current"),
        ),
    )

    class _Hook:
        async def compress(self, compression, signal=None):
            invalid = tuple(
                message
                for message in compression.request.messages
                if message.role is not MessageRole.TOOL
            )
            return ConversationCompactionResult(
                replace(compression.request, messages=invalid),
                "invalid",
            )

    with pytest.raises(ContractViolationError, match="tool calls"):
        await ContextCompressionCoordinator(_Hook()).prepare(request)


@pytest.mark.asyncio
async def test_core_rejects_application_result_that_still_exceeds_budget():
    class _Hook:
        async def compress(self, compression, signal=None):
            return ConversationCompactionResult(
                compression.request,
                "insufficient",
            )

    with pytest.raises(ContextOverflowError) as captured:
        await ContextCompressionCoordinator(_Hook()).prepare(
            _request(_turns(30))
        )

    assert captured.value.reason_code == (
        "context_compression_result_exceeds_budget"
    )


@pytest.mark.asyncio
async def test_initial_compaction_keeps_recent_raw_turns_and_injects_host_summary():
    turns = _turns(8)
    repository = _Repository(turns)
    gateway = _Gateway(_SUMMARY_JSON)
    started = []

    result = await _coordinator(repository, gateway).prepare(
        _request(turns),
        on_compaction_started=lambda payload: _record_started(started, payload),
    )

    assert result.outcome == "compacted"
    assert result.compacted_turn_count == 4
    assert result.retained_raw_turn_count == 4
    assert repository.summary is not None
    assert repository.summary.covered_turn_count == 4
    assert result.compression_state_version == repository.summary.version
    assert result.request.messages[0].role is MessageRole.USER
    assert result.request.messages[0].origin is MessageOrigin.HOST_CONTEXT
    assert [message.content for message in result.request.messages[1:]] == [
        item
        for turn in turns[4:]
        for item in (turn.prompt, turn.response)
    ] + ["current"]
    payload = json.loads(gateway.calls[0][0][1].content)
    assert payload["existingSummary"] is None
    assert [row["user"] for row in payload["newTurns"]] == [
        turn.prompt for turn in turns[:4]
    ]
    assert started[0]["strategy"] == "application_hook"
    assert started[0]["triggerReason"] in {
        "pressure_threshold",
        "message_budget_exceeded",
    }
    assert started[0]["pressureRatio"] >= 0.85


@pytest.mark.asyncio
async def test_short_conversation_does_not_add_a_summary_model_call():
    turns = _turns(7)
    repository = _Repository(turns)
    gateway = _Gateway(_SUMMARY_JSON)
    original = _request(turns, context_window=200_000)

    result = await _coordinator(repository, gateway).prepare(
        original
    )

    assert result.outcome == "below_threshold"
    assert result.request is original
    assert gateway.calls == []


@pytest.mark.asyncio
async def test_large_window_does_not_compact_by_turn_count_alone():
    turns = _turns(24)
    repository = _Repository(turns)
    gateway = _Gateway(_SUMMARY_JSON)

    result = await _coordinator(repository, gateway).prepare(
        _request(turns, context_window=256_000)
    )

    assert result.outcome == "below_threshold"
    assert result.diagnostics["triggerReason"] == "below_threshold"
    assert result.diagnostics["pressureRatio"] < 0.85
    assert result.diagnostics["messageTokensBefore"] > 0
    assert gateway.calls == []


@pytest.mark.asyncio
async def test_hundreds_of_turns_use_bounded_multi_pass_summary_without_losing_current():
    initial_turns = _turns(160)
    repository = _Repository(initial_turns)
    gateway = _Gateway(*([_SUMMARY_JSON] * 12))
    service = _coordinator(repository, gateway)

    covered_counts = []
    result = None
    for _ in range(10):
        result = await service.prepare(_request(initial_turns))
        assert result.outcome == "compacted"
        assert repository.summary is not None
        covered_counts.append(repository.summary.covered_turn_count)
        assert result.request.messages[-1].content == "current"
        if repository.summary.covered_turn_count == 156:
            break

    assert result is not None
    assert covered_counts == sorted(set(covered_counts))
    assert covered_counts[-1] == 156
    assert result.retained_raw_turn_count == 4

    expanded_turns = _turns(200)
    repository.turns = expanded_turns
    added_counts = []
    for _ in range(4):
        result = await service.prepare(_request(expanded_turns))
        assert result.outcome == "compacted"
        assert repository.summary is not None
        added_counts.append(repository.summary.covered_turn_count)
        assert result.request.messages[-1].content == "current"
        if repository.summary.covered_turn_count == 196:
            break

    assert added_counts == sorted(set(added_counts))
    assert added_counts[-1] == 196
    assert result.retained_raw_turn_count == 4
    assert len(gateway.calls) > len(covered_counts) + len(added_counts)
    assert repository.summary is not None
    assert repository.summary.version == len(gateway.calls)


@pytest.mark.asyncio
async def test_semantic_target_belongs_to_application_not_tool_mode():
    turns = _turns(8)
    tool_result = await _coordinator(
        _Repository(turns),
        _Gateway(_SUMMARY_JSON),
    ).prepare(_request(turns, tools_enabled=True))
    direct_result = await _coordinator(
        _Repository(turns),
        _Gateway(_SUMMARY_JSON),
    ).prepare(_request(turns, tools_enabled=False))

    assert tool_result.diagnostics["targetRatio"] == 0.65
    assert direct_result.diagnostics["targetRatio"] == 0.65


@pytest.mark.asyncio
async def test_core_does_not_change_compression_target_from_plan_complexity():
    turns = _turns(8)
    simple = await _coordinator(
        _Repository(turns),
        _Gateway(_SUMMARY_JSON),
    ).prepare(
        _request(turns),
        budget=ContextCompactionBudget(
            phase=ContextCompactionPhase.POST_PLANNING,
            provider_input_tokens=18_000,
            context_tokens=2_000,
            context_tokens_are_resolved=True,
            output_reserve_tokens=8_192,
            planned_step_count=2,
            planned_tool_count=1,
            selected_tool_count=1,
        ),
    )
    complex_result = await _coordinator(
        _Repository(turns),
        _Gateway(_SUMMARY_JSON),
    ).prepare(
        _request(turns),
        budget=ContextCompactionBudget(
            phase=ContextCompactionPhase.POST_PLANNING,
            provider_input_tokens=18_000,
            context_tokens=2_000,
            context_tokens_are_resolved=True,
            output_reserve_tokens=8_192,
            planned_step_count=8,
            planned_tool_count=4,
            selected_tool_count=4,
        ),
    )

    assert simple.diagnostics["compactionPhase"] == "post_planning"
    assert complex_result.diagnostics["compactionPhase"] == "post_planning"
    assert complex_result.diagnostics["targetRatio"] == (
        simple.diagnostics["targetRatio"]
    )


@pytest.mark.asyncio
async def test_compaction_updates_incrementally_from_persisted_summary():
    first_turns = _turns(8)
    repository = _Repository(first_turns)
    first = await _coordinator(
        repository,
        _Gateway(_SUMMARY_JSON),
    ).prepare(_request(first_turns))
    assert repository.summary is not None

    repository.turns = _turns(12)
    gateway = _Gateway(_SUMMARY_JSON)
    second = await _coordinator(repository, gateway).prepare(
        _request(repository.turns)
    )

    assert second.outcome == "compacted"
    assert repository.summary is not None
    assert repository.summary.version == 2
    assert repository.summary.covered_turn_count == 8
    assert second.retained_raw_turn_count == 4
    payload = json.loads(gateway.calls[0][0][1].content)
    assert payload["existingSummary"]["activeGoal"] == "finish the edit"
    assert [row["user"] for row in payload["newTurns"]] == [
        turn.prompt for turn in repository.turns[4:8]
    ]


@pytest.mark.asyncio
async def test_post_planning_optimizer_extends_summary_from_raw_source():
    initial_turns = _turns(8)
    repository = _Repository(initial_turns)
    first = await _coordinator(
        repository,
        _Gateway(_SUMMARY_JSON),
    ).prepare(_request(initial_turns))
    assert repository.summary is not None
    first_covered_count = repository.summary.covered_turn_count

    repository.turns = _turns(12)
    raw_request = _request(repository.turns, context_window=256_000)
    preflight = await _coordinator(
        repository,
        _Gateway(_SUMMARY_JSON),
    ).prepare(raw_request)
    assert preflight.outcome == "reused"

    started = []
    optimized = await _coordinator(
        repository,
        _Gateway(_SUMMARY_JSON),
    ).prepare(
        replace(
            raw_request,
            metadata={
                **dict(raw_request.metadata),
                **dict(preflight.request.metadata),
            },
        ),
        budget=ContextCompactionBudget(
            phase=ContextCompactionPhase.POST_PLANNING,
            provider_input_tokens=22_000,
            context_tokens=4_000,
            context_tokens_are_resolved=True,
            output_reserve_tokens=1_024,
            planned_step_count=6,
            planned_tool_count=4,
            selected_tool_count=4,
        ),
        on_compaction_started=lambda payload: _record_started(started, payload),
    )

    assert optimized.outcome == "compacted"
    assert optimized.compression_state_version == 2
    assert repository.summary is not None
    assert repository.summary.covered_turn_count > (
        first_covered_count
    )
    assert optimized.diagnostics["compactionPhase"] == "post_planning"
    assert started[0]["strategy"] == "application_hook"


@pytest.mark.asyncio
async def test_generation_failure_advances_with_host_fallback_without_blocking():
    turns = _turns(8)
    repository = _Repository(turns)
    first = await _coordinator(
        repository,
        _Gateway(_SUMMARY_JSON),
    ).prepare(_request(turns))
    assert repository.summary is not None
    repository.turns = _turns(12)

    result = await _coordinator(
        repository,
        _Gateway(RuntimeError("provider unavailable")),
    ).prepare(_request(repository.turns))

    assert result.outcome == "compacted_fallback"
    assert repository.summary is not None
    assert result.compression_state_version == repository.summary.version
    assert repository.summary.version == 2
    assert repository.summary.covered_turn_count == 6
    assert result.retained_raw_turn_count == 6
    assert "主机按回合保留的原文摘录" in repository.summary.summary
    assert result.diagnostics["failureStage"] == "generation"
    assert result.diagnostics["failureType"] == "RuntimeError"
    assert result.diagnostics["fallback"] == "host_extractive"
    assert result.diagnostics["strategy"] == "application_hook"


@pytest.mark.asyncio
async def test_initial_generation_failure_creates_persisted_host_fallback():
    turns = _turns(8)
    repository = _Repository(turns)
    original = _request(turns)

    result = await _coordinator(
        repository,
        _Gateway(RuntimeError("provider unavailable")),
    ).prepare(original)

    assert result.outcome == "compacted_fallback"
    assert result.request is not original
    assert repository.summary is not None
    assert result.compression_state_version == repository.summary.version
    assert repository.summary.covered_turn_count == 2
    assert result.compacted_turn_count == 2
    assert result.retained_raw_turn_count == 6


@pytest.mark.asyncio
async def test_invalid_summary_json_is_repaired_once():
    turns = _turns(8)
    repository = _Repository(turns)
    gateway = _Gateway('{"activeGoal":', _SUMMARY_JSON)

    result = await _coordinator(repository, gateway).prepare(
        _request(turns)
    )

    assert result.outcome == "compacted"
    assert len(gateway.calls) == 2
    assert "not one complete JSON object" in gateway.calls[1][0][-1].content


@pytest.mark.asyncio
async def test_history_mismatch_invalidates_summary_and_preserves_full_request():
    turns = _turns(8)
    repository = _Repository(turns)
    first = await _coordinator(
        repository,
        _Gateway(_SUMMARY_JSON),
    ).prepare(_request(turns))
    assert repository.summary is not None
    changed = (*turns[:-1], replace(turns[-1], response="edited"))
    original = _request(changed)
    gateway = _Gateway(_SUMMARY_JSON)

    result = await _coordinator(repository, gateway).prepare(original)

    assert result.outcome == "history_mismatch"
    assert result.request is original
    assert result.compression_state_version is None
    assert repository.deleted is True
    assert gateway.calls == []


def test_planner_receives_summary_as_host_wrapped_history_data():
    # The digest is validated by the service; this test only checks Planner
    # placement, so generate a real summary through the service in async tests.
    summary = ConversationSummary(
        session_id=7,
        version=1,
        covered_through_conversation_id=4,
        covered_turn_count=4,
        source_digest="a" * 64,
        active_goal="finish the edit",
        summary="Earlier dialogue state",
    )
    summary_message = AgentMessage(
        role=MessageRole.USER,
        content=json.dumps(summary.to_mapping(include_persistence=False)),
        origin=MessageOrigin.HOST_CONTEXT,
        attributes={"context_name": "conversation_summary"},
    )
    request = replace(
        _request(()),
        messages=(summary_message, AgentMessage(
            role=MessageRole.USER,
            content="current",
        )),
    )

    messages = build_planner_messages(request, PlanningCapabilities())

    assert "Earlier dialogue state" not in messages[0].content
    payload = json.loads(messages[1].content)
    assert "Earlier dialogue state" in payload["hostContext"][0]["content"]


@pytest.mark.asyncio
async def test_sqlite_compaction_repository_round_trip(tmp_path: Path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    try:
        await db.execute(
            "INSERT INTO ai_conversations (session_id, prompt, response) "
            "VALUES (?, ?, ?)",
            [7, "prompt", "response"],
        )
        repository = SqliteConversationCompactionRepository(db)
        turns = await repository.list_turns(7)
        assert len(turns) == 1
        summary = ConversationSummary(
            session_id=7,
            version=1,
            covered_through_conversation_id=turns[0].id,
            covered_turn_count=1,
            source_digest="b" * 64,
            active_goal="goal",
            decisions=("decision",),
            summary="summary",
        )

        await repository.save_summary(summary)
        loaded = await repository.load_summary(7)
        assert loaded == summary

        await repository.delete_summary(7)
        assert await repository.load_summary(7) is None
    finally:
        await db.close()
