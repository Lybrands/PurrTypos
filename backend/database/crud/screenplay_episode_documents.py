"""Read structured screenplay episode snapshots from native Revision Parts."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from database.crud.screenplay_head_projection import list_revision_episode_parts


EPISODIC_DOCUMENT_KINDS = frozenset({
    "episode_outline",
    "scene_list",
    "review",
})


async def list_episode_rows(
    db,
    *,
    document_id: str,
    include_content: bool = False,
) -> list[dict[str, Any]]:
    rows = await list_revision_episode_parts(db, document_id)
    if rows is None:
        return []
    return [
        {
            key: value for key, value in dict(row).items()
            if include_content or key not in {"content_json", "content_text"}
        }
        for row in rows
    ]


async def get_episode(
    db,
    *,
    document_id: str,
    episode_number: int,
) -> dict[str, Any] | None:
    rows = await list_episode_rows(
        db,
        document_id=document_id,
        include_content=True,
    )
    return next(
        (
            row for row in rows
            if int(row.get("episode_number") or 0) == int(episode_number)
        ),
        None,
    )


async def assemble_episode_document(
    db,
    document: Mapping[str, Any] | None,
    *,
    include_text: bool = False,
) -> dict[str, Any] | None:
    del db, include_text
    return dict(document) if document is not None else None


__all__ = [
    "EPISODIC_DOCUMENT_KINDS",
    "assemble_episode_document",
    "get_episode",
    "list_episode_rows",
]
