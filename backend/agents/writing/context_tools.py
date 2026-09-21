"""Explicit context reads for the replacement Writing Agent."""

from __future__ import annotations

import json
import hashlib

from agents.writing.display_params import display_arguments
from agents.writing.context_contract import (
    WRITING_CONTEXT_SELECTION_STATE_KEY,
    WritingContextSelection,
    WritingContextSelectionError,
)
from agents.writing.read_model import WritingReadScope
from agents.writing.read_tools import WRITING_READ_SCOPE_STATE_KEY
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


async def _search_memories(
    db, memory_operations, state, arguments, signal=None
) -> ToolHandlerResult:
    raise_if_stopped(signal)
    try:
        scope, selection = _state_contracts(state)
        query = str(arguments.get("query") or "").strip()
        limit = _limit(arguments.get("limit", 20))
        pattern = f"%{query}%"
        sparks = await db.fetch_all(
            "SELECT id, layer, content, chapter_id, character_id "
            "FROM ai_memories WHERE book_id = ? AND (? = '' OR content LIKE ?) "
            "ORDER BY id DESC LIMIT ?",
            [scope.book_id, query, pattern, limit],
        )
        foreshadowing = await db.fetch_all(
            "SELECT id, content, type, status, chapter_id, expected_chapter_id, "
            "resolved_chapter_id FROM ai_foreshadowing WHERE book_id = ? "
            "AND (? = '' OR content LIKE ?) ORDER BY id DESC LIMIT ?",
            [scope.book_id, query, pattern, limit],
        )
        long_term = ()
        long_term_error = None
        try:
            long_term = await memory_operations.list_records(
                book_id=scope.book_id,
                states=("active",),
                query=query,
                limit=limit,
            )
        except Exception as error:
            raise_if_stopped(signal)
            long_term_error = str(
                getattr(error, "code", "long_term_memory_unavailable")
            )
        return _result(scope, {
            "preferred": {
                "sparkIds": list(selection.selected_spark_ids),
                "longTermMemoryIds": list(
                    selection.selected_long_term_memory_ids
                ),
                "foreshadowingIds": list(
                    selection.selected_foreshadowing_ids
                ),
            },
            "sparks": [
                _memory_search_item(dict(row), kind="spark", field="content")
                for row in sparks
            ],
            "foreshadowing": [
                _memory_search_item(
                    dict(row), kind="foreshadowing", field="content"
                )
                for row in foreshadowing
            ],
            "longTermMemory": {
                "available": long_term_error is None,
                "items": [
                    _memory_search_item(
                        dict(item), kind="longTerm", field="text"
                    )
                    for item in long_term
                ],
                "reason": long_term_error,
            },
        })
    except (ValueError, WritingContextSelectionError) as error:
        return _error(error)


async def _read_memories(
    db, memory_operations, state, arguments, signal=None
) -> ToolHandlerResult:
    raise_if_stopped(signal)
    try:
        scope, _selection = _state_contracts(state)
        refs = _memory_refs(arguments.get("refs"))
        maximum = _max_text_length(arguments)
        spark_ids = tuple(ref["id"] for ref in refs if ref["kind"] == "spark")
        foreshadowing_ids = tuple(
            ref["id"] for ref in refs if ref["kind"] == "foreshadowing"
        )
        long_term_ids = tuple(
            ref["id"] for ref in refs if ref["kind"] == "longTerm"
        )
        sparks = await _rows_by_ids(
            db,
            table="ai_memories",
            book_id=scope.book_id,
            ids=spark_ids,
            columns="id, layer, content, chapter_id, character_id",
        )
        foreshadowing = await _rows_by_ids(
            db,
            table="ai_foreshadowing",
            book_id=scope.book_id,
            ids=foreshadowing_ids,
            columns=(
                "id, content, type, status, chapter_id, "
                "expected_chapter_id, resolved_chapter_id"
            ),
        )
        long_term = []
        long_term_error = None
        if long_term_ids:
            try:
                long_term = list(await memory_operations.get_many(
                    book_id=scope.book_id,
                    item_ids=long_term_ids,
                    include_inactive=False,
                ))
            except Exception as error:
                raise_if_stopped(signal)
                long_term_error = str(
                    getattr(error, "code", "long_term_memory_unavailable")
                )
        by_ref = {
            **{("spark", str(item["id"])): dict(item) for item in sparks},
            **{
                ("foreshadowing", str(item["id"])): dict(item)
                for item in foreshadowing
            },
            **{
                ("longTerm", str(item["id"])): dict(item)
                for item in long_term
            },
        }
        remaining = maximum
        items = []
        missing = []
        for ref in refs:
            item = by_ref.get((ref["kind"], ref["id"]))
            if item is None:
                missing.append(ref)
                continue
            field = "text" if ref["kind"] == "longTerm" else "content"
            text = str(item.get(field) or "")
            returned = text[:remaining]
            item[field] = returned
            item.update({
                "kind": ref["kind"],
                "truncated": len(returned) < len(text),
                "returnedCharacters": len(returned),
                "totalCharacters": len(text),
            })
            items.append(item)
            remaining -= len(returned)
        return _result(scope, {
            "items": items,
            "missing": missing,
            "longTermMemory": {
                "available": long_term_error is None,
                "reason": long_term_error,
            },
            "maxTextLength": maximum,
            "returnedCharacters": maximum - remaining,
        })
    except (ValueError, WritingContextSelectionError) as error:
        return _error(error)


async def _novel_knowledge(
    db, state, arguments, *, read: bool, signal=None
) -> ToolHandlerResult:
    from application.novel_knowledge_service import get_novel_knowledge_service

    raise_if_stopped(signal)
    try:
        scope, _selection = _state_contracts(state)
        frozen = await snapshot_from_state_or_run_attributes(db, state)
        knowledge_scope = frozen.get("novelKnowledgeScope")
        if not isinstance(knowledge_scope, dict):
            raise ValueError("novel_knowledge_not_configured")
        query = str(arguments.get("query") or "").strip()
        if not read and not query:
            raise ValueError("query is required")
        kwargs = {}
        context_block = "searchNovelKnowledge"
        if read:
            kwargs = {
                "document_id": str(arguments.get("documentId") or "").strip(),
                "revision": str(arguments.get("revision") or "").strip(),
                "chunk_id": (
                    str(arguments.get("chunkId") or "").strip() or None
                ),
            }
            if not kwargs["document_id"] or not kwargs["revision"]:
                raise ValueError("documentId and revision are required")
            context_block = "readNovelKnowledge"
        result = await get_novel_knowledge_service(db).search(
            scope.book_id,
            query,
            scope=knowledge_scope,
            limit=_knowledge_limit(arguments.get("limit", 12)),
            token_budget=_knowledge_budget(arguments.get("tokenBudget", 3_000)),
            signal=signal,
            context_block=context_block,
            **kwargs,
        )
        return ToolHandlerResult(
            content=json.dumps({
                "schemaVersion": 1,
                "scope": scope.to_mapping(),
                **{key: value for key, value in result.items() if key != "receipts"},
            }, ensure_ascii=False, allow_nan=False),
            context_evidence=tuple(result["receipts"]),
        )
    except Exception as error:
        raise_if_stopped(signal)
        return _error(error, fallback_code="novel_knowledge_unavailable")


async def _techniques(
    db, technique_access, state, arguments, signal=None
) -> ToolHandlerResult:
    raise_if_stopped(signal)
    try:
        scope, selection = _state_contracts(state)
        if not selection.writing_technique_input_id:
            return _result(scope, {"selected": False, "file": None})
        snapshot = await _load_technique_snapshot(
            db, technique_access, state, scope, selection
        )
        prior = state.domain.get("writingTechniqueAutomaticRefs")
        automatic_refs = (
            tuple(prior)
            if prior is not None and "automaticRefs" not in arguments
            else _technique_refs(arguments.get("automaticRefs"))
        )
        if prior is not None and prior != list(automatic_refs):
            raise ValueError("writing_technique_automatic_selection_changed")
        state.domain["writingTechniqueAutomaticRefs"] = list(automatic_refs)
        resolution = technique_access.resolve(snapshot, list(automatic_refs))
        maximum = _max_text_length(arguments)
        ref = _technique_ref(arguments.get("ref"), "ref")
        member = next(
            (item for item in resolution["members"] if item["ref"] == ref),
            None,
        )
        if member is None:
            raise ValueError("writing technique file is outside the selected set")
        path = str(arguments.get("path") or "").strip()
        if not path:
            raise ValueError("path is required")
        entry_refs = tuple(state.domain.get("writingTechniqueEntryRefs") or ())
        content = await technique_access.read(
            snapshot,
            ref,
            path,
            automatic_refs=list(automatic_refs),
            entry_refs=entry_refs,
            max_characters=maximum,
        )
        if path == "SKILL.md" and ref not in entry_refs:
            state.domain["writingTechniqueEntryRefs"] = [*entry_refs, ref]
        return _result(scope, {
            "selected": True,
            "mode": snapshot["mode"],
            "file": {
                "ref": ref,
                "sources": member["sources"],
                "path": path,
                "content": content["content"],
                "sha256": content["sha256"],
            },
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
                    "members": item["members"],
                    "selection": item.get("selection", "candidate"),
                }
                for item in (
                    *snapshot.get("manual", ()),
                    *snapshot.get("candidates", ()),
                )
            ],
        })
    except Exception as error:
        raise_if_stopped(signal)
        return _error(error)


async def _skills_list(db, state, arguments, signal=None) -> ToolHandlerResult:
    del arguments
    raise_if_stopped(signal)
    try:
        scope, _selection = _state_contracts(state)
        frozen = await snapshot_from_state_or_run_attributes(db, state)
        return _result(scope, {
            "items": [
                {
                    "ref": item["ref"],
                    "metadata": item["metadata"],
                    "entryBytes": item["entryBytes"],
                }
                for item in frozen.get("skills") or []
            ],
        })
    except Exception as error:
        raise_if_stopped(signal)
        return _error(error, fallback_code="writing_skills_unavailable")


async def _skill_file(db, state, arguments, signal=None) -> ToolHandlerResult:
    raise_if_stopped(signal)
    try:
        scope, _selection = _state_contracts(state)
        frozen = await snapshot_from_state_or_run_attributes(db, state)
        available = {
            canonical_ref(item["ref"]): item
            for item in frozen.get("skills") or []
        }
        ref = _skill_ref(arguments.get("ref"))
        entry = available.get(canonical_ref(ref))
        if entry is None:
            raise ValueError(
                "技能不在本轮可用范围内：未声明允许自动使用（metadata.autoUse）或版本不在冻结快照中"
            )
        path = str(arguments.get("path") or "").strip()
        if not path:
            raise ValueError("path is required")
        entry_refs = tuple(state.domain.get("writingSkillEntryRefs") or ())
        if path != "SKILL.md" and ref not in entry_refs:
            raise ValueError("必须先读取同一技能的 SKILL.md，再读取其中引用的辅助文件")
        from application.writing_technique_service import WritingTechniqueService

        content = await WritingTechniqueService(db).read_version_file(ref, path)
        maximum = _max_text_length(arguments)
        if len(content["content"]) > maximum:
            raise ValueError(
                f"技能文件共 {len(content['content'])} 字符，超过 maxTextLength，请提高上限后重试"
            )
        if path == "SKILL.md" and ref not in entry_refs:
            state.domain["writingSkillEntryRefs"] = [*entry_refs, ref]
        return _result(scope, {
            "file": {
                "ref": ref,
                "path": path,
                "content": content["content"],
                "sha256": content["sha256"],
            },
        })
    except Exception as error:
        raise_if_stopped(signal)
        return _error(error, fallback_code="writing_skills_unavailable")


def _skill_ref(value) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != {"kind", "id", "versionId"}:
        raise ValueError("ref contains an invalid skill reference")
    normalized = {
        key: str(value[key]).strip()
        for key in ("kind", "id", "versionId")
    }
    if normalized["kind"] != "skill" or not all(normalized.values()):
        raise ValueError("ref contains an invalid skill reference")
    return normalized


def canonical_ref(ref: dict) -> str:
    return json.dumps(ref, sort_keys=True, ensure_ascii=False)


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
    async def search_memories(state, arguments, signal=None):
        return await _search_memories(
            db, memory_operations, state, arguments, signal
        )

    async def read_memories(state, arguments, signal=None):
        return await _read_memories(
            db, memory_operations, state, arguments, signal
        )

    async def search_knowledge(state, arguments, signal=None):
        return await _novel_knowledge(
            db, state, arguments, read=False, signal=signal
        )

    async def read_knowledge(state, arguments, signal=None):
        return await _novel_knowledge(
            db, state, arguments, read=True, signal=signal
        )

    async def techniques(state, arguments, signal=None):
        return await _techniques(
            db, technique_access, state, arguments, signal
        )

    async def continuation_sections(state, arguments, signal=None):
        return await _continuation_sections(db, state, arguments, signal)

    async def continuation_section(state, arguments, signal=None):
        return await _continuation_section(db, state, arguments, signal)

    async def skills_list(state, arguments, signal=None):
        return await _skills_list(db, state, arguments, signal)

    async def skill_file(state, arguments, signal=None):
        return await _skill_file(db, state, arguments, signal)

    async def technique_candidates(state, arguments, signal=None):
        return await _technique_candidates(
            db, technique_access, state, arguments, signal
        )

    return (
        _registration(
            "searchWritingMemories",
            "检索当前书的灵感、伏笔和长期记忆。用户预选项仅作为优先提示，不限制检索范围。",
            "检索写作记忆",
            {
                "query": {"type": "string", "maxLength": 4_000},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            search_memories,
        ),
        _registration(
            "readWritingMemories",
            "读取当前书内指定的灵感、伏笔或长期记忆；检索结果中的任意引用均可读取。",
            "读取写作记忆",
            {
                "refs": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "kind": {
                                "type": "string",
                                "enum": ["spark", "foreshadowing", "longTerm"],
                            },
                            "id": {"type": "string", "minLength": 1, "maxLength": 512},
                        },
                        "required": ["kind", "id"],
                        "additionalProperties": False,
                    },
                    "minItems": 1,
                    "maxItems": 32,
                },
                "maxTextLength": {
                    "type": "integer", "minimum": 1, "maximum": 64_000
                },
            },
            read_memories,
            required=("refs",),
        ),
        _registration(
            "searchNovelKnowledge",
            "检索当前书绑定的 Novel Knowledge，返回带版本证据的匹配内容。",
            "检索创作资料",
            {
                "query": {"type": "string", "minLength": 1, "maxLength": 4_000},
                "limit": {"type": "integer", "minimum": 1, "maximum": 12},
                "tokenBudget": {"type": "integer", "minimum": 1, "maximum": 12_000},
            },
            search_knowledge,
            required=("query",),
        ),
        _registration(
            "readNovelKnowledge",
            "按检索结果中的文档、版本和可选分片标识读取当前书绑定的 Novel Knowledge。",
            "读取创作资料",
            {
                "documentId": {"type": "string", "minLength": 1, "maxLength": 512},
                "revision": {"type": "string", "minLength": 1, "maxLength": 512},
                "chunkId": {"type": "string", "minLength": 1, "maxLength": 512},
                "limit": {"type": "integer", "minimum": 1, "maximum": 12},
                "tokenBudget": {"type": "integer", "minimum": 1, "maximum": 12_000},
            },
            read_knowledge,
            required=("documentId", "revision"),
        ),
        _registration(
            "listWritingTechniqueCandidates",
            "列出本轮自动模式已授权的写作技法候选元数据；不返回技法正文。",
            "查看可用写作技法",
            {},
            technique_candidates,
        ),
        _registration(
            "readWritingTechniqueFile",
            "读取本轮已选写作技法的文件。必须先读取同一技法的 SKILL.md，再读取其中引用的辅助文件。",
            "读取写作技法文件",
            {
                "ref": {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string", "enum": ["technique"]},
                        "id": {"type": "string", "minLength": 1},
                        "versionId": {"type": "string", "minLength": 1},
                    },
                    "required": ["kind", "id", "versionId"],
                    "additionalProperties": False,
                },
                "path": {"type": "string", "minLength": 1, "maxLength": 512},
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
            required=("ref", "path"),
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
        _registration(
            "listWritingSkills",
            "列出本轮可自动使用的技能元数据（技能由包内 metadata.autoUse 声明允许自动使用），"
            "不返回技能正文。根据 name、description、tags 与 retrieval 信息判断与当前任务的相关性，"
            "需要时用 readWritingSkillFile 读取。",
            "查看可用技能",
            {},
            skills_list,
        ),
        _registration(
            "readWritingSkillFile",
            "读取本轮可用技能的文件。必须先读取同一技能的 SKILL.md，"
            "再读取其中引用的辅助文件。",
            "读取技能文件",
            {
                "ref": {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string", "enum": ["skill"]},
                        "id": {"type": "string", "minLength": 1},
                        "versionId": {"type": "string", "minLength": 1},
                    },
                    "required": ["kind", "id", "versionId"],
                    "additionalProperties": False,
                },
                "path": {"type": "string", "minLength": 1, "maxLength": 512},
                "maxTextLength": {
                    "type": "integer", "minimum": 1, "maximum": 64_000
                },
            },
            skill_file,
            required=("ref", "path"),
        ),
    )


def _state_contracts(state) -> tuple[WritingReadScope, WritingContextSelection]:
    return (
        WritingReadScope.from_mapping(state.domain.get(WRITING_READ_SCOPE_STATE_KEY)),
        WritingContextSelection.from_mapping(
            state.domain.get(WRITING_CONTEXT_SELECTION_STATE_KEY)
        ),
    )


def _max_text_length(arguments) -> int:
    value = arguments.get("maxTextLength", 32_000)
    if type(value) is not int or not 1 <= value <= 64_000:
        raise ValueError("maxTextLength must be between 1 and 64000")
    return value


def _limit(value) -> int:
    if type(value) is not int or not 1 <= value <= 100:
        raise ValueError("limit must be between 1 and 100")
    return value


def _knowledge_limit(value) -> int:
    if type(value) is not int or not 1 <= value <= 12:
        raise ValueError("limit must be between 1 and 12")
    return value


def _knowledge_budget(value) -> int:
    if type(value) is not int or not 1 <= value <= 12_000:
        raise ValueError("tokenBudget must be between 1 and 12000")
    return value


def _memory_refs(value) -> tuple[dict[str, str], ...]:
    if not isinstance(value, (list, tuple)) or not 1 <= len(value) <= 32:
        raise ValueError("refs must contain between 1 and 32 references")
    result = []
    for item in value:
        if not isinstance(item, dict) or set(item) != {"kind", "id"}:
            raise ValueError("refs contains an invalid reference")
        ref = {
            "kind": str(item["kind"]).strip(),
            "id": str(item["id"]).strip(),
        }
        if (
            ref["kind"] not in {"spark", "foreshadowing", "longTerm"}
            or not ref["id"]
            or len(ref["id"]) > 512
        ):
            raise ValueError("refs contains an invalid reference")
        if ref not in result:
            result.append(ref)
    return tuple(result)


def _memory_search_item(item, *, kind: str, field: str) -> dict[str, object]:
    text = str(item.pop(field, "") or "")
    return {
        **item,
        "kind": kind,
        "preview": text[:240],
        "totalCharacters": len(text),
    }


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
        normalized = _technique_ref(item, "automaticRefs")
        if normalized not in result:
            result.append(normalized)
    return tuple(result)


def _technique_ref(value, field: str) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != {"kind", "id", "versionId"}:
        raise ValueError(f"{field} contains an invalid reference")
    normalized = {
        key: str(value[key]).strip()
        for key in ("kind", "id", "versionId")
    }
    allowed = {"technique", "scheme"} if field == "automaticRefs" else {"technique"}
    if normalized["kind"] not in allowed or not all(normalized.values()):
        raise ValueError(f"{field} contains an invalid reference")
    return normalized


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
            "toolArguments": display_arguments(arguments, tuple(properties)),
        },
    )


__all__ = ["build_writing_context_tool_registrations"]
