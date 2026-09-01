from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from domains.writing.policies import WRITING_TOOL_POLICIES
from domains.writing.tools.cache import (
    WRITING_CACHE_PROBES,
    WRITING_READ_CACHE_KEY_BUILDERS,
    build_read_cache_key,
)
from infrastructure.writing import (
    WritingSkillCatalog,
    WritingToolDependencies,
    build_writing_tool_catalog,
)
from infrastructure.writing.tools.handlers import WRITING_TOOL_OPERATIONS


BACKEND_DIR = Path(__file__).resolve().parent.parent
HANDLERS_DIR = BACKEND_DIR / "infrastructure" / "writing" / "tools" / "handlers"
SKILL_ITEMS = tuple(
    WritingSkillCatalog(BACKEND_DIR / "skills").skill_items()
)


def _catalog(*, db=None, **overrides):
    resolved_db = object() if db is None else db
    return build_writing_tool_catalog(
        dependencies=WritingToolDependencies(
            resolved_db,  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
        ),
        skill_items=SKILL_ITEMS,
        **overrides,
    )


def test_explicit_writing_capability_maps_are_closed_and_immutable():
    catalog = _catalog()
    registrations = tuple(catalog.registrations())

    assert set(WRITING_TOOL_OPERATIONS) == set(WRITING_TOOL_POLICIES) - {
        "searchMemories", "searchWritingMethods",
    }
    assert len(registrations) == 37
    assert len(WRITING_READ_CACHE_KEY_BUILDERS) == 11
    assert len(WRITING_CACHE_PROBES) == 13
    assert {item.schema.name for item in registrations} == set(WRITING_TOOL_POLICIES)
    assert {
        item.schema.name
        for item in registrations
        if item.cancellation_linearizable
    } == {
        name
        for name, policy in WRITING_TOOL_POLICIES.items()
        if policy.requires_user_approval
    }
    with pytest.raises(TypeError):
        WRITING_TOOL_OPERATIONS["newTool"] = object()  # type: ignore[index]
    with pytest.raises(TypeError):
        WRITING_CACHE_PROBES["newTool"] = object()  # type: ignore[index]


def test_handler_override_cannot_inherit_durable_receipt_capability():
    async def _override(_ctx, _args, _send_chunk):
        return object()

    registration = next(
        item
        for item in _catalog(
            handler_overrides={"createMemory": _override},
        ).registrations()
        if item.schema.name == "createMemory"
    )

    assert registration.policy.requires_user_approval is True
    assert registration.cancellation_linearizable is False


def test_canonical_handlers_have_no_registration_or_service_runtime_edge():
    forbidden = {
        "dependencies",
        "services",
    }
    violations: list[str] = []
    for path in sorted(HANDLERS_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                names = []
            for name in names:
                if name in forbidden or name.startswith("services."):
                    violations.append(f"{path.name}:{node.lineno} imports {name}")
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for decorator in node.decorator_list:
                    decorator_name = None
                    if isinstance(decorator, ast.Name):
                        decorator_name = decorator.id
                    elif isinstance(decorator, ast.Call) and isinstance(
                        decorator.func,
                        ast.Name,
                    ):
                        decorator_name = decorator.func.id
                    if decorator_name in {
                        "tool",
                        "cache_predictor",
                        "read_cache_key",
                    }:
                        violations.append(
                            f"{path.name}:{node.lineno} uses @{decorator_name}"
                        )
    assert not violations, "\n".join(violations)


def test_canonical_tool_loaders_have_no_ambient_database_edge():
    paths = [
        BACKEND_DIR / "infrastructure" / "writing" / "tools" / "data_loaders.py",
    ]
    violations: list[str] = []
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                names = []
            for name in names:
                if name == "dependencies" or name.startswith("services"):
                    violations.append(f"{path.name}:{node.lineno} imports {name}")
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "get_db"
            ):
                violations.append(f"{path.name}:{node.lineno} calls get_db")
    assert not violations, "\n".join(violations)


@pytest.mark.parametrize(
    "name,args,expected",
    [
        ("listWritingChapters", {}, "listWritingChapters:b1"),
        ("getBookCharacters", {}, "getBookCharacters:all:b1"),
        ("listBookCharacters", {}, "listBookCharacters:b1"),
        ("getStoryBackground", {}, "getStoryBackground:b1"),
        ("listSettingEntities", {}, "listSettingEntities:b1"),
        ("getSettingEntities", {}, "getSettingEntities:all:b1"),
        ("getStoryHealthDashboard", {}, "getStoryHealthDashboard:b1"),
        ("getWritingStatsDashboard", {}, "getWritingStatsDashboard:b1"),
        (
            "queryOutline",
            {"outlineIds": ["o2", "o1"]},
            "queryOutline:b1:o1,o2:32000",
        ),
        ("getGlobalOutline", {}, "getGlobalOutline:b1:32000"),
        ("listOutlines", {}, "listOutlines:b1"),
    ],
)
def test_all_read_cache_key_protocols_are_frozen(name, args, expected):
    assert build_read_cache_key(name, {"bookId": "b1"}, args) == expected


def test_query_outline_large_receipt_is_bounded_without_breaking_json():
    from purra.contracts import AgentMessage, MessageOrigin
    from domains.writing.continuity_validation import (
        AtomicContinuityGroundingValidator,
    )
    from infrastructure.writing.tools.handlers.outline_tools import (
        _bounded_outline_payload,
    )

    payload = _bounded_outline_payload({
        "success": True,
        "bookId": "book-1",
        "total": 1,
        "outlines": [{
            "id": "outline-1",
            "title": "长大纲",
            "type": "chapter",
            "xmindData": "",
            "markdown": "可核验开头一；可核验开头二；" + "长" * 40_000,
            "hasMarkdown": True,
        }],
    })

    decoded = json.loads(payload)
    assert len(payload) <= 24_000
    assert decoded["success"] is True
    assert decoded["responseTruncated"] is True
    assert decoded["outlines"][0]["id"] == "outline-1"
    assert decoded["outlines"][0]["markdown"].startswith(
        "可核验开头一；可核验开头二；"
    )
    assert "工具结果已截断" in decoded["outlines"][0]["markdown"]

    candidate = (
        "1. 【时间】\n"
        "- 证据：大纲“可核验开头一”；正文“正文值一”。\n"
        "- 若以大纲为准：仅把正文的“正文值一”替换为“可核验开头一”。\n"
        "- 若保留正文：仅把大纲的“可核验开头一”替换为“正文值一”。\n"
        "2. 【关系】\n"
        "- 证据：大纲“可核验开头二”；正文“正文值二”。\n"
        "- 若以大纲为准：仅把正文的“正文值二”替换为“可核验开头二”。\n"
        "- 若保留正文：仅把大纲的“可核验开头二”替换为“正文值二”。"
    )
    messages = (
        AgentMessage(
            role="tool",
            content=payload,
            tool_call_id="query-outline",
            origin=MessageOrigin.HOST_TOOL_RESULT,
            host_metadata={"purra_tool_name": "queryOutline"},
        ),
        AgentMessage(
            role="tool",
            content=json.dumps({
                "chapterId": "chapter-1",
                "plainText": "正文值一、正文值二",
            }, ensure_ascii=False),
            tool_call_id="read-chapter",
            origin=MessageOrigin.HOST_TOOL_RESULT,
            host_metadata={"purra_tool_name": "getChapterContent"},
        ),
    )
    grounding = AtomicContinuityGroundingValidator(
        expected_item_count=2,
        current_chapter_id="chapter-1",
        associated_outline_ids=frozenset({"outline-1"}),
    ).validate(content=candidate, messages=messages)

    assert grounding.accepted is True


@pytest.mark.parametrize("limit", [1, 2, 16, 64, 128, 255, 256, 300, 1_000])
def test_query_outline_payload_honors_every_positive_explicit_bound(limit):
    from infrastructure.writing.tools.handlers.outline_tools import (
        _bounded_outline_payload,
    )

    payload = _bounded_outline_payload(
        {
            "success": True,
            "bookId": "b" * 5_000,
            "outlines": [{
                "id": "outline-1",
                "markdown": "正文" * 20_000,
            }],
        },
        max_characters=limit,
    )

    assert len(payload) <= limit
    json.loads(payload)
    assert "b" * 5_000 not in payload


@pytest.mark.parametrize("limit", [0, -1])
def test_query_outline_payload_rejects_non_positive_bound(limit):
    from infrastructure.writing.tools.handlers.outline_tools import (
        _bounded_outline_payload,
    )

    with pytest.raises(ValueError, match="must be positive"):
        _bounded_outline_payload({}, max_characters=limit)


@pytest.mark.parametrize("limit", [True, 1.5, "24"])
def test_query_outline_payload_rejects_non_integer_bound(limit):
    from infrastructure.writing.tools.handlers.outline_tools import (
        _bounded_outline_payload,
    )

    with pytest.raises(TypeError, match="must be an integer"):
        _bounded_outline_payload({}, max_characters=limit)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_created_chapter_is_visible_to_later_tool_in_same_run(monkeypatch):
    from infrastructure.writing.tools.handlers import chapter_tools

    class _Database:
        async def fetch_one(self, _sql, params):
            assert params == ["book-1"]
            return {"id": "book-1"}

    database = _Database()

    async def _writing_outline(_db, _book_id):
        return {"id": "writing-1"}

    async def _add_chapter(_db, _outline_id, title, parent_id):
        assert title == "第2章"
        assert parent_id is None
        return {
            "id": "chapter-2",
            "title": title,
            "parent_id": None,
            "level": 1,
            "sort": 2,
        }

    async def _list_chapter_rows(_db, book_id, outline_id):
        assert book_id == "book-1"
        assert outline_id == "writing-1"
        return [{
            "id": "chapter-1",
            "title": "第1章",
            "parent_id": None,
            "level": 1,
            "sort": 1,
        }]

    async def _read_chapter(_db, book_id, chapter_id):
        assert book_id == "book-1"
        assert chapter_id == "chapter-2"
        return {
            "id": chapter_id,
            "outline_id": "writing-1",
            "title": "第2章",
            "articleExists": True,
            "plainTextFull": "新章正文",
        }

    monkeypatch.setattr(
        chapter_tools,
        "get_or_create_writing_outline",
        _writing_outline,
    )
    monkeypatch.setattr(chapter_tools, "add_chapter", _add_chapter)
    monkeypatch.setattr(
        chapter_tools,
        "_list_writing_chapter_rows_for_book",
        _list_chapter_rows,
    )
    monkeypatch.setattr(
        chapter_tools,
        "_read_writing_chapter_for_book",
        _read_chapter,
    )
    events: list[dict] = []
    context = {
        "bookId": "book-1",
        "chapterId": "chapter-1",
        "currentChapterTitle": "第1章",
        "writingChapters": [{
            "id": "chapter-1",
            "title": "第1章",
            "parent_id": None,
        }],
        "readToolCache": {"stale": "value"},
    }

    created = await chapter_tools._tool_create_writing_chapter(
        WritingToolDependencies(database, object(), object()),  # type: ignore[arg-type]
        context,
        {},
        events.append,
    )
    read_back = await chapter_tools._tool_get_chapter_content(
        WritingToolDependencies(database, object(), object()),  # type: ignore[arg-type]
        context,
        {},
        None,
    )

    assert json.loads(created.content)["chapter"]["id"] == "chapter-2"
    assert context["chapterId"] == "chapter-2"
    assert context["currentChapterTitle"] == "第2章"
    assert context["readToolCache"] == {}
    assert events == [{
        "chapterCreated": {
            "chapterId": "chapter-2",
            "title": "第2章",
            "parentId": None,
        },
    }]
    assert json.loads(read_back.content)["plainText"] == "新章正文"
