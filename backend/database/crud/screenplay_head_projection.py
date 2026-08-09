"""Internal document-shaped queries over native screenplay Revisions."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any


_ROLE_DEFAULT_KIND = {
    "sourceAnalysis": "source_analysis",
    "creativeBrief": "creative_brief",
    "structure": "beat_sheet",
    "sceneList": "scene_list",
    "screenplayDraft": "scene_draft",
    "review": "review",
}

_KIND_ROLE = {
    "source_analysis": "sourceAnalysis",
    "creative_brief": "creativeBrief",
    "beat_sheet": "structure",
    "episode_outline": "structure",
    "scene_list": "sceneList",
    "scene_draft": "screenplayDraft",
    "review": "review",
}

_SERIES_FORMATS = frozenset({"连续剧", "竖屏短剧", "series", "verticalSeries"})


def _object(value: object) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _list(value: object) -> list[Any]:
    if isinstance(value, list):
        return list(value)
    try:
        parsed = json.loads(str(value or "[]"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


async def is_native_v2_project(db, project_id: str) -> bool:
    row = await db.fetch_one(
        "SELECT source_snapshot_json FROM screenplay_projects WHERE id = ?",
        [str(project_id or "").strip()],
    )
    return row is not None and row.get("source_snapshot_json") is not None


async def list_current_documents(
    db,
    project_id: str,
) -> list[dict[str, Any]]:
    normalized_project_id = str(project_id or "").strip()
    project = await db.fetch_one(
        "SELECT id, format, source_snapshot_json FROM screenplay_projects "
        "WHERE id = ?",
        [normalized_project_id],
    )
    if project is None:
        return []
    if project.get("source_snapshot_json") is None:
        return []
    rows = await db.fetch_all(
        "SELECT r.*, d.role, p.payload_json, p.content_text AS part_content_text "
        "FROM screenplay_project_heads AS h "
        "JOIN screenplay_deliverables AS d ON d.id = h.deliverable_id "
        "JOIN screenplay_revisions AS r ON r.id = h.revision_id "
        "JOIN screenplay_revision_parts AS p ON p.revision_id = r.id "
        "AND p.part_type = 'document' AND p.part_key = 'main' "
        "WHERE h.project_id = ? ORDER BY d.role ASC",
        [normalized_project_id],
    )
    return [
        await _revision_document_view(
            db,
            row,
            project_format=str(project.get("format") or ""),
            accepted=True,
        )
        for row in rows
    ]


async def get_current_document(
    db,
    project_id: str,
    *,
    kind: str | None = None,
    kinds: Sequence[str] = (),
    role: str | None = None,
) -> dict[str, Any] | None:
    requested_kinds = {
        str(value).strip() for value in (kind, *kinds)
        if str(value or "").strip()
    }
    requested_role = str(role or "").strip()
    if not requested_role and len(requested_kinds) == 1:
        requested_role = _KIND_ROLE.get(next(iter(requested_kinds)), "")
    documents = await list_current_documents(db, project_id)
    matches = [
        document for document in documents
        if (
            not requested_role
            or _KIND_ROLE.get(str(document.get("kind") or ""))
            == requested_role
        )
        and (
            not requested_kinds
            or str(document.get("kind") or "") in requested_kinds
        )
    ]
    return max(
        matches,
        key=lambda item: int(item.get("version") or 0),
        default=None,
    )


async def get_latest_document(
    db,
    project_id: str,
    *,
    kind: str,
) -> dict[str, Any] | None:
    """Return the authoritative Head for a deliverable kind."""

    normalized_project_id = str(project_id or "").strip()
    project = await db.fetch_one(
        "SELECT source_snapshot_json FROM screenplay_projects WHERE id = ?",
        [normalized_project_id],
    )
    if project is None:
        return None
    if project.get("source_snapshot_json") is None:
        return None
    return await get_current_document(
        db,
        normalized_project_id,
        kind=kind,
    )


async def get_document(
    db,
    project_id: str,
    document_id: str,
) -> dict[str, Any] | None:
    normalized_project_id = str(project_id or "").strip()
    normalized_document_id = str(document_id or "").strip()
    project = await db.fetch_one(
        "SELECT id, format, source_snapshot_json FROM screenplay_projects "
        "WHERE id = ?",
        [normalized_project_id],
    )
    if project is None or project.get("source_snapshot_json") is None:
        return None
    row = await db.fetch_one(
        "SELECT r.*, d.role, p.payload_json, p.content_text AS part_content_text, "
        "CASE WHEN h.revision_id IS NULL THEN 0 ELSE 1 END AS is_head, "
        "CASE WHEN EXISTS (SELECT 1 FROM screenplay_acceptance_events AS a "
        "WHERE a.revision_id = r.id) THEN 1 ELSE 0 END AS was_accepted "
        "FROM screenplay_revisions AS r "
        "JOIN screenplay_deliverables AS d ON d.id = r.deliverable_id "
        "JOIN screenplay_revision_parts AS p ON p.revision_id = r.id "
        "AND p.part_type = 'document' AND p.part_key = 'main' "
        "LEFT JOIN screenplay_project_heads AS h ON h.project_id = r.project_id "
        "AND h.revision_id = r.id WHERE r.id = ? AND r.project_id = ?",
        [normalized_document_id, normalized_project_id],
    )
    if row is None:
        return None
    return await _revision_document_view(
        db,
        row,
        project_format=str(project.get("format") or ""),
        accepted=bool(row.get("is_head") or row.get("was_accepted")),
    )


async def find_document(
    db,
    document_id: str,
) -> dict[str, Any] | None:
    """Resolve a native Revision by id."""

    normalized_document_id = str(document_id or "").strip()
    owner = await db.fetch_one(
        "SELECT project_id FROM screenplay_revisions WHERE id = ?",
        [normalized_document_id],
    )
    if owner is None:
        return None
    return await get_document(
        db,
        str(owner["project_id"]),
        normalized_document_id,
    )


async def list_revision_episode_parts(
    db,
    revision_id: str,
) -> list[dict[str, Any]] | None:
    revision = await db.fetch_one(
        "SELECT r.id, r.project_id, r.revision_no, r.create_time, d.role, "
        "p.payload_json AS document_payload "
        "FROM screenplay_revisions AS r "
        "JOIN screenplay_deliverables AS d ON d.id = r.deliverable_id "
        "JOIN screenplay_revision_parts AS p ON p.revision_id = r.id "
        "AND p.part_type = 'document' AND p.part_key = 'main' WHERE r.id = ?",
        [str(revision_id or "").strip()],
    )
    if revision is None:
        return None
    content = _object(revision.get("document_payload"))
    kind = _document_kind(
        role=str(revision.get("role") or ""),
        content=content,
        project_format="",
    )
    rows = await db.fetch_all(
        "SELECT part_key, position, payload_json, content_text, create_time "
        "FROM screenplay_revision_parts WHERE revision_id = ? "
        "AND part_type = 'episode' ORDER BY position ASC",
        [revision["id"]],
    )
    result: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        payload = _object(row.get("payload_json"))
        episode_number = _episode_number(
            payload.get("episodeNumber")
            or payload.get("number")
            or row.get("part_key")
            or index
        )
        if episode_number is None:
            continue
        if kind == "episode_outline" and "episode" not in payload:
            content_json = {"episode": payload}
        else:
            content_json = payload
        item_ids = _episode_item_ids(kind, content_json)
        result.append({
            "id": f"{revision['id']}:episode:{episode_number}",
            "project_id": str(revision["project_id"]),
            "document_id": str(revision["id"]),
            "document_kind": kind,
            "episode_number": episode_number,
            "title": str(
                payload.get("title")
                or f"第 {episode_number} 集"
            ),
            "item_ids": item_ids,
            "item_count": len(item_ids),
            "version": int(revision.get("revision_no") or 1),
            "status": "accepted",
            "storage_mode": "revision_part",
            "content_json": content_json,
            "content_text": str(row.get("content_text") or ""),
            "create_time": row.get("create_time") or revision.get("create_time"),
            "update_time": row.get("create_time") or revision.get("create_time"),
        })
    return result


async def _revision_document_view(
    db,
    row: Mapping[str, Any],
    *,
    project_format: str,
    accepted: bool,
) -> dict[str, Any]:
    content = _object(row.get("payload_json"))
    summary = _object(row.get("summary_json"))
    inputs = await db.fetch_all(
        "SELECT input_revision_id FROM screenplay_revision_inputs "
        "WHERE revision_id = ? ORDER BY input_role ASC",
        [row["id"]],
    )
    return {
        "id": str(row["id"]),
        "project_id": str(row["project_id"]),
        "kind": _document_kind(
            role=str(row.get("role") or ""),
            content=content,
            project_format=project_format,
        ),
        "title": str(summary.get("title") or ""),
        "content_json": content,
        "content_text": str(row.get("part_content_text") or ""),
        "version": int(row.get("revision_no") or 1),
        "status": "accepted" if accepted else "candidate",
        "derived_from_ids": [str(item["input_revision_id"]) for item in inputs],
        "operation_id": row.get("operation_id"),
        "storage_model": "revision_parts",
        "create_time": row.get("create_time"),
        "update_time": row.get("create_time"),
    }


def _document_kind(
    *,
    role: str,
    content: Mapping[str, Any],
    project_format: str,
) -> str:
    declared = str(content.get("documentKind") or "").strip()
    if declared in _KIND_ROLE:
        return declared
    if role == "structure" and project_format in _SERIES_FORMATS:
        return "episode_outline"
    return _ROLE_DEFAULT_KIND.get(role, declared or role)


def _episode_number(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _episode_item_ids(kind: str, content: Mapping[str, Any]) -> list[str]:
    if kind == "episode_outline":
        episode = content.get("episode")
        return [str(episode.get("id"))] if isinstance(episode, Mapping) and str(
            episode.get("id") or ""
        ).strip() else []
    fields = (
        ("scenes", "id"),
        ("issues", "id"),
        ("verificationResults", "issueId"),
    )
    return list(dict.fromkeys(
        str(item.get(identity)).strip()
        for field, identity in fields
        for item in _list(content.get(field))
        if isinstance(item, Mapping) and str(item.get(identity) or "").strip()
    ))


__all__ = [
    "find_document",
    "get_current_document",
    "get_document",
    "get_latest_document",
    "is_native_v2_project",
    "list_current_documents",
    "list_revision_episode_parts",
]
