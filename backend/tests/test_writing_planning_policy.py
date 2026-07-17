from __future__ import annotations

import json
from dataclasses import replace

import pytest

from agent_core.contracts import (
    AgentMessage,
    AgentRunRequest,
    ModelRequest,
    PlanningCapabilities,
    PlanningConstraints,
)
from agent_core.planner import build_planner_messages
from domains.writing.contracts import WritingDomainContext
from domains.writing.execution_state import WritingExecutionStateFactory
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
    associated_chapter_ids: tuple[str, ...] = (),
    associated_outline_ids: tuple[str, ...] = (),
    available_outlines: tuple[dict, ...] = (),
    selected_memory_ids: tuple[object, ...] = (),
    selected_foreshadowing_ids: tuple[object, ...] = (),
) -> AgentRunRequest:
    return AgentRunRequest(
        messages=(AgentMessage(role="user", content=text),),
        model=ModelRequest(provider="openai", model="test-model"),
        domain_context=WritingDomainContext(
            book_id=book_id,
            chapter_id=chapter_id,
            current_chapter_title=current_chapter_title,
            writing_chapters=writing_chapters,
            associated_chapter_ids=associated_chapter_ids,
            associated_outline_ids=associated_outline_ids,
            available_outlines=available_outlines,
            selected_memory_ids=selected_memory_ids,
            selected_foreshadowing_ids=selected_foreshadowing_ids,
        ).to_core_context(),
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


def test_writing_policy_does_not_infer_tools_enabled_from_catalog_contents():
    request = _request("12345678", mode="ask", tools_enabled=True)
    empty = PlanningCapabilities(available_tool_names=frozenset())
    populated = PlanningCapabilities(available_tool_names=frozenset({"getSomething"}))

    policy = WritingPlanningPolicy()
    assert policy.should_plan(request, empty) is True
    assert policy.should_plan(request, populated) is True


def _bound_chapter_capabilities(
    *,
    include_host_fact: bool = True,
    bound: bool = True,
    may_omit_chapter_id: bool = True,
    tools: frozenset[str] = frozenset({
        "getChapterContent",
        "listWritingChapters",
    }),
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
        host_planning_facts=facts,
        tool_guidance={
            "getChapterContent": {
                "purpose": "Read one chapter body.",
                "requires": ["listWritingChapters"],
            },
            "listWritingChapters": {
                "purpose": "Discover chapter locators.",
                "requires": [],
            },
        },
    )


def _full_bound_chapter_capabilities() -> PlanningCapabilities:
    tools = frozenset({
        *WRITING_TOOL_PLANNING_DEPENDENCIES,
        *(
            dependency
            for dependencies in WRITING_TOOL_PLANNING_DEPENDENCIES.values()
            for dependency in dependencies
        ),
    })
    return PlanningCapabilities(
        available_tool_names=tools,
        host_planning_facts={
            "currentChapter": {
                "bound": True,
                "singleChapterToolsMayOmitChapterId": True,
            },
        },
        tool_guidance={
            name: {
                "purpose": name,
                "requires": list(
                    WRITING_TOOL_PLANNING_DEPENDENCIES.get(name, ())
                ),
            }
            for name in tools
        },
    )


def _bound_chapter_request(text: str, *, chapter_id: str | None = "chapter-1"):
    return _request(
        text,
        chapter_id=chapter_id,
        current_chapter_title="第一章：北门雨夜",
        writing_chapters=(
            {"id": "chapter-1", "title": "第一章：北门雨夜"},
            {"id": "chapter-2", "title": "第二章：城内清晨"},
        ),
    )


@pytest.mark.parametrize(
    "text",
    [
        (
            "对照当前章节与关联大纲，找出两处不一致，"
            "并给出最小修改建议。"
        ),
        "读取本章正文，给出一百五十字以内的摘要。",
        "分析这章与既定大纲之间的连续性。",
        "总结已打开的正文，但不要修改任何内容。",
        "Read the current chapter and summarize it.",
        "Review the currently open chapter against the selected outline.",
        "读取第一章：北门雨夜并总结。",
        "读取 chapter-1 并总结。",
    ],
)
def test_bound_current_chapter_satisfies_redundant_catalog_dependency(text: str):
    constraints = WritingPlanningPolicy().planning_constraints(
        _bound_chapter_request(text),
        _bound_chapter_capabilities(),
    )

    assert constraints == PlanningConstraints(
        satisfied_tool_dependency_edges=frozenset({
            ("getChapterContent", "listWritingChapters"),
        }),
    )


def test_bound_current_chapter_waives_only_get_edge_in_full_catalog():
    request = _bound_chapter_request("读取当前章节并总结。")
    base = _full_bound_chapter_capabilities()
    constraints = WritingPlanningPolicy().planning_constraints(request, base)

    messages = build_planner_messages(
        request,
        replace(base, constraints=constraints),
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


def test_compound_current_read_keeps_catalog_dependency_for_other_consumers():
    request = _bound_chapter_request("读取当前章节，然后新增一个章节。")
    base = _full_bound_chapter_capabilities()
    constraints = WritingPlanningPolicy().planning_constraints(request, base)

    messages = build_planner_messages(
        request,
        replace(base, constraints=constraints),
    )

    host_context = json.loads(messages[0].content.rsplit("\n", 1)[-1])
    user_payload = json.loads(messages[1].content)
    assert constraints.satisfied_tool_dependency_edges == frozenset({
        ("getChapterContent", "listWritingChapters"),
    })
    assert "listWritingChapters" in user_payload["availableTools"]
    assert host_context["toolGuidance"]["createWritingChapter"]["requires"] == [
        "listWritingChapters"
    ]


@pytest.mark.parametrize(
    "text",
    [
        "列出章节目录，再读取当前章节。",
        "查看章节后，再读取当前章节。",
        "调用 listWritingChapters 后读取当前章节。",
        "对比当前章节和下一章的节奏。",
        "对比当前章节与所有章节。",
        "对比当前章节与第二章：城内清晨。",
        "对比当前章节与第三章的伏笔。",
        "Compare the current chapter with other chapters.",
        "Show the chapter catalog, then read the current chapter.",
        "读取当前章节并评估全书目录结构。",
        "阅读本章并检查各章标题与顺序。",
        "对比当前章节和相邻章节。",
        "对比本章与前文的伏笔。",
        "不要读取当前章节，只分析前文。",
        "不要读取当前章节，只回答这个写作问题。",
        "不读当前章节，只回答这个写作问题。",
        "Compare the current chapter with chapter 2.",
        "How many chapters are there? Then read the current chapter.",
        "Do not analyze the currently open chapter; answer generally.",
        "帮我分析这一段文字。",
    ],
)
def test_explicit_catalog_broader_or_ambiguous_chapter_scope_fails_open(text: str):
    constraints = WritingPlanningPolicy().planning_constraints(
        _bound_chapter_request(text),
        _bound_chapter_capabilities(),
    )

    assert constraints == PlanningConstraints()


@pytest.mark.parametrize(
    "run_request",
    [
        _request(
            "读取 chapter-10 的正文并总结。",
            chapter_id="chapter-1",
            current_chapter_title="第一章",
            writing_chapters=({"id": "chapter-1", "title": "第一章"},),
        ),
        _request(
            "分析雨夜场景并创建新章节。",
            chapter_id="chapter-1",
            current_chapter_title="雨",
            writing_chapters=({"id": "chapter-1", "title": "雨"},),
        ),
        _request(
            "读取第一章番外并总结。",
            chapter_id="chapter-1",
            current_chapter_title="第一章",
            writing_chapters=({"id": "chapter-1", "title": "第一章"},),
        ),
    ],
)
def test_bound_chapter_id_and_title_substrings_do_not_waive_dependency(
    run_request: AgentRunRequest,
):
    constraints = WritingPlanningPolicy().planning_constraints(
        run_request,
        _bound_chapter_capabilities(),
    )

    assert constraints == PlanningConstraints()


@pytest.mark.parametrize(
    "run_request",
    [
        _request(
            "读取当前章节并总结。",
            chapter_id="stale-chapter",
            current_chapter_title="旧章节",
            writing_chapters=({"id": "chapter-1", "title": "第一章"},),
        ),
        _request(
            "读取当前章节并总结。",
            chapter_id="volume-1",
            current_chapter_title="第一卷",
            writing_chapters=(
                {"id": "volume-1", "title": "第一卷", "parent_id": None},
                {
                    "id": "chapter-1",
                    "title": "第一章",
                    "parent_id": "volume-1",
                },
            ),
        ),
    ],
)
def test_stale_or_non_leaf_bound_locator_does_not_waive_dependency(
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
            _bound_chapter_request("读取当前章节并总结。", chapter_id=None),
            _bound_chapter_capabilities(),
        ),
        (
            _bound_chapter_request("读取当前章节并总结。"),
            _bound_chapter_capabilities(include_host_fact=False),
        ),
        (
            _bound_chapter_request("读取当前章节并总结。"),
            _bound_chapter_capabilities(bound=False),
        ),
        (
            _bound_chapter_request("读取当前章节并总结。"),
            _bound_chapter_capabilities(may_omit_chapter_id=False),
        ),
        (
            _bound_chapter_request("读取当前章节并总结。"),
            _bound_chapter_capabilities(
                tools=frozenset({"getChapterContent"}),
            ),
        ),
        (
            _bound_chapter_request("读取当前章节并总结。"),
            _bound_chapter_capabilities(
                tools=frozenset({"listWritingChapters"}),
            ),
        ),
    ],
)
def test_missing_or_inconsistent_bound_chapter_capability_fails_open(
    run_request: AgentRunRequest,
    capabilities: PlanningCapabilities,
):
    constraints = WritingPlanningPolicy().planning_constraints(
        run_request,
        capabilities,
    )

    assert constraints == PlanningConstraints()


def test_bound_chapter_constraint_keeps_catalog_and_cleans_only_get_dependency():
    request = _bound_chapter_request(
        "读取当前章节，对照关联大纲找出两处不一致。"
    )
    base = _bound_chapter_capabilities()
    constraints = WritingPlanningPolicy().planning_constraints(request, base)

    messages = build_planner_messages(
        request,
        replace(base, constraints=constraints),
    )

    host_context = json.loads(messages[0].content.rsplit("\n", 1)[-1])
    user_payload = json.loads(messages[1].content)
    assert user_payload["availableTools"] == [
        "getChapterContent",
        "listWritingChapters",
    ]
    assert host_context["toolGuidance"] == {
        "getChapterContent": {
            "purpose": "Read one chapter body.",
            "requires": [],
        },
        "listWritingChapters": {
            "purpose": "Discover chapter locators.",
            "requires": [],
        },
    }
    assert host_context["planningConstraints"] == {
        "satisfiedToolDependencyEdges": [{
            "tool": "getChapterContent",
            "dependency": "listWritingChapters",
        }],
    }


def test_p5_uses_edge_waiver_and_keeps_catalog_available():
    request = _request(
        (
            "对照当前章节与关联大纲，找出两处不一致，"
            "并给出最小修改建议。"
        ),
        chapter_id="chapter-1",
        current_chapter_title="第一章：北门雨夜",
        writing_chapters=(
            {"id": "chapter-1", "title": "第一章：北门雨夜"},
        ),
        associated_outline_ids=("outline-1",),
        available_outlines=(
            {"id": "outline-1", "title": "第一章情节大纲"},
        ),
    )
    tools = frozenset({
        "getChapterContent",
        "listWritingChapters",
        "queryOutline",
        "listOutlines",
    })
    base = PlanningCapabilities(
        available_tool_names=tools,
        host_planning_facts={
            "currentChapter": {
                "bound": True,
                "singleChapterToolsMayOmitChapterId": True,
            },
            "associatedOutlines": {
                "selectedCount": 1,
                "completeCount": 1,
                "items": [{
                    "ordinal": 1,
                    "status": "complete",
                    "readTool": "queryOutline",
                    "locatorAvailableToExecution": True,
                }],
            },
        },
        tool_guidance={
            "getChapterContent": {
                "purpose": "Read one chapter body.",
                "requires": ["listWritingChapters"],
            },
            "listWritingChapters": {
                "purpose": "Discover chapter locators.",
                "requires": [],
            },
            "queryOutline": {
                "purpose": "Read one outline body.",
                "requires": ["listOutlines"],
            },
            "listOutlines": {
                "purpose": "Discover outline locators.",
                "requires": [],
            },
        },
    )

    constraints = WritingPlanningPolicy().planning_constraints(request, base)
    messages = build_planner_messages(
        request,
        replace(base, constraints=constraints),
    )

    assert constraints.context_satisfied_tool_names == frozenset({
        "queryOutline",
    })
    assert constraints.satisfied_tool_dependency_edges == frozenset({
        ("getChapterContent", "listWritingChapters"),
    })
    user_payload = json.loads(messages[1].content)
    host_context = json.loads(messages[0].content.rsplit("\n", 1)[-1])
    assert user_payload["availableTools"] == [
        "getChapterContent",
        "listOutlines",
        "listWritingChapters",
    ]
    assert host_context["toolGuidance"]["getChapterContent"]["requires"] == []


def _selected_evidence_request(
    text: str,
    *,
    current_is_associated: bool = True,
) -> AgentRunRequest:
    associated_id = "chapter-current" if current_is_associated else "chapter-selected"
    return _request(
        text,
        chapter_id="chapter-current",
        current_chapter_title="第一章：北门雨夜",
        writing_chapters=(
            {"id": "chapter-current", "title": "第一章：北门雨夜"},
            {"id": "chapter-selected", "title": "第二章：城内清晨"},
        ),
        associated_chapter_ids=(associated_id,),
        selected_memory_ids=(101, 102),
    )


def _selected_evidence_capabilities(
    *,
    memory_status: str = "complete",
    chapter_status: str = "complete",
    memory_requested_count: int = 2,
    chapter_selected_count: int = 1,
    tools: frozenset[str] | None = None,
) -> PlanningCapabilities:
    available = tools or frozenset({
        "searchSparkIdeas",
        "searchMemories",
        "getChapterContent",
        "listWritingChapters",
        "getStoryHealthDashboard",
        "listBookCharacters",
        "listSettingEntities",
        "deleteCharacter",
    })
    memory_complete = memory_requested_count if memory_status == "complete" else 0
    memory_truncated = memory_requested_count if memory_status == "truncated" else 0
    memory_not_injected = (
        memory_requested_count if memory_status == "not_injected" else 0
    )
    chapter_complete = (
        chapter_selected_count if chapter_status == "complete" else 0
    )
    return PlanningCapabilities(
        available_tool_names=available,
        host_planning_facts={
            "currentChapter": {
                "bound": True,
                "singleChapterToolsMayOmitChapterId": True,
            },
            "selectedMemories": {
                "requestedCount": memory_requested_count,
                "status": memory_status,
                "completeCount": memory_complete,
                "truncatedCount": memory_truncated,
                "notInjectedCount": memory_not_injected,
                "searchTools": ["searchSparkIdeas"],
                "locatorAvailableToExecution": True,
            },
            "associatedChapters": {
                "selectedCount": chapter_selected_count,
                "completeCount": chapter_complete,
                "items": [
                    {
                        "ordinal": index,
                        "status": chapter_status,
                        "readTool": "getChapterContent",
                        "locatorAvailableToExecution": True,
                    }
                    for index in range(1, chapter_selected_count + 1)
                ],
            },
        },
        tool_guidance={
            name: {"purpose": name, "requires": []}
            for name in available
        },
    )


def test_p2_complete_selected_evidence_removes_redundant_reads_and_broadening():
    request = _selected_evidence_request(
        "只依据当前选中的设定、记忆和章节，列出三个可能的剧情连续性风险；"
        "每一点说明它引用了哪项上下文。不要修改内容。"
    )
    capabilities = _selected_evidence_capabilities()

    constraints = WritingPlanningPolicy().planning_constraints(
        request,
        capabilities,
    )
    messages = build_planner_messages(
        request,
        replace(capabilities, constraints=constraints),
    )

    assert constraints.context_satisfied_tool_names == frozenset({
        "searchSparkIdeas",
        "getChapterContent",
    })
    assert constraints.planning_excluded_tool_names == (
        capabilities.available_tool_names
        - constraints.context_satisfied_tool_names
    )
    assert json.loads(messages[1].content)["availableTools"] == []
    host_context = json.loads(messages[0].content.rsplit("\n", 1)[-1])
    assert "toolGuidance" not in host_context
    assert host_context["planningConstraints"]["contextSatisfiedTools"] == [
        "getChapterContent",
        "searchSparkIdeas",
    ]
    assert "getStoryHealthDashboard" in host_context[
        "planningConstraints"
    ]["planningExcludedTools"]
    assert capabilities.available_tool_names == frozenset({
        "searchSparkIdeas",
        "searchMemories",
        "getChapterContent",
        "listWritingChapters",
        "getStoryHealthDashboard",
        "listBookCharacters",
        "listSettingEntities",
        "deleteCharacter",
    })


def test_complete_memory_and_chapter_constraints_are_independent():
    request = _selected_evidence_request(
        "依据当前选中的设定、记忆和章节分析连续性风险。"
    )

    memory_only = WritingPlanningPolicy().planning_constraints(
        request,
        _selected_evidence_capabilities(chapter_status="truncated"),
    )
    chapter_only = WritingPlanningPolicy().planning_constraints(
        request,
        _selected_evidence_capabilities(memory_status="truncated"),
    )

    assert memory_only.context_satisfied_tool_names == frozenset({
        "searchSparkIdeas",
    })
    assert chapter_only.context_satisfied_tool_names == frozenset({
        "getChapterContent",
    })
    assert memory_only.planning_excluded_tool_names == frozenset()
    assert chapter_only.planning_excluded_tool_names == frozenset()


def test_read_only_action_limit_does_not_imply_an_evidence_scope_limit():
    constraints = WritingPlanningPolicy().planning_constraints(
        _selected_evidence_request(
            "分析已选记忆和关联章节，不要修改内容。"
        ),
        _selected_evidence_capabilities(),
    )

    assert constraints.context_satisfied_tool_names == frozenset({
        "searchSparkIdeas",
        "getChapterContent",
    })
    assert constraints.planning_excluded_tool_names == frozenset()


@pytest.mark.parametrize(
    "text",
    [
        (
            "只根据已选记忆和关联章节分析，并调用 getStoryHealthDashboard "
            "补充证据；不要修改内容。"
        ),
        (
            "不要只分析已选记忆和关联章节；请调用 listBookCharacters "
            "扩展证据，不要修改内容。"
        ),
        "只分析已选记忆和关联章节，故事健康度也要看，不要修改内容。",
        "并非只分析已选记忆和关联章节，人物列表也要查。",
        (
            "Only analyze selected memories and associated chapters; "
            "story health must also be checked."
        ),
    ],
)
def test_explicit_broadening_request_keeps_supplemental_tools_available(
    text: str,
):
    constraints = WritingPlanningPolicy().planning_constraints(
        _selected_evidence_request(text),
        _selected_evidence_capabilities(),
    )

    assert constraints.context_satisfied_tool_names == frozenset({
        "searchSparkIdeas",
        "getChapterContent",
    })
    assert constraints.planning_excluded_tool_names == frozenset()


def test_selected_chapter_does_not_select_a_later_character_setting_phrase():
    constraints = WritingPlanningPolicy().planning_constraints(
        _selected_evidence_request(
            "只根据已选章节和人物设定分析，不要修改内容。"
        ),
        _selected_evidence_capabilities(),
    )

    assert constraints.context_satisfied_tool_names == frozenset({
        "getChapterContent",
    })
    assert constraints.planning_excluded_tool_names == frozenset()


@pytest.mark.parametrize(
    "text",
    [
        "只分析已选记忆和勋章图案，不要扩大证据范围。",
        "只分析已选记忆和文章风格，不要扩大证据范围。",
        "只分析已选记忆和规章制度，不要扩大证据范围。",
    ],
)
def test_chapter_suffix_inside_other_words_does_not_select_a_chapter(
    text: str,
):
    capabilities = _selected_evidence_capabilities()
    constraints = WritingPlanningPolicy().planning_constraints(
        _selected_evidence_request(text),
        capabilities,
    )

    assert constraints.context_satisfied_tool_names == frozenset({
        "searchSparkIdeas",
    })
    assert "getChapterContent" not in constraints.context_satisfied_tool_names


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "重新搜索最新的已选记忆，再依据关联章节分析风险。",
            frozenset({"getChapterContent"}),
        ),
        (
            "重搜已选记忆，再依据关联章节分析风险。",
            frozenset({"getChapterContent"}),
        ),
        (
            "只分析已选记忆和关联章节，同时搜索角色年龄。",
            frozenset({"getChapterContent"}),
        ),
        (
            "Only analyze selected memories and associated chapters, "
            "and search character ages.",
            frozenset({"getChapterContent"}),
        ),
        (
            "不能只分析已选记忆和关联章节，还要检索人物关系。",
            frozenset({"getChapterContent"}),
        ),
        (
            "依据已选记忆和关联章节，并搜索更多其他记忆来分析风险。",
            frozenset({"getChapterContent"}),
        ),
        (
            "明确调用 getChapterContent 重读关联章节，再依据已选记忆分析。",
            frozenset({"searchSparkIdeas"}),
        ),
        (
            "重读关联章节，再依据已选记忆分析。",
            frozenset({"searchSparkIdeas"}),
        ),
        (
            "依据已选记忆、关联章节和其他章节分析风险。",
            frozenset({"searchSparkIdeas"}),
        ),
    ],
)
def test_fresh_or_broader_selected_evidence_scope_fails_open_per_family(
    text: str,
    expected: frozenset[str],
):
    constraints = WritingPlanningPolicy().planning_constraints(
        _selected_evidence_request(text),
        _selected_evidence_capabilities(),
    )

    assert constraints.context_satisfied_tool_names == expected
    assert constraints.planning_excluded_tool_names == frozenset()


@pytest.mark.parametrize(
    ("memory_count", "chapter_count"),
    [(0, 1), (3, 1), (2, 0), (2, 2)],
)
def test_inconsistent_selected_evidence_counts_fail_open(
    memory_count: int,
    chapter_count: int,
):
    constraints = WritingPlanningPolicy().planning_constraints(
        _selected_evidence_request(
            "依据当前选中的设定、记忆和章节分析风险。"
        ),
        _selected_evidence_capabilities(
            memory_requested_count=memory_count,
            chapter_selected_count=chapter_count,
        ),
    )

    expected = set()
    if memory_count == 2:
        expected.add("searchSparkIdeas")
    if chapter_count == 1:
        expected.add("getChapterContent")
    assert constraints.context_satisfied_tool_names == frozenset(expected)
    assert constraints.planning_excluded_tool_names == frozenset()


def test_negated_search_keeps_complete_selected_memory_satisfied():
    capabilities = _selected_evidence_capabilities()
    constraints = WritingPlanningPolicy().planning_constraints(
        _selected_evidence_request(
            "只根据已选记忆和关联章节分析，不要再搜索，不要修改内容。"
        ),
        capabilities,
    )

    assert constraints.context_satisfied_tool_names == frozenset({
        "searchSparkIdeas",
        "getChapterContent",
    })
    assert constraints.planning_excluded_tool_names == (
        capabilities.available_tool_names
        - constraints.context_satisfied_tool_names
    )


def test_current_and_associated_chapter_must_share_the_injected_locator():
    constraints = WritingPlanningPolicy().planning_constraints(
        _selected_evidence_request(
            "对照当前章节与关联章节，依据已选记忆分析风险。不要修改内容。",
            current_is_associated=False,
        ),
        _selected_evidence_capabilities(),
    )

    assert constraints.context_satisfied_tool_names == frozenset({
        "searchSparkIdeas",
    })
    assert constraints.planning_excluded_tool_names == frozenset()


def test_numbered_non_associated_chapter_is_not_treated_as_selected():
    constraints = WritingPlanningPolicy().planning_constraints(
        _selected_evidence_request(
            "只根据已选记忆和第二章分析连续性，不要修改内容。"
        ),
        _selected_evidence_capabilities(),
    )

    assert constraints.context_satisfied_tool_names == frozenset({
        "searchSparkIdeas",
    })
    assert constraints.planning_excluded_tool_names == frozenset()


def test_short_named_non_associated_chapter_remains_readable():
    request = _request(
        "只根据已选记忆和尾声章节分析连续性，不要修改内容。",
        chapter_id="chapter-current",
        current_chapter_title="第一章",
        writing_chapters=(
            {"id": "chapter-current", "title": "第一章"},
            {"id": "chapter-ending", "title": "尾声"},
        ),
        associated_chapter_ids=("chapter-current",),
        selected_memory_ids=(101, 102),
    )

    constraints = WritingPlanningPolicy().planning_constraints(
        request,
        _selected_evidence_capabilities(),
    )

    assert constraints.context_satisfied_tool_names == frozenset({
        "searchSparkIdeas",
    })
    assert constraints.planning_excluded_tool_names == frozenset()


def test_complete_selected_evidence_only_hides_tools_present_in_catalog():
    constraints = WritingPlanningPolicy().planning_constraints(
        _selected_evidence_request(
            "依据当前选中的设定、记忆和章节分析风险。"
        ),
        _selected_evidence_capabilities(
            tools=frozenset({"getChapterContent", "listWritingChapters"}),
        ),
    )

    assert constraints.context_satisfied_tool_names == frozenset({
        "getChapterContent",
    })
    assert constraints.planning_excluded_tool_names == frozenset()
    assert "listWritingChapters" not in constraints.context_satisfied_tool_names


def _outline_capabilities(
    statuses: tuple[str, ...],
    *,
    selected_count: int | None = None,
    complete_count: int | None = None,
    include_query_tool: bool = True,
) -> PlanningCapabilities:
    selected = len(statuses) if selected_count is None else selected_count
    complete = (
        sum(status == "complete" for status in statuses)
        if complete_count is None
        else complete_count
    )
    tools = {"getChapterContent", "getGlobalOutline", "listOutlines"}
    if include_query_tool:
        tools.add("queryOutline")
    return PlanningCapabilities(
        available_tool_names=frozenset(tools),
        host_planning_facts={
            "associatedOutlines": {
                "selectedCount": selected,
                "completeCount": complete,
                "items": [
                    {
                        "ordinal": index,
                        "status": status,
                        "readTool": "queryOutline",
                        "locatorAvailableToExecution": True,
                    }
                    for index, status in enumerate(statuses, start=1)
                ],
            },
        },
        tool_guidance={
            name: {"purpose": name, "requires": []}
            for name in tools
        },
    )


def _selected_outline_request(text: str) -> AgentRunRequest:
    return _request(
        text,
        associated_outline_ids=("selected-outline",),
        available_outlines=(
            {"id": "selected-outline", "title": "第一章情节大纲"},
            {"id": "other-outline", "title": "第二章伏线大纲"},
        ),
    )


@pytest.mark.parametrize(
    "text",
    [
        "对照当前章节与关联大纲，找出两处不一致并给出建议。",
        "不要重新读取关联大纲，直接按已选大纲分析冲突。",
        "无需刷新，依据所选大纲与当前章节比较。",
        "依据最新章节，对照关联大纲分析连续性。",
        "Compare the current chapter with the selected outline.",
        "Compare the latest chapter with the selected outline.",
    ],
)
def test_complete_selected_outline_satisfies_query_for_narrow_analysis(text: str):
    capabilities = _outline_capabilities(("complete",))

    constraints = WritingPlanningPolicy().planning_constraints(
        _selected_outline_request(text),
        capabilities,
    )

    assert constraints == PlanningConstraints(
        context_satisfied_tool_names=frozenset({"queryOutline"})
    )
    assert capabilities.constraints == PlanningConstraints()
    assert "queryOutline" in capabilities.available_tool_names


@pytest.mark.parametrize(
    "text",
    [
        "重新读取关联大纲后，再与当前章节比较。",
        "重读关联大纲后，再与当前章节比较。",
        "刷新并读取最新的关联大纲，然后分析差异。",
        "直接对照最新关联大纲与当前章节。",
        "对照关联大纲，并读取其他大纲作补充。",
        "对照关联大纲和第二章伏线大纲。",
        "Refresh the selected outline before comparing it.",
        "Compare the latest selected outline with the current chapter.",
        "Compare the selected outline with another outline.",
    ],
)
def test_explicit_fresh_or_other_outline_request_keeps_query_available(text: str):
    constraints = WritingPlanningPolicy().planning_constraints(
        _selected_outline_request(text),
        _outline_capabilities(("complete",)),
    )

    assert constraints == PlanningConstraints()


@pytest.mark.parametrize(
    "statuses",
    [
        ("truncated",),
        ("not_injected",),
        ("complete", "truncated"),
        ("complete", "not_injected"),
    ],
)
def test_incomplete_or_mixed_outline_evidence_keeps_query_available(
    statuses: tuple[str, ...],
):
    constraints = WritingPlanningPolicy().planning_constraints(
        _selected_outline_request("对照当前章节与关联大纲分析差异。"),
        _outline_capabilities(statuses),
    )

    assert constraints == PlanningConstraints()


@pytest.mark.parametrize(
    "capabilities",
    [
        _outline_capabilities((), selected_count=0, complete_count=0),
        _outline_capabilities(("complete",), selected_count=2, complete_count=2),
        _outline_capabilities(("complete",), include_query_tool=False),
        PlanningCapabilities(
            available_tool_names=frozenset({"queryOutline"}),
            host_planning_facts={},
        ),
    ],
)
def test_missing_or_inconsistent_outline_facts_fail_open(
    capabilities: PlanningCapabilities,
):
    constraints = WritingPlanningPolicy().planning_constraints(
        _selected_outline_request("对照当前章节与关联大纲分析差异。"),
        capabilities,
    )

    assert constraints == PlanningConstraints()


def test_outline_fact_count_must_match_the_request_selection():
    request = _request(
        "只根据已选大纲分析结构，不要扩大证据范围。",
        associated_outline_ids=("selected-outline", "second-outline"),
        available_outlines=(
            {"id": "selected-outline", "title": "第一章情节大纲"},
            {"id": "second-outline", "title": "第二章情节大纲"},
        ),
    )

    constraints = WritingPlanningPolicy().planning_constraints(
        request,
        _outline_capabilities(("complete",)),
    )

    assert constraints == PlanningConstraints()


@pytest.mark.parametrize(
    "invalid_item",
    [
        {
            "ordinal": 2,
            "status": "complete",
            "readTool": "queryOutline",
            "locatorAvailableToExecution": True,
        },
        {
            "ordinal": 1,
            "status": "complete",
            "readTool": "getGlobalOutline",
            "locatorAvailableToExecution": True,
        },
    ],
)
def test_outline_fact_item_contract_must_be_exact(invalid_item: dict):
    capabilities = PlanningCapabilities(
        available_tool_names=frozenset({"queryOutline"}),
        host_planning_facts={
            "associatedOutlines": {
                "selectedCount": 1,
                "completeCount": 1,
                "items": [invalid_item],
            },
        },
    )

    constraints = WritingPlanningPolicy().planning_constraints(
        _selected_outline_request(
            "只根据已选大纲分析结构，不要扩大证据范围。"
        ),
        capabilities,
    )

    assert constraints == PlanningConstraints()


def test_short_selected_titles_do_not_match_unrelated_substrings():
    request = _request(
        "分析叙事顺序和故事主线，不要修改内容。",
        writing_chapters=({"id": "chapter-1", "title": "序"},),
        associated_chapter_ids=("chapter-1",),
        associated_outline_ids=("outline-1",),
        available_outlines=({"id": "outline-1", "title": "线"},),
    )
    capabilities = PlanningCapabilities(
        available_tool_names=frozenset({
            "getChapterContent",
            "queryOutline",
        }),
        host_planning_facts={
            "associatedChapters": {
                "selectedCount": 1,
                "completeCount": 1,
                "items": [{
                    "ordinal": 1,
                    "status": "complete",
                    "readTool": "getChapterContent",
                    "locatorAvailableToExecution": True,
                }],
            },
            "associatedOutlines": {
                "selectedCount": 1,
                "completeCount": 1,
                "items": [{
                    "ordinal": 1,
                    "status": "complete",
                    "readTool": "queryOutline",
                    "locatorAvailableToExecution": True,
                }],
            },
        },
    )

    constraints = WritingPlanningPolicy().planning_constraints(
        request,
        capabilities,
    )

    assert constraints == PlanningConstraints()


def test_common_three_character_title_requires_an_explicit_reference():
    request = _request(
        "只分析人物故事线，不要扩大证据范围。",
        writing_chapters=({"id": "chapter-1", "title": "故事线"},),
        associated_chapter_ids=("chapter-1",),
        associated_outline_ids=("outline-1",),
        available_outlines=({"id": "outline-1", "title": "故事线"},),
    )
    capabilities = PlanningCapabilities(
        available_tool_names=frozenset({
            "getChapterContent",
            "queryOutline",
        }),
        host_planning_facts={
            "associatedChapters": {
                "selectedCount": 1,
                "completeCount": 1,
                "items": [{
                    "ordinal": 1,
                    "status": "complete",
                    "readTool": "getChapterContent",
                    "locatorAvailableToExecution": True,
                }],
            },
            "associatedOutlines": {
                "selectedCount": 1,
                "completeCount": 1,
                "items": [{
                    "ordinal": 1,
                    "status": "complete",
                    "readTool": "queryOutline",
                    "locatorAvailableToExecution": True,
                }],
            },
        },
    )

    constraints = WritingPlanningPolicy().planning_constraints(
        request,
        capabilities,
    )

    assert constraints == PlanningConstraints()


def test_global_outline_remains_available_when_selected_outline_is_satisfied():
    capabilities = _outline_capabilities(("complete",))

    constraints = WritingPlanningPolicy().planning_constraints(
        _selected_outline_request("对照关联大纲和全局大纲分析结构。"),
        capabilities,
    )

    assert constraints.context_satisfied_tool_names == frozenset({"queryOutline"})
    assert "getGlobalOutline" in capabilities.available_tool_names


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
    assert state.domain["selectedMemoryIds"] == [1, 2]
    assert state.domain["selectedForeshadowingIds"] == [3]
    assert restored.writing_chapters[0]["title"] == "第一章"
