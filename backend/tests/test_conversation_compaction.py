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
from application.conversation_compaction import ConversationCompactionService
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
    def __init__(self, response: str | Exception) -> None:
        self.response = response
        self.calls = []

    async def complete(self, messages, invocation, signal=None):
        self.calls.append((tuple(messages), invocation, signal))
        if isinstance(self.response, Exception):
            raise self.response
        return ModelCompletion(
            message=AgentMessage(
                role=MessageRole.ASSISTANT,
                content=self.response,
            ),
            model="summary-model",
        )

    async def stream(self, messages, invocation, signal=None):  # pragma: no cover
        raise AssertionError("conversation compaction must not stream")


def _turns(count: int) -> tuple[ConversationTurn, ...]:
    return tuple(
        ConversationTurn(id=index, prompt=f"u{index}", response=f"a{index}")
        for index in range(1, count + 1)
    )


def _request(turns: tuple[ConversationTurn, ...]) -> AgentRunRequest:
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
        tools_enabled=True,
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
        "u5", "a5", "u6", "a6", "u7", "a7", "u8", "a8", "current"
    ]
    payload = json.loads(gateway.calls[0][0][1].content)
    assert payload["existingSummary"] is None
    assert [row["user"] for row in payload["newTurns"]] == [
        "u1", "u2", "u3", "u4"
    ]
    assert started == [{
        "selectedTurnCount": 4,
        "previousSummaryVersion": None,
        "coveredTurnCountBefore": 0,
    }]


@pytest.mark.asyncio
async def test_short_conversation_does_not_add_a_summary_model_call():
    turns = _turns(7)
    repository = _Repository(turns)
    gateway = _Gateway(_SUMMARY_JSON)
    original = _request(turns)

    result = await ConversationCompactionService(repository, gateway).prepare(
        original
    )

    assert result.outcome == "below_threshold"
    assert result.request is original
    assert gateway.calls == []


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
        "u5", "u6", "u7", "u8"
    ]


@pytest.mark.asyncio
async def test_generation_failure_reuses_last_valid_summary_without_blocking():
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

    assert result.outcome == "generation_failed_reused"
    assert result.request.conversation_summary is first.summary
    assert result.retained_raw_turn_count == 8


@pytest.mark.asyncio
async def test_initial_generation_failure_preserves_full_history():
    turns = _turns(8)
    repository = _Repository(turns)
    original = _request(turns)

    result = await ConversationCompactionService(
        repository,
        _Gateway(RuntimeError("provider unavailable")),
    ).prepare(original)

    assert result.outcome == "generation_failed"
    assert result.request is original
    assert result.request.conversation_summary is None


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
