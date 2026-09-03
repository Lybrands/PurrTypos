"""Resolve consumed Part identities from canonical tool results."""

from __future__ import annotations

import json
from infrastructure.persistence.prepared_read_evidence import provided_read_materials
from database.crud.screenplay_source_receipts import record_source_receipts


async def consumed_task_part_keys(db, run_id: str) -> frozenset[str]:
    rows = await db.fetch_all(
        "SELECT payload_json FROM ai_agent_run_events "
        "WHERE run_id = ? AND event_type = 'tool.results' ORDER BY id", [run_id],
    )
    keys: set[str] = set()
    for material in await provided_read_materials(db, run_id):
        if material.get("toolName") == "readScreenplayTaskDependencies":
            keys.update(material.get("partKeys", []))
    for row in rows:
        for result in json.loads(row["payload_json"]).get("results", []):
            if result.get("tool_name") != "readScreenplayTaskDependencies" or result.get("error"):
                continue
            try:
                payload = json.loads(result.get("content") or "{}")
            except (ValueError, TypeError):
                continue
            if not isinstance(payload, dict):
                continue
            for part in payload.get("dependencies", []):
                if isinstance(part, dict) and isinstance(part.get("partKey"), str):
                    keys.add(part["partKey"])
    return frozenset(keys)


async def has_prepared_read(db, run_id, tool_names, *, episode_number=None):
    return any(
        item.get("toolName") in tool_names
        and (episode_number is None or item.get("episodeNumber") == episode_number)
        for item in await provided_read_materials(db, run_id)
    )


async def record_prepared_source_receipts(db, run_id, project_id):
    for item in await provided_read_materials(db, run_id):
        if item.get("projectId") == project_id and item.get("sourceRefs"):
            await record_source_receipts(
                db, project_id=project_id, agent_run_id=run_id,
                tool_name=item["toolName"], refs=item["sourceRefs"],
            )
