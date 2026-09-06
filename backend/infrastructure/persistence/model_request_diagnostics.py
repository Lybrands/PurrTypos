"""Persist only normalized model configuration at the actual SDK boundary."""

import json
import time


def model_request_observer(db, *, owner="agent"):
    async def record(value):
        value = {**value, "owner": owner}
        attempt_id = value["attemptId"]
        row = await db.fetch_one(
            "SELECT run_id FROM ai_agent_run_events WHERE event_type = 'stream.opened' "
            "AND json_extract(payload_json, '$.callParameters[0].sdkAttemptId') = ? ORDER BY id DESC LIMIT 1",
            [attempt_id],
        )
        await db.execute(
            "INSERT INTO ai_model_sdk_requests(attempt_id, run_id, record_json, create_time) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(attempt_id) DO UPDATE SET record_json = excluded.record_json "
            "WHERE json_extract(ai_model_sdk_requests.record_json, '$.requestDigest') = json_extract(excluded.record_json, '$.requestDigest')",
            [attempt_id, row["run_id"] if row else None, json.dumps(value, ensure_ascii=False), int(time.time() * 1000)],
        )
    return record
