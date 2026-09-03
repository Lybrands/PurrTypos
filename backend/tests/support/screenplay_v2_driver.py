"""Seed native screenplay Revisions for route integration tests."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from infrastructure.persistence.sqlite_screenplay_v2_repository import (
    SqliteScreenplayV2Repository,
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


async def seed_revision(
    db,
    *,
    project_id: str,
    kind: str,
    title: str,
    content_json: Mapping[str, Any],
    content_text: str,
    derived_from_ids: Sequence[str],
) -> str:
    role = _KIND_TO_ROLE.get(str(kind))
    if role is None:
        raise ValueError(f"unsupported test document kind: {kind}")
    project = await db.fetch_one(
        "SELECT source_kind FROM screenplay_projects WHERE id = ?",
        [project_id],
    )
    assert project is not None, f"Missing fixture project: {project_id}"
    deliverable = await db.fetch_one(
        "SELECT id FROM screenplay_deliverables WHERE project_id = ? AND role = ?",
        [project_id, role],
    )
    assert deliverable is not None, f"Missing fixture deliverable: {role}"

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
                None,
                None,
                "user",
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

    return revision_id


async def accept_revision(db, revision_id: str) -> None:
    project = await db.fetch_one(
        "SELECT p.id, p.revision FROM screenplay_projects AS p "
        "JOIN screenplay_revisions AS r ON r.project_id = p.id WHERE r.id = ?",
        [revision_id],
    )
    assert project is not None, f"Missing fixture revision: {revision_id}"
    await SqliteScreenplayV2Repository(db).accept_revision(
        command_id=f"test-accept-{uuid.uuid4().hex}",
        request_digest=uuid.uuid4().hex,
        project_id=project["id"],
        revision_id=revision_id,
        expected_project_revision=project["revision"],
        confirm_invalidation=True,
    )
