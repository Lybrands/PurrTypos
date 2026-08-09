"""Native-v2 screenplay fixtures used by integration tests.

This module deliberately lives under ``tests``: it gives older integration
scenarios a compact way to seed Revisions without reintroducing the removed
runtime document CRUD or its tables.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from application.screenplay_v2_service import ScreenplayV2ProjectService
from database.crud.screenplay_head_projection import (
    find_document,
    list_current_documents,
    list_revision_episode_parts,
)
from database.crud.screenplay_project_deletion import (
    delete_screenplay_project_data,
)
from database.crud.screenplay_source_receipts import record_source_receipts
from domains.screenplay.project_aggregate import public_format
from exceptions import NotFoundError
from infrastructure.persistence.sqlite_screenplay_v2_repository import (
    SqliteScreenplayV2Repository,
    _agent_candidate_parts,
)
from schemas.screenplay_v2 import (
    AcceptScreenplayV2RevisionRequest,
    CreateScreenplayV2ProjectRequest,
)


_KIND_TO_ROLE = {
    "source_analysis": "sourceAnalysis",
    "creative_brief": "creativeBrief",
    "beat_sheet": "structure",
    "episode_outline": "structure",
    "scene_list": "sceneList",
    "scene_draft": "screenplayDraft",
    "review": "review",
}

_PREREQUISITE = {
    "creativeBrief": "sourceAnalysis",
    "structure": "creativeBrief",
    "sceneList": "structure",
    "screenplayDraft": "sceneList",
    "review": "screenplayDraft",
}


def _dump(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _digest(content: Mapping[str, Any], content_text: str) -> str:
    return hashlib.sha256(_dump({
        "payload": dict(content),
        "contentText": str(content_text or ""),
    }).encode("utf-8")).hexdigest()


async def _project_row(db, project_id: str) -> dict[str, Any] | None:
    row = await db.fetch_one(
        "SELECT * FROM screenplay_projects WHERE id = ? "
        "AND source_snapshot_json IS NOT NULL",
        [project_id],
    )
    if row is None:
        return None
    result = dict(row)
    try:
        result["source_scope"] = json.loads(
            str(result.get("source_scope_json") or "{}")
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        result["source_scope"] = {}
    return result


async def create_project(
    db,
    *,
    title: str,
    source_kind: str,
    source_book_id: str | None,
    screenplay_format: str,
    approach: str,
    premise: str,
    source_scope: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    source: dict[str, Any]
    if source_kind == "book":
        raw_scope = dict(source_scope or {"mode": "whole_book"})
        mode_map = {
            "whole_book": "wholeBook",
            "first_chapters": "firstChapters",
            "first_volumes": "firstVolumes",
            "selected_chapters": "selectedChapters",
            "selected_volumes": "selectedVolumes",
        }
        source = {
            "type": "book",
            "bookId": str(source_book_id or ""),
            "scope": {
                "mode": mode_map.get(
                    str(raw_scope.get("mode") or "whole_book"),
                    "wholeBook",
                ),
                "count": raw_scope.get("count"),
                "chapterIds": list(raw_scope.get("chapterIds") or []),
                "volumeIds": list(raw_scope.get("volumeIds") or []),
            },
        }
    else:
        source = {"type": "original"}
    request = CreateScreenplayV2ProjectRequest.model_validate({
        "title": title,
        "format": public_format(screenplay_format),
        "source": source,
        "brief": {"approach": approach, "premise": premise},
    })
    workspace = await ScreenplayV2ProjectService(db).create_project(
        command_id=f"test-create-{uuid.uuid4().hex}",
        request=request,
    )
    project_id = str(workspace["project"]["id"])

    # A new project owns a Working Copy, but scenarios that exercise context
    # selection also need an addressable immutable candidate.
    initial = await create_document(
        db,
        project_id=project_id,
        kind="creative_brief",
        title="创作简报",
        content_json={
            "schemaVersion": 1,
            "documentKind": "creative_brief",
            "projectTitle": title,
            "approach": approach,
            "premise": premise,
        },
        content_text=premise,
        derived_from_ids=[],
    )
    project = await _project_row(db, project_id)
    if project is None:
        raise RuntimeError("native v2 screenplay project was not persisted")
    return {"project": project, "initialDocument": initial}


async def create_document(
    db,
    *,
    project_id: str,
    kind: str,
    title: str,
    content_json: Mapping[str, Any],
    content_text: str,
    derived_from_ids: Sequence[str],
    source_run_id: str | None = None,
) -> dict[str, Any]:
    role = _KIND_TO_ROLE.get(str(kind))
    if role is None:
        raise ValueError(f"unsupported test document kind: {kind}")
    project = await _project_row(db, project_id)
    if project is None:
        raise NotFoundError("剧本项目不存在")
    deliverable = await db.fetch_one(
        "SELECT id FROM screenplay_deliverables WHERE project_id = ? AND role = ?",
        [project_id, role],
    )
    if deliverable is None:
        raise NotFoundError("剧本交付物不存在")

    normalized_content = dict(content_json)
    normalized_content.setdefault("documentKind", str(kind))
    latest = await db.fetch_one(
        "SELECT COALESCE(MAX(revision_no), 0) AS revision_no "
        "FROM screenplay_revisions WHERE deliverable_id = ?",
        [deliverable["id"]],
    )
    revision_no = int((latest or {}).get("revision_no") or 0) + 1
    revision_id = f"sprev_test_{uuid.uuid4().hex}"
    parent = await db.fetch_one(
        "SELECT revision_id FROM screenplay_project_heads "
        "WHERE project_id = ? AND deliverable_id = ?",
        [project_id, deliverable["id"]],
    )
    parts = await SqliteScreenplayV2Repository(
        db
    )._materialize_agent_candidate_parts(
        project_id=project_id,
        target_role=role,
        parent_revision_id=(parent or {}).get("revision_id"),
        content_json=normalized_content,
        content_text=content_text,
    )
    finalizing_run_id = str(source_run_id or "").strip() or None

    async with db.transaction():
        await db.execute(
            "INSERT INTO screenplay_revisions "
            "(id, project_id, deliverable_id, revision_no, parent_revision_id, "
            "schema_version, content_digest, summary_json, root_run_id, "
            "finalizing_run_id, created_by) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)",
            [
                revision_id,
                project_id,
                deliverable["id"],
                revision_no,
                (parent or {}).get("revision_id"),
                _digest(normalized_content, content_text),
                _dump({
                    "role": role,
                    "proposalKind": kind,
                    "title": title.strip(),
                    "derivedFromIds": [str(item) for item in derived_from_ids],
                    "partCount": len(parts),
                }),
                finalizing_run_id,
                finalizing_run_id,
                "agent" if finalizing_run_id else "user",
            ],
        )
        for part in parts:
            await db.execute(
                "INSERT INTO screenplay_revision_parts "
                "(revision_id, part_type, part_key, position, payload_json, "
                "content_text, content_digest) VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    revision_id,
                    part["partType"],
                    part["partKey"],
                    part["position"],
                    _dump(part["payload"]),
                    part["contentText"],
                    part["contentDigest"],
                ],
            )

        prerequisite = _PREREQUISITE.get(role)
        if role == "creativeBrief" and project.get("source_kind") != "book":
            prerequisite = None
        if prerequisite:
            input_row = await db.fetch_one(
                "SELECT h.revision_id FROM screenplay_project_heads AS h "
                "JOIN screenplay_deliverables AS d ON d.id = h.deliverable_id "
                "WHERE h.project_id = ? AND d.role = ?",
                [project_id, prerequisite],
            )
            if input_row is not None:
                await db.execute(
                    "INSERT INTO screenplay_revision_inputs "
                    "(revision_id, input_role, input_revision_id) VALUES (?, ?, ?)",
                    [revision_id, prerequisite, input_row["revision_id"]],
                )

        run_ids = [finalizing_run_id] if finalizing_run_id else []
        artifact_ref = str(normalized_content.get("artifactRef") or "").strip()
        if artifact_ref:
            artifact_id = artifact_ref.rsplit("/", 1)[-1].strip()
            artifact = await db.fetch_one(
                "SELECT run_id, created_by_run_id FROM ai_agent_artifacts "
                "WHERE id = ? AND owner_id = ? AND resource_ref = ?",
                [artifact_id, project_id, artifact_ref],
            )
            if artifact is not None:
                run_ids.extend([
                    str(artifact.get("run_id") or "").strip(),
                    str(artifact.get("created_by_run_id") or "").strip(),
                ])
        run_ids = list(dict.fromkeys(item for item in run_ids if item))
        if run_ids:
            placeholders = ",".join("?" for _ in run_ids)
            await db.execute(
                "INSERT OR IGNORE INTO screenplay_revision_source_refs "
                "(revision_id, source_type, source_id, source_revision, excerpt) "
                "SELECT ?, source_type, source_id, source_revision, excerpt "
                "FROM screenplay_source_receipts WHERE project_id = ? "
                f"AND agent_run_id IN ({placeholders})",
                [revision_id, project_id, *run_ids],
            )

    result = await find_document(db, revision_id)
    if result is None:
        raise RuntimeError("native v2 screenplay revision was not persisted")
    return result


async def accept_document(db, document_id: str) -> dict[str, Any]:
    document = await find_document(db, document_id)
    if document is None:
        raise NotFoundError("剧本版本不存在")
    project = await _project_row(db, str(document["project_id"]))
    if project is None:
        raise NotFoundError("剧本项目不存在")
    await SqliteScreenplayV2Repository(db).accept_revision(
        command_id=f"test-accept-{uuid.uuid4().hex}",
        request_digest=uuid.uuid4().hex,
        project_id=str(project["id"]),
        revision_id=document_id,
        expected_project_revision=int(project.get("revision") or 1),
        confirm_invalidation=True,
    )
    accepted = await find_document(db, document_id)
    if accepted is None:
        raise RuntimeError("accepted native v2 screenplay revision disappeared")
    return accepted


async def get_project(db, project_id: str) -> dict[str, Any] | None:
    return await _project_row(db, project_id)


async def get_document(db, document_id: str) -> dict[str, Any] | None:
    return await find_document(db, document_id)


async def list_documents(db, project_id: str) -> list[dict[str, Any]]:
    return await list_current_documents(db, project_id)


async def list_accepted_draft_episodes(
    db,
    project_id: str,
) -> list[dict[str, Any]]:
    head = await db.fetch_one(
        "SELECT h.revision_id FROM screenplay_project_heads AS h "
        "JOIN screenplay_deliverables AS d ON d.id = h.deliverable_id "
        "WHERE h.project_id = ? AND d.role = 'screenplayDraft'",
        [project_id],
    )
    if head is None:
        return []
    return await list_revision_episode_parts(db, str(head["revision_id"])) or []


async def delete_project(db, project_id: str) -> bool:
    return await delete_screenplay_project_data(db, project_id)


async def get_or_create_agent_session(db, project_id: str) -> dict[str, Any]:
    return await ScreenplayV2ProjectService(db).ensure_current_session(project_id)


record_source_refs = record_source_receipts


async def list_source_refs(
    db,
    *,
    project_id: str,
    document_id: str | None = None,
    agent_run_id: str | None = None,
) -> list[dict[str, Any]]:
    if document_id:
        return await db.fetch_all(
            "SELECT NULL AS id, r.project_id, s.revision_id AS document_id, "
            "NULL AS agent_run_id, NULL AS tool_name, s.source_type, "
            "s.source_id, s.source_revision, 'referenced' AS coverage_mode, "
            "s.excerpt, s.create_time FROM screenplay_revision_source_refs AS s "
            "JOIN screenplay_revisions AS r ON r.id = s.revision_id "
            "WHERE r.project_id = ? AND s.revision_id = ? "
            "ORDER BY s.create_time ASC, s.source_type ASC, s.source_id ASC",
            [project_id, document_id],
        )
    clauses = ["project_id = ?"]
    params: list[Any] = [project_id]
    if agent_run_id:
        clauses.append("agent_run_id = ?")
        params.append(agent_run_id)
    return await db.fetch_all(
        "SELECT NULL AS id, project_id, NULL AS document_id, agent_run_id, "
        "tool_name, source_type, source_id, source_revision, coverage_mode, "
        "excerpt, create_time FROM screenplay_source_receipts "
        f"WHERE {' AND '.join(clauses)} "
        "ORDER BY create_time ASC, source_type ASC, source_id ASC",
        params,
    )


__all__ = [
    "accept_document",
    "create_document",
    "create_project",
    "delete_project",
    "get_document",
    "get_or_create_agent_session",
    "get_project",
    "list_accepted_draft_episodes",
    "list_documents",
    "list_source_refs",
    "record_source_refs",
]
