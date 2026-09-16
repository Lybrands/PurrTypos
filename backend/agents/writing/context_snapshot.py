"""Immutable context-version snapshot for one replacement Writing Run."""

from __future__ import annotations

import hashlib
import json

from agents.writing.context_contract import WritingContextSelection
from application.continuation_context import ContinuationContextService


WRITING_CONTEXT_SNAPSHOT_STATE_KEY = "writingContextSnapshot"


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
        "novelKnowledgeScope": knowledge_scope or None,
        "continuation": {
            "creationMode": continuation["creationMode"],
            "binding": dict(binding) if isinstance(binding, dict) else None,
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


__all__ = [
    "WRITING_CONTEXT_SNAPSHOT_STATE_KEY",
    "build_writing_context_snapshot",
    "snapshot_from_state_or_run_attributes",
]
