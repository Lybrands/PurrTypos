"""Durable provenance receipts for screenplay source-material reads."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from exceptions import AppError, NotFoundError


async def record_source_refs(
    db,
    *,
    project_id: str,
    agent_run_id: str,
    tool_name: str,
    refs: Iterable[Mapping[str, Any]],
) -> int:
    project = await db.fetch_one(
        "SELECT id FROM screenplay_projects WHERE id = ?",
        [project_id],
    )
    if project is None:
        raise NotFoundError("剧本项目不存在")
    normalized_run_id = str(agent_run_id or "").strip()
    if not normalized_run_id:
        return 0

    inserted = 0
    async with db.transaction():
        for ref in refs:
            source_type = str(ref.get("sourceType") or "").strip()
            source_id = str(ref.get("sourceId") or "").strip()
            revision = str(ref.get("sourceRevision") or "").strip()
            if not source_type or not source_id or not revision:
                continue
            await db.execute(
                "INSERT INTO screenplay_source_refs "
                "(project_id, agent_run_id, tool_name, source_type, source_id, "
                "source_revision, excerpt) VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(agent_run_id, source_type, source_id) DO UPDATE SET "
                "tool_name = excluded.tool_name, "
                "source_revision = excluded.source_revision, "
                "excerpt = excluded.excerpt",
                [
                    project_id,
                    normalized_run_id,
                    str(tool_name or "").strip(),
                    source_type,
                    source_id,
                    revision,
                    str(ref.get("excerpt") or "")[:1000],
                ],
            )
            changed = await db.fetch_one("SELECT changes() AS count")
            inserted += int((changed or {}).get("count") or 0)
    return inserted


async def attach_run_refs_to_document(
    db,
    *,
    project_id: str,
    document_id: str,
    agent_run_id: str | None,
) -> int:
    normalized_run_id = str(agent_run_id or "").strip()
    if not normalized_run_id:
        return 0
    document = await db.fetch_one(
        "SELECT id FROM screenplay_documents "
        "WHERE id = ? AND project_id = ?",
        [document_id, project_id],
    )
    if document is None:
        raise NotFoundError("剧本文档不存在")
    foreign = await db.fetch_one(
        "SELECT id FROM screenplay_source_refs "
        "WHERE agent_run_id = ? AND project_id != ? LIMIT 1",
        [normalized_run_id, project_id],
    )
    if foreign is not None:
        raise AppError("来源记录不属于当前剧本项目")
    await db.execute(
        "UPDATE screenplay_source_refs SET document_id = ? "
        "WHERE project_id = ? AND agent_run_id = ? AND document_id IS NULL",
        [document_id, project_id, normalized_run_id],
    )
    changed = await db.fetch_one("SELECT changes() AS count")
    return int((changed or {}).get("count") or 0)


async def list_source_refs(
    db,
    *,
    project_id: str,
    document_id: str | None = None,
    agent_run_id: str | None = None,
) -> list[dict[str, Any]]:
    project = await db.fetch_one(
        "SELECT id FROM screenplay_projects WHERE id = ?",
        [project_id],
    )
    if project is None:
        raise NotFoundError("剧本项目不存在")
    clauses = ["project_id = ?"]
    params: list[Any] = [project_id]
    if document_id:
        clauses.append("document_id = ?")
        params.append(document_id)
    if agent_run_id:
        clauses.append("agent_run_id = ?")
        params.append(agent_run_id)
    return await db.fetch_all(
        "SELECT * FROM screenplay_source_refs "
        f"WHERE {' AND '.join(clauses)} ORDER BY create_time ASC, id ASC",
        params,
    )
