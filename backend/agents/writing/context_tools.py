"""Explicit context reads for the replacement Writing Agent."""

from __future__ import annotations

import json
import hashlib

from agents.writing.context_contract import (
    WRITING_CONTEXT_SELECTION_STATE_KEY,
    WritingContextSelection,
    WritingContextSelectionError,
)
from agents.writing.read_model import WritingReadScope
from agents.writing.read_tools import WRITING_READ_SCOPE_STATE_KEY
from agents.writing.revisions import text_revision
from agents.writing.context_snapshot import (
    snapshot_from_state_or_run_attributes,
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
from utils.text import extract_text_from_lexical


async def _associated(db, state, arguments, signal=None) -> ToolHandlerResult:
    raise_if_stopped(signal)
    try:
        scope, selection = _state_contracts(state)
        maximum = _max_text_length(arguments)
        chapter_ids = _requested_subset(
            arguments.get("chapterIds"),
            selection.associated_chapter_ids,
            "chapterIds",
        )
        outline_ids = _requested_subset(
            arguments.get("outlineIds"),
            selection.associated_outline_ids,
            "outlineIds",
        )
        chapters = []
        if chapter_ids:
            placeholders = ",".join("?" for _ in chapter_ids)
            rows = await db.fetch_all(
                "SELECT c.id, c.title, COALESCE(a.content, '') AS content "
                "FROM outline_chapters AS c "
                "JOIN outlines AS o ON o.id = c.outline_id "
                "LEFT JOIN articles AS a ON a.chapter_id = c.id "
                f"WHERE o.book_id = ? AND o.type = 'writing' "
                f"AND c.id IN ({placeholders})",
                [scope.book_id, *chapter_ids],
            )
            by_id = {str(row["id"]): row for row in rows}
            for item_id in chapter_ids:
                row = by_id.get(item_id)
                if row is None:
                    continue
                raw = str(row.get("content") or "")
                try:
                    text = extract_text_from_lexical(raw) if raw else ""
                except Exception:
                    text = ""
                chapters.append(_text_item(row, text, maximum))

        outlines = []
        if outline_ids:
            placeholders = ",".join("?" for _ in outline_ids)
            rows = await db.fetch_all(
                "SELECT id, title, type, markdown_content FROM outlines "
                f"WHERE book_id = ? AND id IN ({placeholders}) "
                "AND type IN ('global', 'volume', 'chapter', 'writing')",
                [scope.book_id, *outline_ids],
            )
            by_id = {str(row["id"]): row for row in rows}
            for item_id in outline_ids:
                row = by_id.get(item_id)
                if row is not None:
                    outlines.append(
                        _text_item(
                            row,
                            str(row.get("markdown_content") or ""),
                            maximum,
                            extra={"outlineType": str(row.get("type") or "")},
                        )
                    )
        return _result(scope, {
            "chapters": chapters,
            "outlines": outlines,
            "missingChapterIds": [
                item for item in chapter_ids
                if item not in {entry["id"] for entry in chapters}
            ],
            "missingOutlineIds": [
                item for item in outline_ids
                if item not in {entry["id"] for entry in outlines}
            ],
        })
    except (ValueError, WritingContextSelectionError) as error:
        return _error(error)


async def _selected_local(
    db, memory_operations, state, arguments, signal=None
) -> ToolHandlerResult:
    raise_if_stopped(signal)
    try:
        scope, selection = _state_contracts(state)
        sparks = await _rows_by_ids(
            db,
            table="ai_memories",
            book_id=scope.book_id,
            ids=selection.selected_spark_ids,
            columns="id, layer, content, chapter_id, character_id",
        )
        foreshadowing = await _rows_by_ids(
            db,
            table="ai_foreshadowing",
            book_id=scope.book_id,
            ids=selection.selected_foreshadowing_ids,
            columns=(
                "id, content, type, status, chapter_id, "
                "expected_chapter_id, resolved_chapter_id"
            ),
        )
        long_term = []
        long_term_error = None
        if selection.selected_long_term_memory_ids:
            try:
                frozen = await snapshot_from_state_or_run_attributes(db, state)
                frozen_memory = frozen.get("longTermMemory", {})
                if frozen_memory.get("state") not in (None, "available"):
                    raise ValueError(str(frozen_memory["state"]))
                records = await memory_operations.get_many(
                    book_id=scope.book_id,
                    item_ids=selection.selected_long_term_memory_ids,
                    include_inactive=False,
                )
                expected_versions = {
                    str(item["id"]): int(item["version"])
                    for item in frozen_memory.get("refs", ())
                }
                if frozen_memory.get("state") == "available" and (
                    {str(record["id"]) for record in records}
                    != set(expected_versions)
                    or any(
                        expected_versions.get(str(record["id"]))
                        != int(record["version"])
                        for record in records
                    )
                ):
                    raise ValueError("long_term_memory_snapshot_changed")
                remaining = _max_text_length(arguments)
                for record in records:
                    text = str(record.get("text") or "")
                    returned = text[:remaining]
                    long_term.append({
                        **{key: record.get(key) for key in (
                            "id", "version", "state", "metadata", "source"
                        )},
                        "text": returned,
                        "truncated": len(returned) < len(text),
                        "totalCharacters": len(text),
                    })
                    remaining = max(0, remaining - len(returned))
            except Exception as error:
                raise_if_stopped(signal)
                long_term_error = str(
                    getattr(
                        error,
                        "code",
                        (
                            str(error)
                            if str(error) in {
                                "long_term_memory_snapshot_changed",
                            }
                            else "long_term_memory_unavailable"
                        ),
                    )
                )
        return _result(scope, {
            "sparks": sparks,
            "foreshadowing": foreshadowing,
            "missingSparkIds": _missing(selection.selected_spark_ids, sparks),
            "missingForeshadowingIds": _missing(
                selection.selected_foreshadowing_ids, foreshadowing
            ),
            "longTermMemory": {
                "requestedIds": list(selection.selected_long_term_memory_ids),
                "available": long_term_error is None,
                "items": long_term,
                "missingIds": _missing(
                    selection.selected_long_term_memory_ids, long_term
                ),
                "reason": long_term_error,
            },
        })
    except (ValueError, WritingContextSelectionError) as error:
        return _error(error)


async def _techniques(
    db, technique_access, state, arguments, signal=None
) -> ToolHandlerResult:
    raise_if_stopped(signal)
    try:
        scope, selection = _state_contracts(state)
        if not selection.writing_technique_input_id:
            return _result(scope, {"selected": False, "entries": []})
        snapshot = await _load_technique_snapshot(
            db, technique_access, state, scope, selection
        )
        automatic_refs = _technique_refs(arguments.get("automaticRefs"))
        prior = state.domain.get("writingTechniqueAutomaticRefs")
        if prior is not None and prior != list(automatic_refs):
            raise ValueError("writing_technique_automatic_selection_changed")
        state.domain["writingTechniqueAutomaticRefs"] = list(automatic_refs)
        resolution = technique_access.resolve(snapshot, list(automatic_refs))
        maximum = _max_text_length(arguments)
        remaining = maximum
        entries = []
        for member in resolution["members"]:
            content = await technique_access.read(
                snapshot,
                member["ref"],
                "SKILL.md",
                automatic_refs=list(automatic_refs),
                max_characters=maximum,
            )
            if len(content["content"]) > remaining:
                raise ValueError(
                    "selected writing technique entries exceed maxTextLength"
                )
            entries.append({
                "ref": member["ref"],
                "sources": member["sources"],
                "path": "SKILL.md",
                "content": content["content"],
                "sha256": content["sha256"],
            })
            remaining -= len(content["content"])
        return _result(scope, {
            "selected": True,
            "mode": snapshot["mode"],
            "entries": entries,
            "schemes": [
                {
                    "ref": item["ref"],
                    "composition": item["composition"],
                }
                for item in resolution["selected"]
                if item["ref"]["kind"] == "scheme"
            ],
        })
    except Exception as error:
        raise_if_stopped(signal)
        return _error(error)


async def _technique_candidates(
    db, technique_access, state, arguments, signal=None
) -> ToolHandlerResult:
    del arguments
    raise_if_stopped(signal)
    try:
        scope, selection = _state_contracts(state)
        if not selection.writing_technique_input_id:
            return _result(scope, {"selected": False, "mode": None, "items": []})
        snapshot = await _load_technique_snapshot(
            db, technique_access, state, scope, selection
        )
        return _result(scope, {
            "selected": True,
            "mode": snapshot["mode"],
            "items": [
                {
                    "ref": item["ref"],
                    "metadata": item["metadata"],
                    "entryBytes": item["entryBytes"],
                }
                for item in snapshot.get("candidates", ())
            ],
        })
    except Exception as error:
        raise_if_stopped(signal)
        return _error(error)


async def _continuation_sections(
    db, state, arguments, signal=None
) -> ToolHandlerResult:
    from application.continuation_context import ContinuationContextService

    raise_if_stopped(signal)
    try:
        scope, _selection = _state_contracts(state)
        payload = await ContinuationContextService(db).list_source_sections(
            book_id=scope.book_id,
            offset=arguments.get("offset", 0),
            limit=arguments.get("limit", 50),
        )
        await _validate_continuation_snapshot(db, state, scope.book_id)
        return _result(scope, payload)
    except Exception as error:
        raise_if_stopped(signal)
        return _error(error, fallback_code="continuation_context_unavailable")


async def _continuation_section(
    db, state, arguments, signal=None
) -> ToolHandlerResult:
    from application.continuation_context import ContinuationContextService

    raise_if_stopped(signal)
    try:
        scope, _selection = _state_contracts(state)
        payload = await ContinuationContextService(db).read_source_section(
            book_id=scope.book_id,
            section_id=str(arguments.get("sectionId") or ""),
        )
        await _validate_continuation_snapshot(db, state, scope.book_id)
        return _result(scope, {"section": payload})
    except Exception as error:
        raise_if_stopped(signal)
        return _error(error, fallback_code="continuation_context_unavailable")


def build_writing_context_tool_registrations(
    db, *, memory_operations, technique_access
) -> tuple[ToolRegistration, ...]:
    async def associated(state, arguments, signal=None):
        return await _associated(db, state, arguments, signal)

    async def selected_local(state, arguments, signal=None):
        return await _selected_local(
            db, memory_operations, state, arguments, signal
        )

    async def techniques(state, arguments, signal=None):
        return await _techniques(
            db, technique_access, state, arguments, signal
        )

    async def continuation_sections(state, arguments, signal=None):
        return await _continuation_sections(db, state, arguments, signal)

    async def continuation_section(state, arguments, signal=None):
        return await _continuation_section(db, state, arguments, signal)

    async def technique_candidates(state, arguments, signal=None):
        return await _technique_candidates(
            db, technique_access, state, arguments, signal
        )

    return (
        _registration(
            "readAssociatedWritingContext",
            "读取本轮由用户明确选择的关联章节或大纲。只能读取宿主冻结清单内的 ID。",
            "读取关联写作资料",
            {
                "chapterIds": _id_array(),
                "outlineIds": _id_array(),
                "maxTextLength": {
                    "type": "integer", "minimum": 1, "maximum": 64_000
                },
            },
            associated,
        ),
        _registration(
            "readSelectedWritingContext",
            "读取本轮明确选择的灵感和伏笔；语义长期记忆未接入时返回稳定诊断，不伪装为空结果。",
            "读取已选写作记忆",
            {
                "maxTextLength": {
                    "type": "integer", "minimum": 1, "maximum": 64_000
                },
            },
            selected_local,
        ),
        _registration(
            "listWritingTechniqueCandidates",
            "列出本轮自动模式已授权的写作技法候选元数据；不返回技法正文。",
            "查看可用写作技法",
            {},
            technique_candidates,
        ),
        _registration(
            "readWritingTechniqueContext",
            "读取本轮冻结的写作技法入口文件；技法正文只通过本工具进入当前模型回合。",
            "读取本轮写作技法",
            {
                "maxTextLength": {
                    "type": "integer", "minimum": 1, "maximum": 64_000
                },
                "automaticRefs": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "kind": {
                                "type": "string",
                                "enum": ["technique", "scheme"],
                            },
                            "id": {"type": "string", "minLength": 1},
                            "versionId": {"type": "string", "minLength": 1},
                        },
                        "required": ["kind", "id", "versionId"],
                        "additionalProperties": False,
                    },
                    "maxItems": 16,
                    "uniqueItems": True,
                },
            },
            techniques,
        ),
        _registration(
            "listContinuationSourceSections",
            "列出当前续写作品在分叉点之前的冻结来源目录；原创作品返回空目录。",
            "查看续写来源目录",
            {
                "offset": {"type": "integer", "minimum": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            },
            continuation_sections,
        ),
        _registration(
            "readContinuationSourceSection",
            "按目录返回的 sectionId 读取分叉点之前的一节冻结来源正文。",
            "读取续写来源章节",
            {
                "sectionId": {
                    "type": "string", "minLength": 1, "maxLength": 200
                },
            },
            continuation_section,
            required=("sectionId",),
        ),
    )


def _state_contracts(state) -> tuple[WritingReadScope, WritingContextSelection]:
    return (
        WritingReadScope.from_mapping(state.domain.get(WRITING_READ_SCOPE_STATE_KEY)),
        WritingContextSelection.from_mapping(
            state.domain.get(WRITING_CONTEXT_SELECTION_STATE_KEY)
        ),
    )


def _requested_subset(value, allowed: tuple[str, ...], field: str) -> tuple[str, ...]:
    if value is None:
        return allowed
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field} must be an array")
    requested = tuple(dict.fromkeys(str(item).strip() for item in value))
    if any(not item for item in requested) or len(requested) > 32:
        raise ValueError(f"{field} contains invalid identifiers")
    if not set(requested).issubset(allowed):
        raise ValueError(f"{field} contains identifiers outside the frozen selection")
    return requested


def _max_text_length(arguments) -> int:
    value = arguments.get("maxTextLength", 32_000)
    if type(value) is not int or not 1 <= value <= 64_000:
        raise ValueError("maxTextLength must be between 1 and 64000")
    return value


async def _rows_by_ids(db, *, table, book_id, ids, columns):
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    rows = await db.fetch_all(
        f"SELECT {columns} FROM {table} WHERE book_id = ? "
        f"AND id IN ({placeholders})",
        [book_id, *ids],
    )
    by_id = {str(row["id"]): dict(row) for row in rows}
    return [by_id[item_id] for item_id in ids if item_id in by_id]


def _text_item(row, text: str, maximum: int, *, extra=None) -> dict[str, object]:
    returned = text[:maximum]
    return {
        "id": str(row["id"]),
        "title": str(row.get("title") or ""),
        "text": returned,
        "truncated": len(returned) < len(text),
        "returnedCharacters": len(returned),
        "totalCharacters": len(text),
        "baseRevision": text_revision(text),
        **(extra or {}),
    }


def _missing(requested, rows) -> list[str]:
    present = {str(row["id"]) for row in rows}
    return [item for item in requested if item not in present]


async def _load_technique_snapshot(
    db, technique_access, state, scope, selection
):
    if scope.session_id is None:
        raise ValueError("writing technique input requires a bound session")
    frozen = await snapshot_from_state_or_run_attributes(db, state)
    frozen_input = frozen.get("writingTechniqueInput")
    if isinstance(frozen_input, dict):
        row = await db.fetch_one(
            "SELECT request_digest, snapshot_json FROM "
            "writing_technique_request_inputs WHERE id = ? "
            "AND book_id = ? AND session_id = ?",
            [
                selection.writing_technique_input_id,
                scope.book_id,
                str(scope.session_id),
            ],
        )
        if row is None or (
            str(row["request_digest"]) != frozen_input["requestDigest"]
            or _text_digest(str(row["snapshot_json"]))
            != frozen_input["snapshotDigest"]
        ):
            raise ValueError("writing_technique_snapshot_changed")
    return await technique_access.load_input(
        selection.writing_technique_input_id,
        scope.book_id,
        str(scope.session_id),
    )


def _technique_refs(value) -> tuple[dict[str, str], ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)) or len(value) > 16:
        raise ValueError("automaticRefs must contain at most 16 references")
    result = []
    for item in value:
        if not isinstance(item, dict) or set(item) != {"kind", "id", "versionId"}:
            raise ValueError("automaticRefs contains an invalid reference")
        normalized = {key: str(item[key]).strip() for key in item}
        if normalized["kind"] not in {"technique", "scheme"} or not all(
            normalized.values()
        ):
            raise ValueError("automaticRefs contains an invalid reference")
        if normalized not in result:
            result.append(normalized)
    return tuple(result)


async def _validate_continuation_snapshot(db, state, book_id: str) -> None:
    from application.continuation_context import ContinuationContextService

    frozen = await snapshot_from_state_or_run_attributes(db, state)
    expected = frozen.get("continuation")
    if not isinstance(expected, dict):
        return
    current = await ContinuationContextService(db).load_for_writing(book_id)
    current_binding = current.get("binding")
    expected_binding = expected.get("binding")
    if expected.get("creationMode") != current.get("creationMode"):
        raise ValueError("continuation_context_snapshot_changed")
    if isinstance(expected_binding, dict) and (
        not isinstance(current_binding, dict)
        or expected_binding.get("bindingDigest")
        != current_binding.get("bindingDigest")
        or expected_binding.get("canonSnapshotDigest")
        != current_binding.get("canonSnapshotDigest")
    ):
        raise ValueError("continuation_context_snapshot_changed")


def _text_digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _result(scope, payload) -> ToolHandlerResult:
    return ToolHandlerResult(content=json.dumps({
        "schemaVersion": 1,
        "scope": scope.to_mapping(),
        **payload,
    }, ensure_ascii=False, allow_nan=False))


def _error(
    error: Exception, *, fallback_code: str = "tool_input_invalid"
) -> ToolHandlerResult:
    code = getattr(error, "code", fallback_code)
    return ToolHandlerResult(
        content=json.dumps(
            {"success": False, "code": code, "error": str(error)},
            ensure_ascii=False,
        ),
        error_code=code,
    )


def _registration(
    name, description, display_name, properties, handler, *, required=()
):
    return ToolRegistration(
        schema=ToolSchema(
            name=name,
            description=description,
            parameters={
                "type": "object",
                "properties": properties,
                **({"required": list(required)} if required else {}),
                "additionalProperties": False,
            },
            display_names={"zh-CN": display_name, "en": name},
        ),
        handler=handler,
        policy=ToolPolicy(
            ToolExecutionMode.READ, display_name, ToolRiskLevel.READ
        ),
        concurrency_safe=True,
        data_contract=ToolDataContract(
            model_owned_paths=tuple(properties),
            host_bound_paths=("bookId", "contextSelection"),
        ),
        operation_display_params=lambda state, arguments, call: {
            "displayNames": {"zh-CN": display_name, "en": name},
        },
    )


def _id_array():
    return {
        "type": "array",
        "items": {"type": "string", "minLength": 1, "maxLength": 512},
        "maxItems": 32,
        "uniqueItems": True,
    }


__all__ = ["build_writing_context_tool_registrations"]
