from __future__ import annotations

import json
from dataclasses import replace

import pytest

from purra.contracts import (
    AgentMessage,
    AgentRunRequest,
    ContextBlock,
    ModelRequest,
    PlanningCapabilities,
    PlanningConstraints,
)
from purra.planner import build_planner_messages
from domains.writing.contracts import WritingDomainContext
from domains.writing.execution_state import WritingExecutionStateFactory
from domains.writing.context import WRITING_PLANNING_FACTS_CONTEXT
from domains.writing.planning import (
    WRITING_TOOL_PLANNING_DEPENDENCIES,
    WritingPlanningPolicy,
)


def _request(
    text: str,
    *,
    book_id: str | None = "book-1",
    mode: str | None = "agent",
    tools_enabled: bool = True,
    chapter_id: str | None = None,
    current_chapter_title: str | None = None,
    writing_chapters: tuple[dict, ...] = (),
) -> AgentRunRequest:
    return AgentRunRequest(
        messages=(AgentMessage(role="user", content=text),),
        model=ModelRequest(provider="openai", model="test-model"),
        domain_context=WritingDomainContext(
            book_id=book_id,
            chapter_id=chapter_id,
            current_chapter_title=current_chapter_title,
            writing_chapters=writing_chapters,
        ).to_core_context(),
        mode=mode,
        tools_enabled=tools_enabled,
    )


@pytest.mark.parametrize(
    ("text", "book_id", "mode", "tools_enabled", "expected"),
    [
        ("", "book-1", "agent", True, False),
        ("       ", "book-1", "agent", True, False),
        ("续写", None, "agent", True, False),
        ("续写", "book-1", "agent", True, True),
        ("续写", "book-1", "agent", False, True),
        ("续写", "book-1", "ask", False, False),
        ("续写", "book-1", "ask", True, True),
        ("续写", "book-1", None, False, False),
        ("续写", "book-1", None, True, True),
        ("续写", "book-1", "other", False, False),
        ("续写", "book-1", "other", True, True),
        ("续写", "book-1", " AGENT ", False, True),
    ],
)
def test_writing_policy_applies_the_planning_eligibility_gate(
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

    assert actual is expected


@pytest.mark.parametrize(
    ("mode", "tools_enabled", "expected"),
    [
        ("ask", True, True),
        ("ask", False, False),
        ("other", False, False),
    ],
)
def test_writing_policy_does_not_infer_tools_enabled_from_catalog_contents(
    mode: str,
    tools_enabled: bool,
    expected: bool,
):
    request = _request(
        "续写",
        mode=mode,
        tools_enabled=tools_enabled,
    )
    empty = PlanningCapabilities(available_tool_names=frozenset())
    populated = PlanningCapabilities(
        available_tool_names=frozenset({"getSomething"}),
    )

    policy = WritingPlanningPolicy()

    assert policy.should_plan(request, empty) is expected
    assert policy.should_plan(request, populated) is expected


def _bound_chapter_capabilities(
    *,
    include_host_fact: bool = True,
    bound: bool = True,
    may_omit_chapter_id: bool = True,
    tools: frozenset[str] = frozenset({
        "getChapterContent",
        "listWritingChapters",
    }),
    constraints: PlanningConstraints = PlanningConstraints(),
) -> PlanningCapabilities:
    facts = (
        {
            "currentChapter": {
                "bound": bound,
                "singleChapterToolsMayOmitChapterId": may_omit_chapter_id,
            },
        }
        if include_host_fact
        else {}
    )
    return PlanningCapabilities(
        available_tool_names=tools,
        planning_context_blocks=(
            ContextBlock(
                name=WRITING_PLANNING_FACTS_CONTEXT,
                content=json.dumps(facts),
                token_count=1,
            ),
        ),
        tool_guidance={
            name: {
                "purpose": name,
                "requires": list(
                    WRITING_TOOL_PLANNING_DEPENDENCIES.get(name, ())
                ),
            }
            for name in tools
        },
        constraints=constraints,
    )


def _bound_chapter_request(
    text: str,
    *,
    chapter_id: str | None = "chapter-1",
) -> AgentRunRequest:
    return _request(
        text,
        chapter_id=chapter_id,
        current_chapter_title="chapter-one",
        writing_chapters=(
            {"id": "chapter-1", "title": "chapter-one"},
            {"id": "chapter-2", "title": "chapter-two"},
        ),
    )


@pytest.mark.parametrize(
    "text",
    [
        "",
        "x",
        "request-without-domain-phrases",
        "任意内容",
    ],
)
def test_bound_chapter_constraint_does_not_parse_prompt_text(text: str):
    constraints = WritingPlanningPolicy().planning_constraints(
        _bound_chapter_request(text),
        _bound_chapter_capabilities(),
    )

    assert constraints == PlanningConstraints(
        satisfied_tool_dependency_edges=frozenset({
            ("getChapterContent", "listWritingChapters"),
        }),
    )


def test_bound_chapter_waives_only_the_single_chapter_read_edge():
    tools = frozenset({
        *WRITING_TOOL_PLANNING_DEPENDENCIES,
        *(
            dependency
            for dependencies in WRITING_TOOL_PLANNING_DEPENDENCIES.values()
            for dependency in dependencies
        ),
    })
    capabilities = _bound_chapter_capabilities(tools=tools)
    request = _bound_chapter_request("opaque-request")
    constraints = WritingPlanningPolicy().planning_constraints(
        request,
        capabilities,
    )

    messages = build_planner_messages(
        request,
        replace(capabilities, constraints=constraints),
    )
    host_context = json.loads(messages[0].content.rsplit("\n", 1)[-1])
    user_payload = json.loads(messages[1].content)

    assert "listWritingChapters" in user_payload["availableTools"]
    assert host_context["toolGuidance"]["getChapterContent"]["requires"] == []
    for consumer in (
        "createWritingChapter",
        "batchGetChapterContents",
        "editChapterContent",
        "addForeshadowing",
    ):
        assert host_context["toolGuidance"][consumer]["requires"] == [
            "listWritingChapters"
        ]


@pytest.mark.parametrize(
    "run_request",
    [
        _request(
            "opaque-request",
            chapter_id="stale-chapter",
            current_chapter_title="stale",
            writing_chapters=(
                {"id": "chapter-1", "title": "chapter-one"},
            ),
        ),
        _request(
            "opaque-request",
            chapter_id="volume-1",
            current_chapter_title="volume-one",
            writing_chapters=(
                {"id": "volume-1", "title": "volume-one", "parent_id": None},
                {
                    "id": "chapter-1",
                    "title": "chapter-one",
                    "parent_id": "volume-1",
                },
            ),
        ),
    ],
)
def test_invalid_bound_locator_does_not_waive_dependency(
    run_request: AgentRunRequest,
):
    constraints = WritingPlanningPolicy().planning_constraints(
        run_request,
        _bound_chapter_capabilities(),
    )

    assert constraints == PlanningConstraints()


@pytest.mark.parametrize(
    ("run_request", "capabilities"),
    [
        (
            _bound_chapter_request("opaque-request", chapter_id=None),
            _bound_chapter_capabilities(),
        ),
        (
            _bound_chapter_request("opaque-request"),
            _bound_chapter_capabilities(include_host_fact=False),
        ),
        (
            _bound_chapter_request("opaque-request"),
            _bound_chapter_capabilities(bound=False),
        ),
        (
            _bound_chapter_request("opaque-request"),
            _bound_chapter_capabilities(may_omit_chapter_id=False),
        ),
        (
            _bound_chapter_request("opaque-request"),
            _bound_chapter_capabilities(
                tools=frozenset({"getChapterContent"}),
            ),
        ),
        (
            _bound_chapter_request("opaque-request"),
            _bound_chapter_capabilities(
                tools=frozenset({"listWritingChapters"}),
            ),
        ),
    ],
)
def test_missing_bound_chapter_fact_does_not_waive_dependency(
    run_request: AgentRunRequest,
    capabilities: PlanningCapabilities,
):
    constraints = WritingPlanningPolicy().planning_constraints(
        run_request,
        capabilities,
    )

    assert constraints == PlanningConstraints()


def test_structural_constraint_preserves_existing_constraints():
    base = PlanningConstraints(
        context_satisfied_tool_names=frozenset({"read-existing"}),
        planning_excluded_tool_names=frozenset({"write-blocked"}),
        satisfied_tool_dependency_edges=frozenset({
            ("consumer", "dependency"),
        }),
    )
    constraints = WritingPlanningPolicy().planning_constraints(
        _bound_chapter_request("opaque-request"),
        _bound_chapter_capabilities(constraints=base),
    )

    assert constraints == PlanningConstraints(
        context_satisfied_tool_names=frozenset({"read-existing"}),
        planning_excluded_tool_names=frozenset({"write-blocked"}),
        satisfied_tool_dependency_edges=frozenset({
            ("consumer", "dependency"),
            ("getChapterContent", "listWritingChapters"),
        }),
    )


def test_writing_context_round_trips_and_creates_detached_run_state():
    domain = WritingDomainContext(
        book_id="book-1",
        chapter_id="chapter-1",
        current_chapter_title="chapter-one",
        writing_chapters=(
            {"id": "chapter-1", "title": "chapter-one"},
        ),
        available_outlines=(
            {"id": "outline-1", "title": "outline-one"},
        ),
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
    state.domain["writingChapters"][0]["title"] = "changed"

    assert restored == domain
    assert state.domain["bookId"] == "book-1"
    assert state.domain["contextWindow"] == "200k"
    assert state.domain["selectedMemoryIds"] == [1, 2]
    assert state.domain["selectedForeshadowingIds"] == [3]
    assert restored.writing_chapters[0]["title"] == "chapter-one"
