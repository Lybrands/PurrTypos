"""Chapter read/edit tools for the replacement Writing Agent."""

from __future__ import annotations

import json

from agents.writing.chapter_create_tools import created_chapter_ids
from application.domain_effect_bridge import get_broadcaster
from agents.writing.chapter_write_model import (
    SqliteWritingChapterRepository,
    WritingChapterMutationError,
)
from agents.writing.read_model import WritingReadScope
from agents.writing.read_tools import WRITING_READ_SCOPE_STATE_KEY
from purra.contracts import (
    DomainEffect,
    ToolContextContract,
    ToolDataContract,
    ToolEffectState,
    ToolExecutionMode,
    ToolHandlerResult,
    ToolPolicy,
    ToolRiskLevel,
    ToolSchema,
)
from purra.json_values import thaw_json_mapping
from purra.ports import ToolRegistration
from utils.text import fold_blank_lines


def build_writing_chapter_tool_registrations(db) -> tuple[ToolRegistration, ...]:
    repository = SqliteWritingChapterRepository(db)

    async def validate_edit(state, arguments, signal=None):
        del signal
        try:
            await repository.validate_edit(
                _scope(state),
                content=fold_blank_lines(arguments["content"]),
                base_revision=arguments["baseRevision"],
                clear_content=arguments.get("clearContent", False),
                chapter_id=arguments.get("chapterId"),
                created_ids=tuple(created_chapter_ids(state)),
            )
        except WritingChapterMutationError as error:
            return error.code
        return None

    async def edit_content(state, arguments, signal=None):
        try:
            payload = await repository.commit_edit(
                _scope(state),
                content=fold_blank_lines(arguments["content"]),
                base_revision=arguments["baseRevision"],
                clear_content=arguments.get("clearContent", False),
                chapter_id=arguments.get("chapterId"),
                created_ids=tuple(created_chapter_ids(state)),
                signal=signal,
            )
            # 提交落库的瞬间就在自己的代码里发布通知（不经过框架管道）。
            if not payload.get("noop"):
                get_broadcaster().publish(
                    getattr(state, "run_id", None),
                    "writing.chapter_content_updated",
                    {
                        "bookId": payload["bookId"],
                        "chapterId": payload["chapterId"],
                        "committedRevision": payload["committedRevision"],
                        "firstContent": payload.get("firstContent", False),
                    },
                )
            return ToolHandlerResult(
                json.dumps(payload, ensure_ascii=False),
                effects=(DomainEffect(
                    type="writing.chapter_content_updated",
                    payload={
                        "bookId": payload["bookId"],
                        "chapterId": payload["chapterId"],
                        "committedRevision": payload["committedRevision"],
                        "intentDigest": payload["intentDigest"],
                        "noop": payload["noop"],
                        "firstContent": payload.get("firstContent", False),
                    },
                ),),
                effect_state=ToolEffectState.COMMITTED,
            )
        except WritingChapterMutationError as error:
            return _error(error)

    return (
        ToolRegistration(
            schema=ToolSchema(
                name="editChapterContent",
                description=(
                    "提议并在用户明确批准后替换章节正文：默认写当前绑定章节；"
                    "传 chapterId 可写本 Run 由 createWritingChapters 新建的章节，"
                    "无章节绑定的全局对话可写本书任意章节。必须使用"
                    " readWritingChapters 返回的该章节 baseRevision。空正文只有在"
                    " clearContent=true 时才表示清空。正文为纯文本，段落之间"
                    "用单个换行分隔，不要插入空行。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "content": {
                            "type": "string",
                            "maxLength": 250_000,
                            "description": (
                                "章节正文纯文本；段落之间用单个换行分隔，"
                                "不要输出空行（连续换行会折叠为单个换行）。"
                            ),
                        },
                        "baseRevision": {
                            "type": "string",
                            "minLength": 8,
                            "maxLength": 80,
                        },
                        "clearContent": {"type": "boolean"},
                        "chapterId": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 512,
                        },
                    },
                    "required": ["content", "baseRevision"],
                    "additionalProperties": False,
                },
                display_names={"zh-CN": "编辑当前章节正文", "en": "Edit Chapter"},
            ),
            handler=edit_content,
            policy=ToolPolicy(
                ToolExecutionMode.CONFIRM,
                "编辑当前章节正文",
                ToolRiskLevel.WRITE,
            ),
            scope_validator=validate_edit,
            cancellation_linearizable=True,
            context_contract=ToolContextContract(
                prerequisite_tools=("readWritingChapters",),
                mandatory_context_keys=("binding.bookId", "binding.chapterId"),
                produces=("chapter.contentUpdated",),
            ),
            data_contract=ToolDataContract(
                model_owned_paths=(
                    "content", "baseRevision", "clearContent", "chapterId",
                ),
                host_bound_paths=("bookId", "sessionId"),
                host_derived_paths=("committedRevision", "intentDigest"),
            ),
            max_argument_chars=300_000,
            operation_display_params=lambda state, arguments, call: {
                "displayNames": {
                    "zh-CN": "审批并保存章节正文",
                    "en": "Approve and Save Chapter",
                },
                **(
                    {"toolArguments": {"chapterId": str(arguments.get("chapterId"))}}
                    if arguments.get("chapterId")
                    else {}
                ),
            },
        ),
    )


def _scope(state) -> WritingReadScope:
    return WritingReadScope.from_mapping(
        thaw_json_mapping(state.domain).get(WRITING_READ_SCOPE_STATE_KEY)
    )


def _error(error: WritingChapterMutationError) -> ToolHandlerResult:
    return ToolHandlerResult(
        json.dumps({
            "success": False,
            "code": error.code,
            "error": str(error),
        }, ensure_ascii=False),
        error_code=error.code,
        effect_state=ToolEffectState.NOT_STARTED,
    )


__all__ = ["build_writing_chapter_tool_registrations"]
