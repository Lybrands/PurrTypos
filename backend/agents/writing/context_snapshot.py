"""Immutable context-version snapshot for one replacement Writing Run."""

from __future__ import annotations

import asyncio
import hashlib
import json

from agents.writing.context_contract import WritingContextSelection
from agents.writing.read_model import SqliteWritingReadRepository
from application.continuation_context import ContinuationContextService


WRITING_CONTEXT_SNAPSHOT_STATE_KEY = "writingContextSnapshot"


async def _skill_entries(db) -> list[dict[str, object]]:
    """冻结快照中的技能：仅收录声明允许自动使用（metadata.autoUse）的活跃已发布技能。

    未声明 autoUse 的技能对 Agent 不可见——它们只在技能库页面供用户查看。
    """
    from application.writing_technique_service import WritingTechniqueService

    rows = await db.fetch_all(
        "SELECT record_json FROM writing_technique_catalog WHERE kind='skill'")
    service = WritingTechniqueService(db)
    entries: list[dict[str, object]] = []
    for row in rows:
        record = json.loads(str(row["record_json"]))
        metadata = record.get("metadata") or {}
        if (record.get("status") != "active" or not record.get("publishedHead")
                or metadata.get("autoUse") is not True):
            continue
        ref = {"kind": "skill", "id": record["id"], "versionId": record["publishedHead"]}
        try:
            manifest = await asyncio.to_thread(
                service.store("skill").get_version_manifest, ref)
        except Exception:
            continue
        entry_bytes = next(
            (item["size"] for item in manifest["files"] if item["path"] == "SKILL.md"),
            0,
        )
        entries.append({"ref": ref, "metadata": metadata, "entryBytes": entry_bytes})
    return entries


async def build_writing_context_snapshot(
    db,
    *,
    scope,
    selection: WritingContextSelection,
    memory_operations,
) -> dict[str, object]:
    memory_refs = []
    missing_memory_ids = list(selection.selected_long_term_memory_ids)
    memory_state = "not_requested"
    if selection.selected_long_term_memory_ids:
        try:
            records = await memory_operations.get_many(
                book_id=scope.book_id,
                item_ids=selection.selected_long_term_memory_ids,
                include_inactive=False,
            )
            memory_refs = [
                {"id": str(item["id"]), "version": int(item["version"])}
                for item in records
            ]
            present = {item["id"] for item in memory_refs}
            missing_memory_ids = [
                item for item in selection.selected_long_term_memory_ids
                if item not in present
            ]
            memory_state = "available"
        except Exception as error:
            memory_state = str(
                getattr(error, "code", "long_term_memory_unavailable")
            )

    technique = None
    if selection.writing_technique_input_id:
        if scope.session_id is None:
            raise ValueError("writing technique input requires a bound session")
        row = await db.fetch_one(
            "SELECT request_digest, snapshot_json FROM "
            "writing_technique_request_inputs "
            "WHERE id = ? AND book_id = ? AND session_id = ?",
            [
                selection.writing_technique_input_id,
                scope.book_id,
                str(scope.session_id),
            ],
        )
        if row is None:
            raise ValueError(
                "writing technique input is missing or outside the bound session"
            )
        snapshot_json = str(row["snapshot_json"])
        technique = {
            "inputId": selection.writing_technique_input_id,
            "requestDigest": str(row["request_digest"]),
            "snapshotDigest": _digest(snapshot_json),
        }

    continuation = await ContinuationContextService(db).load_for_writing(
        scope.book_id
    )
    repository = SqliteWritingReadRepository(db)
    chapters, outlines, characters = await asyncio.gather(
        repository.writing_chapter_window(scope, limit=25),
        repository.writing_outlines(scope, limit=24),
        repository.characters(scope, limit=32),
    )
    from application.novel_knowledge_service import get_novel_knowledge_service

    knowledge_scope = await get_novel_knowledge_service(db).scope_snapshot(
        scope.book_id,
        scope.chapter_id,
    )
    binding = continuation.get("binding")
    return {
        "schemaVersion": 1,
        "longTermMemory": {
            "state": memory_state,
            "refs": memory_refs,
            "missingIds": missing_memory_ids,
        },
        "writingTechniqueInput": technique,
        "skills": await _skill_entries(db),
        "novelKnowledgeScope": knowledge_scope or None,
        "continuation": {
            "creationMode": continuation["creationMode"],
            "binding": dict(binding) if isinstance(binding, dict) else None,
        },
        "workspaceManifest": {
            "chapters": _manifest_view(chapters, (
                "id", "title", "level", "progress", "sort", "parentId",
                "articleExists", "storedContentNonempty",
            )),
            "outlines": _manifest_view(outlines, (
                "id", "title", "outlineType", "sort", "storedContentNonempty",
            )),
            "characters": _manifest_view(
                characters,
                ("id", "name", "tags"),
            ),
        },
    }


async def snapshot_from_state_or_run_attributes(db, state):
    """Resolve the persisted Run snapshot, falling back before Run creation."""

    run_id = str(getattr(state, "run_id", "") or "").strip()
    if run_id:
        row = await db.fetch_one(
            "SELECT binding_attributes_json FROM ai_agent_runs WHERE id = ?",
            [run_id],
        )
        if row is not None:
            attributes = json.loads(
                str(row.get("binding_attributes_json") or "{}")
            )
            value = attributes.get("writingContextSnapshot")
            if isinstance(value, dict):
                return value
    value = state.domain.get(WRITING_CONTEXT_SNAPSHOT_STATE_KEY)
    return value if isinstance(value, dict) else {}


def _digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _manifest_view(
    page: dict[str, object],
    fields: tuple[str, ...],
) -> dict[str, object]:
    raw_items = page.get("items")
    items = [item for item in raw_items if isinstance(item, dict)] if isinstance(
        raw_items, list
    ) else []
    return {
        "total": int(page.get("total") or 0),
        "offset": int(page.get("offset") or 0),
        "truncated": page.get("nextOffset") is not None,
        "items": [
            {field: item.get(field) for field in fields if field in item}
            for item in items
        ],
    }


__all__ = [
    "WRITING_CONTEXT_SNAPSHOT_STATE_KEY",
    "build_writing_context_snapshot",
    "snapshot_from_state_or_run_attributes",
]
