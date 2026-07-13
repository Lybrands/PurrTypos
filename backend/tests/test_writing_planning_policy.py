from __future__ import annotations

import pytest

from agent_core.contracts import (
    AgentMessage,
    AgentRunRequest,
    ModelRequest,
    PlanningCapabilities,
)
from domains.writing.contracts import WritingDomainContext
from domains.writing.execution_state import WritingExecutionStateFactory
from domains.writing.planning import WritingPlanningPolicy
from services.task_planner import should_request_task_plan


def _request(
    text: str,
    *,
    book_id: str | None = "book-1",
    mode: str | None = "agent",
    tools_enabled: bool = True,
) -> AgentRunRequest:
    return AgentRunRequest(
        messages=(AgentMessage(role="user", content=text),),
        model=ModelRequest(provider="openai", model="test-model"),
        domain_context=WritingDomainContext(book_id=book_id).to_core_context(),
        mode=mode,
        tools_enabled=tools_enabled,
    )


@pytest.mark.parametrize(
    ("text", "book_id", "mode", "tools_enabled", "expected"),
    [
        ("", "book-1", "agent", True, False),
        ("       ", "book-1", "agent", True, False),
        ("1234567", "book-1", "agent", True, False),
        ("12345678", "book-1", "agent", True, True),
        ("12345678", None, "agent", True, False),
        ("12345678", "", "agent", True, False),
        ("12345678", "   ", "agent", True, True),
        ("12345678", "book-1", "agent", False, True),
        ("12345678", "book-1", "ask", False, False),
        ("12345678", "book-1", None, False, False),
        ("12345678", "book-1", "ask", True, True),
        ("12345678", "book-1", None, True, True),
        ("12345678", "book-1", "other", True, True),
        ("12345678", "book-1", " AGENT ", False, True),
    ],
)
def test_writing_policy_preserves_legacy_planner_gate(
    text: str,
    book_id: str | None,
    mode: str | None,
    tools_enabled: bool,
    expected: bool,
):
    request = _request(
        text,
        book_id=book_id,
        mode=mode,
        tools_enabled=tools_enabled,
    )
    capabilities = PlanningCapabilities(available_tool_names=frozenset())

    actual = WritingPlanningPolicy().should_plan(request, capabilities)
    legacy = should_request_task_plan(
        user_text=text,
        book_id=book_id,
        enable_agent_tools=tools_enabled,
        chat_agent_mode=mode,
    )

    assert actual is expected
    assert actual is legacy


def test_writing_policy_does_not_infer_tools_enabled_from_catalog_contents():
    request = _request("12345678", mode="ask", tools_enabled=True)
    empty = PlanningCapabilities(available_tool_names=frozenset())
    populated = PlanningCapabilities(available_tool_names=frozenset({"getSomething"}))

    policy = WritingPlanningPolicy()
    assert policy.should_plan(request, empty) is True
    assert policy.should_plan(request, populated) is True


def test_writing_context_round_trips_and_creates_detached_run_state():
    domain = WritingDomainContext(
        book_id="book-1",
        chapter_id="chapter-1",
        current_chapter_title="第一章",
        writing_chapters=({"id": "chapter-1", "title": "第一章"},),
        available_outlines=({"id": "outline-1", "title": "总纲"},),
        associated_chapter_ids=("chapter-2",),
        associated_outline_ids=("outline-1",),
        selected_memory_ids=(1, 2),
        selected_foreshadowing_ids=(3,),
        context_window_label="200k",
    )
    request = AgentRunRequest(
        messages=(AgentMessage(role="user", content="12345678"),),
        model=ModelRequest(provider="openai", model="test-model"),
        domain_context=domain.to_core_context(),
        context_window=200_000,
        tools_enabled=True,
    )

    restored = WritingDomainContext.from_core_context(request.domain_context)
    state = WritingExecutionStateFactory().create(request)
    state.domain["writingChapters"][0]["title"] = "已修改"

    assert restored == domain
    assert state.domain["bookId"] == "book-1"
    assert state.domain["contextWindow"] == "200k"
    assert restored.writing_chapters[0]["title"] == "第一章"
