"""Bounded source-read tools for replacement Novel Analysis operations."""

from __future__ import annotations

import json

from agents.novel_analysis.domain import NovelAnalysisRequestScope
from agents.novel_analysis.source_model import (
    NovelAnalysisSourceScopeError,
    SqliteNovelAnalysisSourceRepository,
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


NOVEL_ANALYSIS_SOURCE_SCOPE_STATE_KEY = "novelAnalysisSourceScope"


def build_novel_analysis_source_tool_catalog(db) -> InMemoryToolCatalog:
    repository = SqliteNovelAnalysisSourceRepository(db)

    async def list_segments(state, arguments, signal=None):
        del arguments
        return await _read(repository.list_segments, state, signal=signal)

    async def read_segment(state, arguments, signal=None):
        return await _read(
            repository.read_segment,
            state,
            arguments.get("segmentId"),
            signal=signal,
        )

    registrations = (
        _registration(
            "listAnalysisSourceSegments",
            "列出本次分析冻结的来源片段；返回范围与摘要，不返回正文。",
            "查看分析来源目录",
            {},
            (),
            list_segments,
        ),
        _registration(
            "readAnalysisSourceSegment",
            "读取一个已冻结来源片段。正文按连续 sourceSpanId 返回，引用时保留 segmentId 与 sourceSpanId。",
            "读取分析来源片段",
            {
                "segmentId": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 500,
                },
            },
            ("segmentId",),
            read_segment,
        ),
    )
    return InMemoryToolCatalog(registrations)


async def _read(operation, state, *args, signal=None) -> ToolHandlerResult:
    raise_if_stopped(signal)
    try:
        scope = NovelAnalysisRequestScope.from_mapping(
            state.domain.get(NOVEL_ANALYSIS_SOURCE_SCOPE_STATE_KEY)
        )
        payload = await operation(scope, *args)
        raise_if_stopped(signal)
        return ToolHandlerResult(
            content=json.dumps(payload, ensure_ascii=False, allow_nan=False),
        )
    except NovelAnalysisSourceScopeError as error:
        return ToolHandlerResult(
            content=json.dumps({
                "success": False,
                "code": error.code,
                "error": str(error),
            }, ensure_ascii=False),
            error_code=error.code,
        )
    except ValueError as error:
        return ToolHandlerResult(
            content=json.dumps({
                "success": False,
                "code": "tool_input_invalid",
                "error": str(error),
            }, ensure_ascii=False),
            error_code="tool_input_invalid",
        )


def _registration(
    name,
    description,
    display_name,
    properties,
    required,
    handler,
) -> ToolRegistration:
    return ToolRegistration(
        schema=ToolSchema(
            name=name,
            description=description,
            parameters={
                "type": "object",
                "properties": properties,
                "required": list(required),
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
            model_owned_paths=tuple(properties),
            host_bound_paths=("sourceRevisionId", "segments"),
        ),
        operation_display_params=(
            lambda state, arguments, call, *, label=display_name, tool_name=name: {
                "displayNames": {"zh-CN": label, "en": tool_name},
            }
        ),
    )


__all__ = [
    "NOVEL_ANALYSIS_SOURCE_SCOPE_STATE_KEY",
    "build_novel_analysis_source_tool_catalog",
]
