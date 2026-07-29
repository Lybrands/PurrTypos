from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from agent_core.contracts import (
    AgentMessage,
    AgentRunRequest,
    ConversationSummary,
    ConversationTurn,
    DomainContext,
    MessageOrigin,
    MessageRole,
    ModelCompletion,
    ModelRequest,
    PlanningCapabilities,
)
from agent_core.planner import build_planner_messages
from application.conversation_compaction import (
    ConversationCompactionService,
    PostPlanningConversationContextOptimizer,
)
from database.connection import DatabaseConnection
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
            message=AgentMessage(
                role=MessageRole.ASSISTANT,
                content=response,
            ),
            model="summary-model",
        )

    async def stream(self, messages, invocation, signal=None):  # pragma: no cover
        raise AssertionError("conversation compaction must not stream")


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
        model=ModelRequest(provider="test", model="model"),
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


@pytest.mark.asyncio
async def test_initial_compaction_keeps_recent_raw_turns_and_injects_host_summary():
    turns = _turns(8)
    repository = _Repository(turns)
    gateway = _Gateway(_SUMMARY_JSON)
    started = []

    result = await ConversationCompactionService(repository, gateway).prepare(
        _request(turns),
        on_compaction_started=lambda payload: _record_started(started, payload),
    )

    assert result.outcome == "compacted"
    assert result.compacted_turn_count == 4
    assert result.retained_raw_turn_count == 4
    assert result.summary is repository.summary
    assert result.summary is not None
    assert result.summary.covered_turn_count == 4
    assert result.request.conversation_summary is result.summary
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
    assert started[0]["selectedTurnCount"] == 4
    assert started[0]["previousSummaryVersion"] is None
    assert started[0]["coveredTurnCountBefore"] == 0
    assert started[0]["pressureRatio"] >= 0.70


@pytest.mark.asyncio
async def test_short_conversation_does_not_add_a_summary_model_call():
    turns = _turns(7)
    repository = _Repository(turns)
    gateway = _Gateway(_SUMMARY_JSON)
    original = _request(turns, context_window=200_000)

    result = await ConversationCompactionService(repository, gateway).prepare(
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

    result = await ConversationCompactionService(repository, gateway).prepare(
        _request(turns, context_window=256_000)
    )

    assert result.outcome == "below_threshold"
    assert result.diagnostics["decisionReason"] == "below_pressure"
    assert result.diagnostics["pressureRatio"] < 0.70
    assert result.diagnostics["conversationTokens"] > 0
    assert gateway.calls == []


@pytest.mark.asyncio
async def test_tool_mode_uses_more_headroom_than_direct_mode():
    turns = _turns(8)
    tool_result = await ConversationCompactionService(
        _Repository(turns),
        _Gateway(_SUMMARY_JSON),
    ).prepare(_request(turns, tools_enabled=True))
    direct_result = await ConversationCompactionService(
        _Repository(turns),
        _Gateway(_SUMMARY_JSON),
    ).prepare(_request(turns, tools_enabled=False))

    assert tool_result.diagnostics["targetRatio"] < (
        direct_result.diagnostics["targetRatio"]
    )
    assert tool_result.diagnostics["contextReserveTokens"] > (
        direct_result.diagnostics["contextReserveTokens"]
    )


@pytest.mark.asyncio
async def test_resolved_plan_complexity_adjusts_post_planning_target():
    turns = _turns(8)
    simple = await ConversationCompactionService(
        _Repository(turns),
        _Gateway(_SUMMARY_JSON),
    ).prepare(
        _request(turns),
        provider_input_tokens=100_000,
        resolved_context_tokens=2_000,
        planned_step_count=2,
        planned_tool_count=1,
        selected_tool_count=1,
    )
    complex_result = await ConversationCompactionService(
        _Repository(turns),
        _Gateway(_SUMMARY_JSON),
    ).prepare(
        _request(turns),
        provider_input_tokens=100_000,
        resolved_context_tokens=2_000,
        planned_step_count=8,
        planned_tool_count=4,
        selected_tool_count=4,
    )

    assert simple.diagnostics["providerBudgetKind"] == "resolved"
    assert simple.diagnostics["contextEstimateKind"] == "resolved"
    assert complex_result.diagnostics["plannedToolCount"] == 4
    assert complex_result.diagnostics["expectedGrowthRounds"] == 5
    assert complex_result.diagnostics["targetRatio"] < (
        simple.diagnostics["targetRatio"]
    )


@pytest.mark.asyncio
async def test_compaction_updates_incrementally_from_persisted_summary():
    first_turns = _turns(8)
    repository = _Repository(first_turns)
    first = await ConversationCompactionService(
        repository,
        _Gateway(_SUMMARY_JSON),
    ).prepare(_request(first_turns))
    assert first.summary is not None

    repository.turns = _turns(12)
    gateway = _Gateway(_SUMMARY_JSON)
    second = await ConversationCompactionService(repository, gateway).prepare(
        _request(repository.turns)
    )

    assert second.outcome == "compacted"
    assert second.summary is not None
    assert second.summary.version == 2
    assert second.summary.covered_turn_count == 8
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
    first = await ConversationCompactionService(
        repository,
        _Gateway(_SUMMARY_JSON),
    ).prepare(_request(initial_turns))
    assert first.summary is not None

    repository.turns = _turns(12)
    raw_request = _request(repository.turns, context_window=256_000)
    preflight = await ConversationCompactionService(
        repository,
        _Gateway(_SUMMARY_JSON),
    ).prepare(raw_request)
    assert preflight.outcome == "reused"

    started = []
    optimizer = PostPlanningConversationContextOptimizer(
        ConversationCompactionService(
            repository,
            _Gateway(_SUMMARY_JSON),
        ),
        raw_request,
    )
    optimized = await optimizer.optimize(
        preflight.request,
        provider_input_tokens=22_000,
        resolved_context_tokens=4_000,
        output_reserve_tokens=1_024,
        planned_step_count=6,
        planned_tool_count=4,
        selected_tool_names=("read", "analyze", "write", "review"),
        on_compaction_started=lambda payload: _record_started(started, payload),
    )

    assert optimized.outcome == "compacted"
    assert optimized.summary_version == 2
    assert optimized.request.conversation_summary is repository.summary
    assert repository.summary is not None
    assert repository.summary.covered_turn_count > (
        first.summary.covered_turn_count
    )
    assert optimized.diagnostics["providerBudgetKind"] == "resolved"
    assert optimized.diagnostics["plannedToolCount"] == 4
    assert started[0]["coveredTurnCountBefore"] == 4


@pytest.mark.asyncio
async def test_generation_failure_advances_with_host_fallback_without_blocking():
    turns = _turns(8)
    repository = _Repository(turns)
    first = await ConversationCompactionService(
        repository,
        _Gateway(_SUMMARY_JSON),
    ).prepare(_request(turns))
    assert first.summary is not None
    repository.turns = _turns(12)

    result = await ConversationCompactionService(
        repository,
        _Gateway(RuntimeError("provider unavailable")),
    ).prepare(_request(repository.turns))

    assert result.outcome == "compacted_fallback"
    assert result.summary is repository.summary
    assert result.summary is not None
    assert result.summary.version == 2
    assert result.summary.covered_turn_count == 6
    assert result.retained_raw_turn_count == 6
    assert "主机按回合保留的原文摘录" in result.summary.summary
    assert result.diagnostics["failureStage"] == "generation"
    assert result.diagnostics["failureType"] == "RuntimeError"
    assert result.diagnostics["fallback"] == "host_extractive"
    assert result.diagnostics["decisionReason"] in {
        "soft_pressure",
        "hard_pressure",
    }


@pytest.mark.asyncio
async def test_initial_generation_failure_creates_persisted_host_fallback():
    turns = _turns(8)
    repository = _Repository(turns)
    original = _request(turns)

    result = await ConversationCompactionService(
        repository,
        _Gateway(RuntimeError("provider unavailable")),
    ).prepare(original)

    assert result.outcome == "compacted_fallback"
    assert result.request is not original
    assert result.request.conversation_summary is repository.summary
    assert result.summary is not None
    assert result.summary.covered_turn_count == 2
    assert result.compacted_turn_count == 2
    assert result.retained_raw_turn_count == 6


@pytest.mark.asyncio
async def test_invalid_summary_json_is_repaired_once():
    turns = _turns(8)
    repository = _Repository(turns)
    gateway = _Gateway('{"activeGoal":', _SUMMARY_JSON)

    result = await ConversationCompactionService(repository, gateway).prepare(
        _request(turns)
    )

    assert result.outcome == "compacted"
    assert len(gateway.calls) == 2
    assert "not one complete JSON object" in gateway.calls[1][0][-1].content


@pytest.mark.asyncio
async def test_history_mismatch_invalidates_summary_and_preserves_full_request():
    turns = _turns(8)
    repository = _Repository(turns)
    first = await ConversationCompactionService(
        repository,
        _Gateway(_SUMMARY_JSON),
    ).prepare(_request(turns))
    assert first.summary is not None
    changed = (*turns[:-1], replace(turns[-1], response="edited"))
    original = _request(changed)
    gateway = _Gateway(_SUMMARY_JSON)

    result = await ConversationCompactionService(repository, gateway).prepare(original)

    assert result.outcome == "history_mismatch"
    assert result.request is original
    assert result.request.conversation_summary is None
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
    request = replace(_request(()), conversation_summary=summary)

    messages = build_planner_messages(request, PlanningCapabilities())

    assert "Earlier dialogue state" not in messages[0].content
    payload = json.loads(messages[1].content)
    assert payload["conversationSummary"]["summary"] == "Earlier dialogue state"


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
