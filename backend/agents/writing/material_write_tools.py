"""Approved material write tools (update / create / delete) for the Writing Agent."""

from __future__ import annotations

import json

from agents.writing.display_params import display_arguments
from agents.writing.material_write_model import (
    SqliteWritingMaterialRepository,
    WritingMaterialMutationError,
)
from agents.writing.read_model import WritingReadScope, WritingReadScopeError
from agents.writing.read_tools import WRITING_READ_SCOPE_STATE_KEY
from application.domain_effect_bridge import get_broadcaster
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


def build_writing_material_tool_registrations(db) -> tuple[ToolRegistration, ...]:
    repository = SqliteWritingMaterialRepository(db)

    async def validate_background(state, arguments, signal=None):
        del signal
        return await _validate(repository.validate_story_background, state, arguments,
                               content=arguments.get("content"),
                               base_revision=arguments.get("baseRevision"),
                               clear_content=arguments.get("clearContent", False))

    async def edit_background(state, arguments, signal=None):
        return await _commit(
            repository.commit_story_background, "writing.story_background_updated",
            state, arguments, signal=signal, content=arguments.get("content"),
            base_revision=arguments.get("baseRevision"),
            clear_content=arguments.get("clearContent", False),
        )

    async def validate_outline(state, arguments, signal=None):
        del signal
        return await _validate(repository.validate_global_outline, state, arguments,
                               markdown=arguments.get("markdownContent"),
                               base_revision=arguments.get("baseRevision"),
                               clear_content=arguments.get("clearContent", False))

    async def edit_outline(state, arguments, signal=None):
        return await _commit(
            repository.commit_global_outline, "writing.global_outline_updated",
            state, arguments, signal=signal, markdown=arguments.get("markdownContent"),
            base_revision=arguments.get("baseRevision"),
            clear_content=arguments.get("clearContent", False),
        )

    async def validate_character(state, arguments, signal=None):
        del signal
        return await _validate(
            repository.validate_character, state, arguments,
            character_id=arguments.get("characterId"),
            base_revision=arguments.get("baseRevision"), patch=_patch(arguments),
        )

    async def update_character(state, arguments, signal=None):
        return await _commit(
            repository.commit_character, "writing.character_updated", state, arguments,
            signal=signal, character_id=arguments.get("characterId"),
            base_revision=arguments.get("baseRevision"), patch=_patch(arguments),
        )

    async def validate_entity(state, arguments, signal=None):
        del signal
        return await _validate(
            repository.validate_setting_entity, state, arguments,
            entity_id=arguments.get("entityId"),
            base_revision=arguments.get("baseRevision"), patch=_patch(arguments),
        )

    async def update_entity(state, arguments, signal=None):
        return await _commit(
            repository.commit_setting_entity, "writing.setting_entity_updated",
            state, arguments, signal=signal,
            broadcast_action="updated",
            entity_id=arguments.get("entityId"),
            base_revision=arguments.get("baseRevision"), patch=_patch(arguments),
        )

    async def validate_create_entity(state, arguments, signal=None):
        del signal
        return await _validate(
            repository.validate_create_setting_entity, state, arguments,
            name=arguments.get("name"),
            entity_type=arguments.get("entityType"),
            tags=arguments.get("tags", ""),
            profile_md=arguments.get("profileMd", ""),
        )

    async def create_entity(state, arguments, signal=None):
        return await _commit(
            repository.commit_create_setting_entity, "writing.setting_entity_created",
            state, arguments, signal=signal,
            broadcast_action="created",
            name=arguments.get("name"),
            entity_type=arguments.get("entityType"),
            tags=arguments.get("tags", ""),
            profile_md=arguments.get("profileMd", ""),
        )

    async def validate_delete_entity(state, arguments, signal=None):
        del signal
        return await _validate(
            repository.validate_delete_setting_entity, state, arguments,
            entity_id=arguments.get("entityId"),
            base_revision=arguments.get("baseRevision"),
        )

    async def delete_entity(state, arguments, signal=None):
        return await _commit(
            repository.commit_delete_setting_entity, "writing.setting_entity_deleted",
            state, arguments, signal=signal,
            broadcast_action="deleted",
            entity_id=arguments.get("entityId"),
            base_revision=arguments.get("baseRevision"),
        )

    return (
        _text_registration(
            "editStoryBackground", "编辑小说背景", "content",
            "全文替换当前书故事背景；必须先读取并携带 baseRevision，清空需明确声明。",
            "getStoryBackground", "storyBackground.updated", validate_background,
            edit_background,
        ),
        _record_registration(
            "updateCharacter", "更新人物设定", "characterId",
            "部分更新当前书已有的人物；必须先读取详情并携带 baseRevision。",
            "getBookCharacters", "character.updated", validate_character,
            update_character,
        ),
        _record_registration(
            "updateSettingEntity", "更新世界设定", "entityId",
            "部分更新当前书已有的设定实体；必须先读取详情并携带 baseRevision。",
            "getSettingEntities", "settingEntity.updated", validate_entity,
            update_entity,
        ),
        _create_entity_registration(
            "createSettingEntity", "新增世界设定",
            "在当前书新增一条世界设定实体（地点/势力/物品/其他）；"
            "名称不得与已有设定重复，创建前应先查看设定目录确认。",
            "listSettingEntities", "settingEntity.created",
            validate_create_entity, create_entity,
        ),
        _delete_entity_registration(
            "deleteSettingEntity", "删除世界设定",
            "删除当前书已有的一条设定实体；必须先读取详情并携带 baseRevision，"
            "删除会连同其修订历史一起清除。",
            "getSettingEntities", "settingEntity.deleted",
            validate_delete_entity, delete_entity,
        ),
        _text_registration(
            "editGlobalOutline", "编辑总纲", "markdownContent",
            "全文替换当前书唯一总纲；必须先读取并携带 baseRevision，清空需明确声明。",
            "readWritingOutlines", "globalOutline.updated", validate_outline,
            edit_outline,
        ),
    )


async def _validate(operation, state, arguments, **kwargs):
    del arguments
    try:
        await operation(_scope(state), **kwargs)
    except (WritingMaterialMutationError, WritingReadScopeError) as error:
        return error.code
    return None


async def _commit(
    operation, effect_type, state, arguments, *, signal=None,
    broadcast_action: str | None = None, **kwargs,
):
    del arguments
    try:
        payload = await operation(_scope(state), signal=signal, **kwargs)
        if broadcast_action and not payload["noop"]:
            # 提交成功的瞬间通知已连接的 SSE 流：世界设定面板据此刷新列表。
            get_broadcaster().publish(
                getattr(state, "run_id", None),
                "writing.setting_entities_changed",
                {
                    "bookId": payload["bookId"],
                    "action": broadcast_action,
                    "id": payload["targetId"],
                    "name": payload.get("name"),
                },
            )
        return ToolHandlerResult(
            json.dumps(payload, ensure_ascii=False),
            effects=(DomainEffect(type=effect_type, payload={
                "bookId": payload["bookId"],
                "targetId": payload["targetId"],
                "committedRevision": payload["committedRevision"],
                "intentDigest": payload["intentDigest"],
                "noop": payload["noop"],
            }),),
            effect_state=ToolEffectState.COMMITTED,
        )
    except (WritingMaterialMutationError, WritingReadScopeError) as error:
        return ToolHandlerResult(
            json.dumps({"success": False, "code": error.code, "error": str(error)},
                       ensure_ascii=False),
            error_code=error.code,
            effect_state=ToolEffectState.NOT_STARTED,
        )


def _text_registration(
    name, display_name, content_key, description, prerequisite, produces,
    validator, handler,
) -> ToolRegistration:
    properties = {
        content_key: {"type": "string", "maxLength": 250_000},
        "baseRevision": {"type": "string", "minLength": 8, "maxLength": 80},
        "clearContent": {"type": "boolean"},
    }
    return _registration(
        name, display_name, description, properties,
        required=(content_key, "baseRevision"), prerequisite=prerequisite,
        produces=produces, validator=validator, handler=handler,
    )


def _record_registration(
    name, display_name, id_key, description, prerequisite, produces,
    validator, handler,
) -> ToolRegistration:
    properties = {
        id_key: {"type": "integer", "minimum": 1},
        "baseRevision": {"type": "string", "minLength": 8, "maxLength": 80},
        "name": {"type": "string", "minLength": 1, "maxLength": 200},
        "tags": {"type": "string", "maxLength": 10_000},
        "profileMd": {"type": "string", "maxLength": 250_000},
    }
    return _registration(
        name, display_name, description, properties,
        required=(id_key, "baseRevision"), prerequisite=prerequisite,
        produces=produces, validator=validator, handler=handler,
    )


def _create_entity_registration(
    name, display_name, description, prerequisite, produces,
    validator, handler,
) -> ToolRegistration:
    properties = {
        "name": {"type": "string", "minLength": 1, "maxLength": 200},
        "entityType": {
            "type": "string", "enum": ["location", "faction", "item", "other"],
        },
        "tags": {"type": "string", "maxLength": 10_000},
        "profileMd": {"type": "string", "maxLength": 250_000},
    }
    return _registration(
        name, display_name, description, properties,
        required=("name", "entityType"), prerequisite=prerequisite,
        produces=produces, validator=validator, handler=handler,
    )


def _delete_entity_registration(
    name, display_name, description, prerequisite, produces,
    validator, handler,
) -> ToolRegistration:
    properties = {
        "entityId": {"type": "integer", "minimum": 1},
        "baseRevision": {"type": "string", "minLength": 8, "maxLength": 80},
    }
    return _registration(
        name, display_name, description, properties,
        required=("entityId", "baseRevision"), prerequisite=prerequisite,
        produces=produces, validator=validator, handler=handler,
        risk_level=ToolRiskLevel.DESTRUCTIVE,
    )


def _registration(
    name, display_name, description, properties, *, required, prerequisite,
    produces, validator, handler, risk_level=ToolRiskLevel.WRITE,
) -> ToolRegistration:
    return ToolRegistration(
        schema=ToolSchema(
            name=name,
            description=description,
            parameters={
                "type": "object", "properties": properties,
                "required": list(required), "additionalProperties": False,
            },
            display_names={"zh-CN": display_name, "en": name},
        ),
        handler=handler,
        policy=ToolPolicy(ToolExecutionMode.CONFIRM, display_name, risk_level),
        scope_validator=validator,
        cancellation_linearizable=True,
        context_contract=ToolContextContract(
            prerequisite_tools=(prerequisite,),
            mandatory_context_keys=("binding.bookId",),
            produces=(produces,),
        ),
        data_contract=ToolDataContract(
            model_owned_paths=tuple(properties),
            host_bound_paths=("bookId", "sessionId", "chapterId"),
            host_derived_paths=("committedRevision", "intentDigest"),
        ),
        max_argument_chars=300_000,
        operation_display_params=lambda state, arguments, call: {
            "displayNames": {"zh-CN": display_name, "en": name},
            "toolArguments": display_arguments(arguments, tuple(properties)),
        },
    )


def _scope(state) -> WritingReadScope:
    return WritingReadScope.from_mapping(
        thaw_json_mapping(state.domain).get(WRITING_READ_SCOPE_STATE_KEY)
    )


def _patch(arguments) -> dict[str, str]:
    return {
        key: arguments[key]
        for key in ("name", "tags", "profileMd")
        if key in arguments
    }


__all__ = ["build_writing_material_tool_registrations"]
