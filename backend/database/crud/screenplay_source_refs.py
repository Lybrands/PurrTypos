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
            coverage_mode = str(
                ref.get("coverageMode") or "referenced"
            ).strip()
            if coverage_mode not in {
                "catalog",
                "search",
                "passage",
                "sampled",
                "full",
                "referenced",
            }:
                coverage_mode = "referenced"
            if not source_type or not source_id or not revision:
                continue
            await db.execute(
                "INSERT INTO screenplay_source_refs "
                "(project_id, agent_run_id, tool_name, source_type, source_id, "
                "source_revision, coverage_mode, excerpt) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(agent_run_id, source_type, source_id) DO UPDATE SET "
                "tool_name = excluded.tool_name, "
                "source_revision = excluded.source_revision, "
                "coverage_mode = CASE "
                "WHEN screenplay_source_refs.coverage_mode = 'full' THEN 'full' "
                "WHEN excluded.coverage_mode = 'full' THEN 'full' "
                "WHEN screenplay_source_refs.coverage_mode = 'sampled' THEN 'sampled' "
                "WHEN excluded.coverage_mode = 'sampled' THEN 'sampled' "
                "WHEN excluded.coverage_mode = 'passage' THEN 'passage' "
                "ELSE screenplay_source_refs.coverage_mode END, "
                "excerpt = excluded.excerpt",
                [
                    project_id,
                    normalized_run_id,
                    str(tool_name or "").strip(),
                    source_type,
                    source_id,
                    revision,
                    coverage_mode,
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
        "INSERT OR IGNORE INTO screenplay_document_source_refs "
        "(document_id, source_ref_id) "
        "SELECT ?, id FROM screenplay_source_refs "
        "WHERE project_id = ? AND agent_run_id = ?",
        [document_id, project_id, normalized_run_id],
    )
    changed = await db.fetch_one("SELECT changes() AS count")
    return int((changed or {}).get("count") or 0)


async def attach_artifact_refs_to_document(
    db,
    *,
    project_id: str,
    document_id: str,
    artifact_ref: str | None,
) -> int:
    """Link the immutable source receipts that authorized an Artifact.

    The Run that emits the final document proposal is not necessarily the Run
    that created the Artifact and read its sources.  Artifact continuation is
    host-owned, so provenance must follow the Artifact identity instead of the
    last visible Run.
    """

    normalized_ref = str(artifact_ref or "").strip()
    if not normalized_ref:
        return 0
    artifact_id = normalized_ref.rsplit("/", 1)[-1].strip()
    if not artifact_id:
        return 0
    artifact = await db.fetch_one(
        "SELECT owner_id, run_id, created_by_run_id, resource_ref "
        "FROM ai_agent_artifacts WHERE id = ?",
        [artifact_id],
    )
    if (
        artifact is None
        or str(artifact.get("owner_id") or "") != project_id
        or str(artifact.get("resource_ref") or "") != normalized_ref
    ):
        return 0
    run_ids = list(dict.fromkeys(
        str(value).strip()
        for value in (
            artifact.get("run_id"),
            artifact.get("created_by_run_id"),
        )
        if str(value or "").strip()
    ))
    if not run_ids:
        return 0
    placeholders = ",".join("?" for _ in run_ids)
    await db.execute(
        "INSERT OR IGNORE INTO screenplay_document_source_refs "
        "(document_id, source_ref_id) "
        "SELECT ?, id FROM screenplay_source_refs "
        f"WHERE project_id = ? AND agent_run_id IN ({placeholders})",
        [document_id, project_id, *run_ids],
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
    clauses = ["r.project_id = ?"]
    params: list[Any] = [project_id]
    if document_id:
        clauses.append(
            "(r.document_id = ? OR EXISTS ("
            "SELECT 1 FROM screenplay_document_source_refs AS dsr "
            "WHERE dsr.source_ref_id = r.id AND dsr.document_id = ?))"
        )
        params.extend([document_id, document_id])
    if agent_run_id:
        clauses.append("r.agent_run_id = ?")
        params.append(agent_run_id)
    document_projection = "?" if document_id else "r.document_id"
    if document_id:
        params = [document_id, *params]
    return await db.fetch_all(
        "SELECT r.id, r.project_id, " + document_projection + " "
        "AS document_id, r.agent_run_id, r.tool_name, r.source_type, "
        "r.source_id, r.source_revision, r.coverage_mode, r.excerpt, "
        "r.create_time FROM screenplay_source_refs AS r "
        f"WHERE {' AND '.join(clauses)} "
        "ORDER BY r.create_time ASC, r.id ASC",
        params,
    )
