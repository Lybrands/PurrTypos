from __future__ import annotations

from pathlib import Path

import pytest

from domains.writing.tools.display_names import (
    WRITING_TOOL_DISPLAY_NAMES,
    writing_tool_display_names,
)
from domains.writing.tools.catalog import _schema_from_skill


SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"


def test_every_production_domain_tool_has_zh_and_en_display_names():
    writing_names = {
        path.parent.name for path in SKILLS_DIR.glob("*/SKILL.md")
    }
    assert set(WRITING_TOOL_DISPLAY_NAMES) == writing_names
    for localized_names in WRITING_TOOL_DISPLAY_NAMES.values():
        assert set(localized_names) >= {"zh-CN", "en-US"}
        assert all(str(value).strip() for value in localized_names.values())


def test_writing_skill_schema_embeds_display_names_as_host_metadata():
    schema = _schema_from_skill({
        "name": "getChapterContent",
        "description": "读取章节。",
        "parameters": {"type": "object", "properties": {}},
    })

    assert schema.name == "getChapterContent"
    assert dict(schema.display_names) == dict(
        WRITING_TOOL_DISPLAY_NAMES["getChapterContent"]
    )


@pytest.mark.parametrize(("tool_name", "arguments", "expected"), (
    ("getChapterContent", {"chapterId": "chapter-1"}, "查看《第一章 雨夜》正文"),
    (
        "batchGetChapterContents",
        {"chapterIds": ["chapter-1", "chapter-2"]},
        "查看《第一章 雨夜》、《第二章 来客》正文",
    ),
    ("getBookCharacters", {"names": ["林默", "周遥"]}, "查看人物「林默」、「周遥」的资料"),
    ("getSettingEntities", {"entityType": "location"}, "查看地点详情"),
    ("queryOutline", {"outlineId": "outline-1"}, "查看《第一幕》大纲"),
    ("searchMemories", {"query": "雨夜 红门"}, "检索与“雨夜 红门”相关的长期记忆"),
    (
        "searchSparkIdeas",
        {"layer": "章节", "chapterId": "chapter-2", "query": "钥匙"},
        "在《第二章 来客》的章节设定中检索与“钥匙”相关的设定",
    ),
    ("deleteCharacter", {"characterId": 991}, "删除指定人物"),
))
def test_writing_operation_labels_distinguish_safe_business_targets(
    tool_name,
    arguments,
    expected,
):
    state = {
        "currentChapterTitle": "第二章 来客",
        "writingChapters": [
            {"id": "chapter-1", "title": "第一章 雨夜"},
            {"id": "chapter-2", "title": "第二章 来客"},
        ],
        "availableOutlines": [
            {"id": "outline-1", "title": "第一幕"},
        ],
    }

    names = writing_tool_display_names(tool_name, state, arguments)

    assert names["zh-CN"] == expected
    assert "991" not in names["zh-CN"]
    assert names["en-US"] == WRITING_TOOL_DISPLAY_NAMES[tool_name]["en-US"]
