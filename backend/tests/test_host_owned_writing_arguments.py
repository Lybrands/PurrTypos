from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from purra.contracts import (
    ApprovalResult,
    ApprovalStatus,
    ExecutionState,
    ToolBatchOutcome,
    ToolBatchRequest,
    ToolCall,
)
from purra.tools.executor import CoreToolExecutor
from domains.writing.policies import WRITING_TOOL_POLICIES
from domains.writing.tools.host_arguments import (
    CURRENT_CHAPTER_DEFAULT_TOOLS,
    bind_host_writing_arguments,
)
from domains.writing.tools.contracts import ToolResult
from domains.writing.tools.scope import resolve_chapter_id_strict
from infrastructure.writing import (
    WritingSkillCatalog,
    WritingToolDependencies,
    build_writing_tool_catalog,
)


BACKEND_DIR = Path(__file__).resolve().parent.parent


class _RecordingSink:
    def __init__(self) -> None:
        self.events = []

    async def emit(self, event) -> None:
        self.events.append(event)


class _ImmediateApproval:
    def __init__(self) -> None:
        self.requests = []

    async def request(self, run_id, approval, event_sink, signal=None):
        self.requests.append(approval)
        return ApprovalResult("approval-1", ApprovalStatus.APPROVED)

    async def resolve(self, run_id, approval_id, decision):
        return None

    async def cancel_pending(self, run_id):
        return 0


def _skill_items() -> list[dict]:
    return WritingSkillCatalog(BACKEND_DIR / "skills").skill_items()


def _core_catalog(*, handler_overrides=None):
    return build_writing_tool_catalog(
        dependencies=WritingToolDependencies(
            object(),  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
        ),
        skill_items=tuple(_skill_items()),
        handler_overrides=handler_overrides,
    )


def _core_request(
    arguments: dict,
    *,
    name: str = "deleteCharacter",
    domain: dict | None = None,
) -> ToolBatchRequest:
    return ToolBatchRequest(
        run_id="run-1",
        calls=(ToolCall(
            id="call-1",
            name=name,
            arguments_json=json.dumps(arguments),
        ),),
        allowed_tool_names=frozenset({name}),
        state=ExecutionState(domain=domain or {"bookId": "book-a"}),
    )


def test_all_writing_schemas_hide_book_id_without_mutating_source():
    skill_items = _skill_items()
    source_snapshot = deepcopy(skill_items)
    core_catalog = _core_catalog()
    core_by_name = {
        schema.name: schema.parameters
        for schema in core_catalog.schemas()
    }

    assert set(core_by_name) == set(WRITING_TOOL_POLICIES)
    schemas_with_host_book_id = 0
    for source in skill_items:
        name = source["name"]
        source_parameters = source["parameters"]
        source_properties = source_parameters.get("properties") or {}
        source_required = source_parameters.get("required") or []
        expected_properties = {
            key: value
            for key, value in source_properties.items()
            if key != "bookId"
        }
        expected_required = [
            item for item in source_required if item != "bookId"
        ]
        if "bookId" in source_properties:
            schemas_with_host_book_id += 1

        visible = core_by_name[name]
        assert dict(visible.get("properties") or {}) == expected_properties
        assert list(visible.get("required") or []) == expected_required

    assert schemas_with_host_book_id > 0
    assert skill_items == source_snapshot

    # ``chapterId`` is not wholly host-owned: single-chapter tools default to
    # the current chapter but still accept a real non-current catalog id.
    assert "chapterId" in core_by_name["getChapterContent"]["properties"]
    assert "chapterId" in core_by_name["editChapterContent"]["properties"]
    assert "chapterIds" in core_by_name["batchGetChapterContents"]["properties"]


def test_only_single_chapter_default_tools_bind_omitted_chapter_id():
    context = {"bookId": "book-a", "chapterId": "chapter-current"}

    for name in CURRENT_CHAPTER_DEFAULT_TOOLS:
        omitted = bind_host_writing_arguments(context, name, {})
        explicit_other = bind_host_writing_arguments(
            context,
            name,
            {"chapterId": "chapter-other"},
        )
        current_title = bind_host_writing_arguments(
            context,
            name,
            {"chapterId": "第一章"},
        )

        assert omitted["chapterId"] == "chapter-current"
        assert explicit_other["chapterId"] == "chapter-other"
        assert current_title["chapterId"] == "第一章"

    # These chapter fields select/associate arbitrary chapters and must never
    # be silently rewritten to the current UI chapter.
    explicit_tools = {
        "addForeshadowing": {"chapterId": "chapter-other"},
        "addSparkIdea": {"chapterId": "chapter-other"},
        "batchGetChapterContents": {
            "chapterIds": ["chapter-other", "chapter-third"],
        },
        "searchSparkIdeas": {"chapterId": "chapter-other"},
        "updateSparkIdea": {"chapterId": "chapter-other"},
    }
    for name, arguments in explicit_tools.items():
        assert bind_host_writing_arguments(context, name, arguments) == {
            **arguments,
            "bookId": "book-a",
        }


def test_explicit_chapter_ids_remain_subject_to_catalog_resolution():
    context = {"bookId": "book-a", "chapterId": "chapter-current"}
    catalog = [
        {"id": "chapter-current", "title": "第一章"},
        {"id": "chapter-other", "title": "第二章"},
    ]

    explicit_other = bind_host_writing_arguments(
        context,
        "getChapterContent",
        {"chapterId": "chapter-other"},
    )
    assert resolve_chapter_id_strict(
        explicit_other,
        catalog,
        context["chapterId"],
    ) == {"ok": True, "chapterId": "chapter-other"}

    for invalid in ("chapter-unknown", "第一章"):
        bound = bind_host_writing_arguments(
            context,
            "getChapterContent",
            {"chapterId": invalid},
        )
        assert bound["chapterId"] == invalid
        assert resolve_chapter_id_strict(
            bound,
            catalog,
            context["chapterId"],
        ) == {"ok": False, "reason": "chapter_not_in_catalog"}


@pytest.mark.asyncio
async def test_core_scope_rejects_conflict_before_approval_then_binds_host_for_handler():
    calls: list[dict] = []

    async def _handler(ctx, args, send_chunk):
        calls.append(args)
        return ToolResult('{"success":true}')

    approval = _ImmediateApproval()
    executor = CoreToolExecutor(
        _core_catalog(handler_overrides={"deleteCharacter": _handler}),
        approval_gateway=approval,
    )
    rejected = await executor.execute_batch(
        _core_request({"bookId": "book-b", "characterId": 7}),
        _RecordingSink(),
    )

    assert rejected.outcome is ToolBatchOutcome.REJECTED
    assert rejected.error == "tool_scope_violation"
    assert approval.requests == []
    assert calls == []

    completed = await executor.execute_batch(
        _core_request({"characterId": 7}),
        _RecordingSink(),
    )

    assert completed.outcome is ToolBatchOutcome.COMPLETED
    assert calls == [{
        "characterId": 7,
        "__toolCallId": "call-1",
        "bookId": "book-a",
    }]
    assert len(approval.requests) == 1
    assert "bookId" not in approval.requests[0].summary
    assert "book-a" not in approval.requests[0].summary


@pytest.mark.asyncio
async def test_core_omitted_chapter_binds_but_explicit_chapter_survives():
    calls: list[dict] = []

    async def _handler(ctx, args, send_chunk):
        calls.append(args)
        return ToolResult('{"success":true}')

    executor = CoreToolExecutor(_core_catalog(
        handler_overrides={"getChapterContent": _handler},
    ))
    domain = {
        "bookId": "book-a",
        "chapterId": "chapter-current",
    }
    for arguments in (
        {},
        {"chapterId": "chapter-other"},
    ):
        completed = await executor.execute_batch(
            _core_request(
                arguments,
                name="getChapterContent",
                domain=domain,
            ),
            _RecordingSink(),
        )
        assert completed.outcome is ToolBatchOutcome.COMPLETED

    assert [call["chapterId"] for call in calls] == [
        "chapter-current",
        "chapter-other",
    ]

    explicit_label = await executor.execute_batch(
        _core_request(
            {"chapterId": "display-label"},
            name="getChapterContent",
            domain={"bookId": "book-a"},
        ),
        _RecordingSink(),
    )
    assert explicit_label.outcome is ToolBatchOutcome.COMPLETED
    assert len(calls) == 3
    assert calls[-1]["chapterId"] == "display-label"


@pytest.mark.asyncio
async def test_core_binding_thaws_nested_model_arguments_for_writing_handler():
    calls: list[dict] = []

    async def _handler(ctx, args, send_chunk):
        calls.append(args)
        return ToolResult('{"success":true}')

    executor = CoreToolExecutor(_core_catalog(
        handler_overrides={"getBookCharacters": _handler},
    ))
    result = await executor.execute_batch(
        ToolBatchRequest(
            run_id="run-1",
            calls=(ToolCall(
                id="call-nested",
                name="getBookCharacters",
                arguments_json=json.dumps({
                    "names": ["测试人物"],
                    "filters": {"tags": ["主角"]},
                }, ensure_ascii=False),
            ),),
            allowed_tool_names=frozenset({"getBookCharacters"}),
            state=ExecutionState(domain={"bookId": "book-a"}),
        ),
        _RecordingSink(),
    )

    assert result.outcome is ToolBatchOutcome.COMPLETED
    assert calls == [{
        "names": ["测试人物"],
        "filters": {"tags": ["主角"]},
        "__toolCallId": "call-nested",
        "bookId": "book-a",
    }]
    assert isinstance(calls[0]["names"], list)
    assert isinstance(calls[0]["filters"], dict)
    assert isinstance(calls[0]["filters"]["tags"], list)
