from __future__ import annotations

from pathlib import Path

from domains.screenplay.tool_contracts import (
    SCREENPLAY_TOOL_NAMES,
    SCREENPLAY_TOOL_SCHEMAS,
)
from domains.screenplay.tool_display_names import (
    SCREENPLAY_TOOL_DISPLAY_NAMES,
)
from domains.writing.tools.display_names import WRITING_TOOL_DISPLAY_NAMES
from domains.writing.tools.catalog import _schema_from_skill


SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"


def test_every_production_domain_tool_has_zh_and_en_display_names():
    writing_names = {
        path.parent.name for path in SKILLS_DIR.glob("*/SKILL.md")
    }
    assert set(WRITING_TOOL_DISPLAY_NAMES) == writing_names
    assert set(SCREENPLAY_TOOL_DISPLAY_NAMES) == set(SCREENPLAY_TOOL_NAMES)
    for localized_names in (
        *WRITING_TOOL_DISPLAY_NAMES.values(),
        *SCREENPLAY_TOOL_DISPLAY_NAMES.values(),
    ):
        assert set(localized_names) >= {"zh-CN", "en-US"}
        assert all(str(value).strip() for value in localized_names.values())


def test_screenplay_schemas_embed_display_names_as_host_metadata():
    schemas = {schema.name: schema for schema in SCREENPLAY_TOOL_SCHEMAS}
    assert set(schemas) == set(SCREENPLAY_TOOL_NAMES)
    for name, schema in schemas.items():
        assert dict(schema.display_names) == dict(
            SCREENPLAY_TOOL_DISPLAY_NAMES[name]
        )


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
