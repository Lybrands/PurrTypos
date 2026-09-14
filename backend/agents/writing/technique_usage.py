"""Read-only technique usage projection for replacement Writing Runs."""

from __future__ import annotations

import hashlib
import json


async def project_replacement_writing_technique_usage(db, run_id: str):
    """Return ``None`` for non-replacement Runs, otherwise a safe usage list."""

    run = await db.fetch_one(
        "SELECT binding_attributes_json FROM ai_agent_runs WHERE id=?",
        [run_id],
    )
    if run is None:
        return None
    try:
        attributes = json.loads(run["binding_attributes_json"] or "{}")
    except (TypeError, json.JSONDecodeError):
        return None
    implementation = attributes.get("agentImplementation") or {}
    if (
        implementation.get("agentKind") != "writing"
        or implementation.get("implementationId") != "purra-native"
    ):
        return None

    frozen_input = (
        (attributes.get("writingContextSnapshot") or {}).get(
            "writingTechniqueInput"
        )
    )
    if not isinstance(frozen_input, dict):
        return []
    selection = attributes.get("contextSelection") or {}
    input_id = selection.get("writingTechniqueInputId")
    if not input_id or frozen_input.get("inputId") != input_id:
        return []
    row = await db.fetch_one(
        "SELECT request_digest,snapshot_json FROM "
        "writing_technique_request_inputs WHERE id=?",
        [input_id],
    )
    if row is None:
        return []
    snapshot_json = str(row["snapshot_json"])
    expected_digest = (
        "sha256:" + hashlib.sha256(snapshot_json.encode()).hexdigest()
    )
    if (
        frozen_input.get("requestDigest") != row["request_digest"]
        or frozen_input.get("snapshotDigest") != expected_digest
    ):
        return []
    try:
        snapshot = json.loads(snapshot_json)
    except json.JSONDecodeError:
        return []
    manual = snapshot.get("manual") or []
    candidates = [*manual, *(snapshot.get("candidates") or [])]
    events = await db.fetch_all(
        "SELECT payload_json FROM ai_agent_run_events WHERE run_id=? "
        "AND event_type='tool.results' ORDER BY id",
        [run_id],
    )
    usage: dict[str, dict] = {}
    for event in events:
        try:
            results = json.loads(event["payload_json"]).get("results", [])
        except (TypeError, json.JSONDecodeError):
            continue
        for result in results:
            if (
                not isinstance(result, dict)
                or result.get("tool_name") != "readWritingTechniqueContext"
                or result.get("error")
            ):
                continue
            try:
                payload = json.loads(result.get("content") or "{}")
            except (TypeError, json.JSONDecodeError):
                continue
            for entry in payload.get("entries") or []:
                if not isinstance(entry, dict):
                    continue
                member = entry.get("ref")
                for selected_ref in entry.get("sources") or []:
                    candidate = next(
                        (
                            item
                            for item in candidates
                            if item.get("ref") == selected_ref
                            and member in (item.get("members") or [])
                        ),
                        None,
                    )
                    if candidate is None:
                        continue
                    key = json.dumps(
                        selected_ref,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    item = usage.setdefault(
                        key,
                        {
                            "ref": selected_ref,
                            "name": (candidate.get("metadata") or {}).get(
                                "name", ""
                            ),
                            "source": (
                                "manual"
                                if any(
                                    value.get("ref") == selected_ref
                                    for value in manual
                                )
                                else "auto"
                            ),
                            "files": [],
                        },
                    )
                    file = {
                        "ref": member,
                        "path": entry.get("path"),
                        "sha256": entry.get("sha256"),
                    }
                    if file not in item["files"]:
                        item["files"].append(file)
    return list(usage.values())


__all__ = ["project_replacement_writing_technique_usage"]
