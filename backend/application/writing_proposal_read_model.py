"""Product read model for durable writing proposal occurrences."""

from __future__ import annotations

import json
import base64
from collections.abc import Iterable
from typing import Any


SETTING_DIFF_EVENT_TYPE = "writing.proposed_setting_diff"
TOOL_COMPLETED_EVENT_TYPE = "tool.call_completed"
SETTING_PROPOSAL_TOOLS = frozenset({
    "updateCharacter",
    "updateSettingEntity",
    "editStoryBackground",
})


class SqliteWritingProposalReadModel:
    """Whitelist proposal effects without changing the public Core output contract."""

    def __init__(self, db) -> None:
        self._db = db

    async def list_for_run(self, run_id: str) -> list[dict[str, Any]]:
        normalized_run_id = str(run_id or "").strip()
        if not normalized_run_id:
            return []
        owner = await self._db.fetch_one(
            "SELECT s.book_id FROM ai_agent_runs AS r "
            "JOIN ai_sessions AS s ON s.id = r.session_id "
            "WHERE r.id = ?",
            [normalized_run_id],
        )
        owner_book_id = str((owner or {}).get("book_id") or "").strip()
        if not owner_book_id:
            return []
        receipt_rows = await self._db.fetch_all(
            "SELECT tool_call_id, tool_name, effects_json "
            "FROM ai_agent_tool_receipts "
            "WHERE run_id = ? ORDER BY create_time, tool_call_id",
            [normalized_run_id],
        )
        receipts: dict[str, dict[str, Any]] = {}
        for receipt in receipt_rows:
            tool_call_id = str(receipt.get("tool_call_id") or "").strip()
            tool_name = str(receipt.get("tool_name") or "").strip()
            if not tool_call_id or tool_name not in SETTING_PROPOSAL_TOOLS:
                continue
            receipts[tool_call_id] = {
                "toolName": tool_name,
                "effects": _json_list(receipt.get("effects_json")),
            }
        rows = await self._db.fetch_all(
            "SELECT id, event_type, payload_json, create_time "
            "FROM ai_agent_run_events WHERE run_id = ? "
            "AND event_type IN (?, ?) ORDER BY id",
            [
                normalized_run_id,
                SETTING_DIFF_EVENT_TYPE,
                TOOL_COMPLETED_EVENT_TYPE,
            ],
        )
        projected: list[dict[str, Any]] = []
        seen: set[str] = set()
        pending: list[dict[str, Any]] = []
        for row in rows:
            if row.get("event_type") == SETTING_DIFF_EVENT_TYPE:
                payload = _json_object(row.get("payload_json"))
                if payload:
                    pending.append({
                        "payload": payload,
                        "createdAt": row.get("create_time"),
                    })
                continue
            completion = _json_object(row.get("payload_json"))
            tool_call_id = str(completion.get("toolCallId") or "").strip()
            receipt = receipts.get(tool_call_id)
            if receipt is None:
                continue
            if completion.get("toolName") != receipt["toolName"]:
                continue
            candidates = list(pending)
            for effect_index, effect in enumerate(receipt["effects"]):
                if effect.get("type") != SETTING_DIFF_EVENT_TYPE:
                    continue
                payload = effect.get("payload")
                if (
                    not isinstance(payload, dict)
                    or not _valid_proposal(payload, owner_book_id)
                ):
                    continue
                match_index = next((
                    index
                    for index, item in enumerate(candidates)
                    if item["payload"] == payload
                ), None)
                if match_index is None:
                    continue
                journal = candidates.pop(match_index)
                proposal_id = proposal_occurrence_id(
                    normalized_run_id,
                    tool_call_id,
                    effect_index,
                )
                if proposal_id in seen:
                    continue
                seen.add(proposal_id)
                projected.append(_event(
                    run_id=normalized_run_id,
                    proposal_id=proposal_id,
                    payload=payload,
                    tool_call_id=tool_call_id,
                    effect_index=effect_index,
                    created_at=journal.get("createdAt"),
                ))
            # A different tool can emit its journal proposal before this
            # completion. Keep unmatched occurrences for the later matching
            # receipt instead of discarding the entire pending window.
            pending = candidates
        return projected


def proposal_occurrence_id(
    run_id: str,
    tool_call_id: str,
    effect_index: int,
) -> str:
    encoded = base64.urlsafe_b64encode(json.dumps(
        [str(run_id), str(tool_call_id), int(effect_index)],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")).decode("ascii").rstrip("=")
    return f"setting-proposal:v1:{encoded}"


def unseen_product_chunks(
    events: Iterable[dict[str, Any]],
    seen_proposal_ids: set[str],
) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    for event in events:
        proposal_id = str(event.get("proposalId") or "").strip()
        chunk = event.get("chunk")
        if not proposal_id or proposal_id in seen_proposal_ids:
            continue
        seen_proposal_ids.add(proposal_id)
        if isinstance(chunk, dict):
            chunks.append(chunk)
    return chunks


def _event(
    *,
    run_id: str,
    proposal_id: str,
    payload: dict[str, Any],
    effect_index: int,
    tool_call_id: str | None = None,
    created_at: object = None,
) -> dict[str, Any]:
    proposal = {**payload, "proposalId": proposal_id}
    result: dict[str, Any] = {
        "version": 1,
        "type": SETTING_DIFF_EVENT_TYPE,
        "runId": run_id,
        "proposalId": proposal_id,
        "effectIndex": int(effect_index),
        "payload": proposal,
        "chunk": {
            "runId": run_id,
            "proposedSettingDiff": proposal,
        },
    }
    if tool_call_id is not None:
        result["toolCallId"] = tool_call_id
    if created_at is not None:
        result["createdAt"] = str(created_at)
    return result


def _valid_proposal(payload: dict[str, Any], owner_book_id: str) -> bool:
    kind = payload.get("kind")
    if kind not in {"character", "background", "entity"}:
        return False
    if str(payload.get("bookId") or "") != owner_book_id:
        return False
    if not isinstance(payload.get("before"), dict):
        return False
    if not isinstance(payload.get("proposed"), dict):
        return False
    if kind == "character" and payload.get("characterId") is None:
        return False
    if kind == "entity" and payload.get("entityId") is None:
        return False
    return True


def _json_object(value: object) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _json_list(value: object) -> list[dict[str, Any]]:
    try:
        parsed = json.loads(str(value or "[]"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, dict)]
