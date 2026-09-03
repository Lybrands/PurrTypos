"""Read material receipts from the latest actual model input."""

import json


async def provided_read_materials(db, run_id):
    row = await db.fetch_one(
        "SELECT payload_json FROM ai_agent_run_events WHERE run_id = ? "
        "AND kind = 'stream.opened' AND source = 'provider' AND visibility = 'private' "
        "ORDER BY id DESC LIMIT 1", [run_id],
    )
    if row is None:
        return ()
    payload = json.loads(row["payload_json"])
    return tuple(
        receipt["metadata"] for receipt in payload.get("contextEvidence", [])
        if receipt.get("source") == "purrtypos.prepared_read"
        and isinstance(receipt.get("metadata"), dict)
        and receipt["metadata"].get("complete") is True
    )
