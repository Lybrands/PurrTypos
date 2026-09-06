"""Resolve consumed Part identities from canonical tool results."""

from __future__ import annotations

import json


async def consumed_task_part_keys(db, run_id: str) -> frozenset[str]:
    rows = await db.fetch_all(
        "SELECT payload_json FROM ai_agent_run_events "
        "WHERE run_id = ? AND event_type = 'tool.results' ORDER BY id", [run_id],
    )
    keys: set[str] = set()
    for row in rows:
        for result in json.loads(row["payload_json"]).get("results", []):
            if result.get("tool_name") not in {
                "readScreenplayTaskDependencies",
                "getScreenplaySceneContext",
            } or result.get("error"):
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
