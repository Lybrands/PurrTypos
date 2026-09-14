"""Read-only PurrA tool catalog for the replacement Writing Agent."""

from __future__ import annotations

import json
from collections.abc import Mapping

from agents.writing.read_model import (
    SqliteWritingReadRepository,
    WritingReadScope,
    WritingReadScopeError,
)
from purra.cancellation import raise_if_stopped
from purra.contracts import (
    ToolDataContract,
    ToolExecutionMode,
    ToolHandlerResult,
    ToolPolicy,
    ToolRiskLevel,
    ToolSchema,
)
from purra.ports import ToolRegistration
from purra.tools import InMemoryToolCatalog


WRITING_REPLACEMENT_DOMAIN_NAMESPACE = "purrtypos.writing"
WRITING_READ_SCOPE_STATE_KEY = "writingReadScope"

_PAGE_PROPERTIES = {
    "offset": {"type": "integer", "minimum": 0},
    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
}


def build_writing_read_tool_catalog(db) -> InMemoryToolCatalog:
    repository = SqliteWritingReadRepository(db)

    async def story_background(state, arguments, signal=None):
        del arguments
        return await _read(
            repository.story_background,
            state,
            signal=signal,
        )

    async def list_characters(state, arguments, signal=None):
        return await _read(
            repository.characters,
            state,
            offset=arguments.get("offset", 0),
            limit=arguments.get("limit", 50),
            signal=signal,
        )

    async def get_characters(state, arguments, signal=None):
        return await _read(
            repository.characters,
            state,
            offset=arguments.get("offset", 0),
            limit=arguments.get("limit", 50),
            character_ids=arguments.get("characterIds", ()),
            names=arguments.get("names", ()),
            include_profile=True,
            signal=signal,
        )

    async def list_chapters(state, arguments, signal=None):
        return await _read(
            repository.writing_chapters,
            state,
            offset=arguments.get("offset", 0),
            limit=arguments.get("limit", 50),
            signal=signal,
        )

    async def global_outline(state, arguments, signal=None):
        return await _read(
            repository.global_outline,
            state,
            max_text_length=arguments.get("maxTextLength", 32_000),
            signal=signal,
        )

    async def list_entities(state, arguments, signal=None):
        return await _read(
            repository.setting_entities,
            state,
            offset=arguments.get("offset", 0),
            limit=arguments.get("limit", 50),
            entity_type=arguments.get("entityType"),
            signal=signal,
        )

    async def get_entities(state, arguments, signal=None):
        return await _read(
            repository.setting_entities,
            state,
            offset=arguments.get("offset", 0),
            limit=arguments.get("limit", 50),
            entity_ids=arguments.get("entityIds", ()),
            names=arguments.get("names", ()),
            entity_type=arguments.get("entityType"),
            include_profile=True,
            signal=signal,
        )

    registrations = (
        _registration(
            "getStoryBackground",
            "读取当前绑定书籍的故事背景；没有内容时返回 hasContent=false。",
            "查看故事背景",
            {},
            story_background,
        ),
        _registration(
            "listBookCharacters",
            "分页列出当前书自有的人物，并由宿主返回权威 total。回答人物总数必须使用 total。",
            "查看人物目录",
            _PAGE_PROPERTIES,
            list_characters,
        ),
        _registration(
            "getBookCharacters",
            "分页读取当前书人物详情；可按人物 ID 或精确姓名筛选。",
            "读取人物详情",
            {
                **_PAGE_PROPERTIES,
                "characterIds": {
                    "type": "array",
                    "items": {"type": "integer", "minimum": 1},
                    "maxItems": 100,
                    "uniqueItems": True,
                },
                "names": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1, "maxLength": 200},
                    "maxItems": 100,
                    "uniqueItems": True,
                },
            },
            get_characters,
        ),
        _registration(
            "listWritingChapters",
            "分页列出当前书写作目录中的章节，不返回或截断正文 JSON。",
            "查看章节目录",
            _PAGE_PROPERTIES,
            list_chapters,
        ),
        _registration(
            "getGlobalOutline",
            "读取当前书自己的全局大纲；不回退读取其他书或无归属的大纲。",
            "查看全局大纲",
            {
                "maxTextLength": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 64_000,
                },
            },
            global_outline,
        ),
        _registration(
            "listSettingEntities",
            "分页列出当前书的世界设定实体，可按类型筛选。",
            "查看设定目录",
            {
                **_PAGE_PROPERTIES,
                "entityType": _entity_type_schema(),
            },
            list_entities,
        ),
        _registration(
            "getSettingEntities",
            "分页读取当前书的世界设定详情；可按 ID、精确名称或类型筛选。",
            "读取设定详情",
            {
                **_PAGE_PROPERTIES,
                "entityIds": {
                    "type": "array",
                    "items": {"type": "integer", "minimum": 1},
                    "maxItems": 100,
                    "uniqueItems": True,
                },
                "names": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1, "maxLength": 200},
                    "maxItems": 100,
                    "uniqueItems": True,
                },
                "entityType": _entity_type_schema(),
            },
            get_entities,
        ),
    )
    return InMemoryToolCatalog(
        registrations,
        enablement=lambda request: (
            frozenset(item.schema.name for item in registrations)
            if request.domain_context.namespace
            == WRITING_REPLACEMENT_DOMAIN_NAMESPACE
            else frozenset()
        ),
    )


async def _read(operation, state, *, signal=None, **kwargs) -> ToolHandlerResult:
    raise_if_stopped(signal)
    try:
        scope = WritingReadScope.from_mapping(
            state.domain.get(WRITING_READ_SCOPE_STATE_KEY)
        )
        payload = await operation(scope, **kwargs)
        raise_if_stopped(signal)
        return ToolHandlerResult(
            content=json.dumps(payload, ensure_ascii=False, allow_nan=False),
        )
    except WritingReadScopeError as error:
        return ToolHandlerResult(
            content=json.dumps(
                {"success": False, "error": str(error), "code": error.code},
                ensure_ascii=False,
            ),
            error_code=error.code,
        )
    except ValueError as error:
        return ToolHandlerResult(
            content=json.dumps(
                {"success": False, "error": str(error), "code": "tool_input_invalid"},
                ensure_ascii=False,
            ),
            error_code="tool_input_invalid",
        )


def _registration(
    name: str,
    description: str,
    display_name: str,
    properties: Mapping[str, object],
    handler,
) -> ToolRegistration:
    model_owned_paths = tuple(properties)
    return ToolRegistration(
        schema=ToolSchema(
            name=name,
            description=description,
            parameters={
                "type": "object",
                "properties": dict(properties),
                "additionalProperties": False,
            },
            display_names={"zh-CN": display_name, "en": name},
        ),
        handler=handler,
        policy=ToolPolicy(
            ToolExecutionMode.READ,
            display_name,
            ToolRiskLevel.READ,
        ),
        concurrency_safe=True,
        data_contract=ToolDataContract(
            model_owned_paths=model_owned_paths,
            host_bound_paths=("bookId", "sessionId", "chapterId"),
        ),
        operation_display_params=(
            lambda state, arguments, call, label=display_name: {
                "displayNames": {"zh-CN": label, "en": name},
            }
        ),
    )


def _entity_type_schema() -> dict[str, object]:
    return {
        "type": "string",
        "enum": ["location", "faction", "item", "other"],
    }


__all__ = [
    "WRITING_READ_SCOPE_STATE_KEY",
    "WRITING_REPLACEMENT_DOMAIN_NAMESPACE",
    "build_writing_read_tool_catalog",
]
