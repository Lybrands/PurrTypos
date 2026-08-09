"""Read screenplay draft episode snapshots from native Revision Parts."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from database.crud.screenplay_head_projection import (
    get_current_document,
    get_document as get_project_document,
    list_revision_episode_parts,
)


def _object(value: object) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


async def list_episode_rows(
    db,
    *,
    project_id: str | None,
    draft_document_id: str | None = None,
    status: str | None = "accepted",
    include_content: bool = False,
) -> list[dict[str, Any]]:
    del status
    document = None
    if draft_document_id:
        revision_id = draft_document_id
        if project_id:
            document = await get_project_document(db, project_id, revision_id)
    elif project_id:
        document = await get_current_document(
            db,
            project_id,
            kind="scene_draft",
        )
        if document is None:
            return []
        revision_id = str(document["id"])
    else:
        return []
    rows = await list_revision_episode_parts(db, revision_id)
    if rows is None:
        return []
    return [
        _episode_view(row, document=document, include_content=include_content)
        for row in rows
    ]


async def get_episode(
    db,
    *,
    project_id: str,
    episode_number: int,
    draft_document_id: str | None = None,
) -> dict[str, Any] | None:
    rows = await list_episode_rows(
        db,
        project_id=project_id,
        draft_document_id=draft_document_id,
        status=None,
        include_content=True,
    )
    return next(
        (
            row for row in rows
            if int(row.get("episode_number") or 0) == int(episode_number)
        ),
        None,
    )


async def assemble_draft_document(
    db,
    document: Mapping[str, Any] | None,
    *,
    include_text: bool = False,
) -> dict[str, Any] | None:
    if document is None:
        return None
    result = dict(document)
    episodes = await list_episode_rows(
        db,
        project_id=str(result.get("project_id") or "") or None,
        draft_document_id=str(result.get("id") or ""),
        status=None,
        include_content=True,
    )
    content = _object(result.get("content_json"))
    completed_scene_ids = [
        scene_id
        for episode in episodes
        for scene_id in episode.get("scene_ids", [])
    ]
    content.update({
        "completedSceneIds": completed_scene_ids,
        "completedSceneCount": len(completed_scene_ids),
        "sceneExecutions": [
            execution
            for episode in episodes
            for execution in episode.get("scene_executions", [])
        ],
        "episodeCount": len(episodes),
    })
    result["content_json"] = content
    if include_text:
        result["content_text"] = "\n\n".join(
            str(episode.get("content_text") or "").strip()
            for episode in episodes
            if str(episode.get("content_text") or "").strip()
        )
    return result


async def latest_accepted_draft_manifest(
    db,
    project_id: str,
) -> dict[str, Any] | None:
    return await get_current_document(db, project_id, kind="scene_draft")


async def latest_accepted_draft_document(
    db,
    project_id: str,
    *,
    include_text: bool = False,
) -> dict[str, Any] | None:
    return await assemble_draft_document(
        db,
        await latest_accepted_draft_manifest(db, project_id),
        include_text=include_text,
    )


def _episode_view(
    row: Mapping[str, Any],
    *,
    document: Mapping[str, Any] | None,
    include_content: bool,
) -> dict[str, Any]:
    payload = _object(row.get("content_json"))
    scene_ids = [
        str(value).strip()
        for value in payload.get("sceneIds", [])
        if str(value).strip()
    ]
    result = {
        "id": str(row.get("id") or ""),
        "project_id": str(row.get("project_id") or ""),
        "draft_document_id": str(row.get("document_id") or ""),
        "scene_list_document_id": str(
            _object((document or {}).get("content_json")).get("sceneListId")
            or ""
        ),
        "episode_number": int(row.get("episode_number") or 0),
        "title": str(row.get("title") or ""),
        "scene_ids": scene_ids,
        "scene_count": len(scene_ids),
        "continuity_excerpt": str(
            payload.get("continuitySummary") or ""
        )[:240],
        "version": int(row.get("version") or 1),
        "status": str(row.get("status") or "accepted"),
        "storage_mode": "revision_part",
        "create_time": row.get("create_time"),
        "update_time": row.get("update_time"),
    }
    if include_content:
        result.update({
            "scene_executions": [
                dict(item)
                for item in payload.get("sceneExecutions", [])
                if isinstance(item, Mapping)
            ],
            "scene_texts": [
                dict(item)
                for item in payload.get("sceneTexts", [])
                if isinstance(item, Mapping)
            ],
            "content_text": str(
                payload.get("contentText")
                or row.get("content_text")
                or ""
            ),
            "continuity_summary": str(
                payload.get("continuitySummary") or ""
            ),
        })
    return result


__all__ = [
    "assemble_draft_document",
    "get_episode",
    "latest_accepted_draft_document",
    "latest_accepted_draft_manifest",
    "list_episode_rows",
]
