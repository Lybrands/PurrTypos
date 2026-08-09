"""Durable source-read receipts captured during native screenplay Runs."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from exceptions import NotFoundError


async def record_source_receipts(
    db,
    *,
    project_id: str,
    agent_run_id: str,
    tool_name: str,
    refs: Iterable[Mapping[str, object]],
) -> int:
    project = await db.fetch_one(
        "SELECT id FROM screenplay_projects "
        "WHERE id = ? AND source_snapshot_json IS NOT NULL",
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
                "INSERT INTO screenplay_source_receipts "
                "(project_id, agent_run_id, tool_name, source_type, source_id, "
                "source_revision, coverage_mode, excerpt) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(agent_run_id, source_type, source_id) DO UPDATE SET "
                "tool_name = excluded.tool_name, "
                "source_revision = excluded.source_revision, "
                "coverage_mode = CASE "
                "WHEN screenplay_source_receipts.coverage_mode = 'full' "
                "THEN 'full' "
                "WHEN excluded.coverage_mode = 'full' THEN 'full' "
                "WHEN screenplay_source_receipts.coverage_mode = 'sampled' "
                "THEN 'sampled' "
                "WHEN excluded.coverage_mode = 'sampled' THEN 'sampled' "
                "WHEN excluded.coverage_mode = 'passage' THEN 'passage' "
                "ELSE screenplay_source_receipts.coverage_mode END, "
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


__all__ = ["record_source_receipts"]
