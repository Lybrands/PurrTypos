"""Writing-product overlays persisted beside authoritative Run conversations."""

from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass
from typing import Any

from application.writing_proposal_read_model import SqliteWritingProposalReadModel
from infrastructure.persistence.run_conversation_store import (
    ensure_terminal_run_conversation,
)

TERMINAL_RUN_STATUSES = frozenset({"done", "failed", "blocked", "canceled"})
ALIAS_FIELDS = ("clientTurnIds", "runIds", "conversationIds")


class BookSettingResolutionConflictError(ValueError):
    pass


@dataclass(frozen=True)
class BookSettingResolutionWrite:
    conversation_id: int
    replayed: bool


async def persist_setting_diff_resolution(
    db,
    *,
    session_id: int,
    run_id: str,
    resolution: dict[str, Any],
    allow_new_committed: bool = False,
) -> BookSettingResolutionWrite:
    """Persist one journal-owned resolution, joining any ambient transaction."""

    proposal_id = str(resolution.get("proposalId") or "").strip()
    status = str(resolution.get("status") or "").strip()
    if not proposal_id or status not in {"committed", "rejected"}:
        raise BookSettingResolutionConflictError("invalid setting proposal resolution")

    session = await db.fetch_one(
        "SELECT id, book_id, chapter_id FROM ai_sessions WHERE id = ?",
        [int(session_id)],
    )
    run = await db.fetch_one(
        "SELECT conversation_id, session_id, status, prompt, model_name "
        "FROM ai_agent_runs WHERE id = ?",
        [str(run_id)],
    )
    if session is None or run is None or int(run.get("session_id") or 0) != int(session_id):
        raise BookSettingResolutionConflictError("setting proposal Run ownership mismatch")

    projected = await SqliteWritingProposalReadModel(db).list_for_run(str(run_id))
    occurrence = next(
        (event for event in projected if event.get("proposalId") == proposal_id),
        None,
    )
    if occurrence is None:
        raise BookSettingResolutionConflictError("setting proposal is not journal-owned")
    proposal = occurrence.get("payload")
    if not isinstance(proposal, dict) or not _resolution_matches_proposal(
        resolution,
        proposal,
    ):
        raise BookSettingResolutionConflictError("setting proposal resolution identity mismatch")

    run_status = str(run.get("status") or "")
    if run_status != "running" and run_status not in TERMINAL_RUN_STATUSES:
        raise BookSettingResolutionConflictError("setting proposal Run is not resolvable")
    conversation_id = run.get("conversation_id")
    if conversation_id is None and run_status in TERMINAL_RUN_STATUSES:
        conversation_id = await ensure_terminal_run_conversation(db, str(run_id))
    if conversation_id is None:
        conversation_id = await db.execute_and_get_id(
            "INSERT INTO ai_conversations "
            "(session_id, chapter_id, prompt, response, model) "
            "VALUES (?, ?, ?, '', ?)",
            [
                int(session_id),
                session.get("chapter_id"),
                str(run.get("prompt") or ""),
                run.get("model_name"),
            ],
        )
        if conversation_id is None:
            raise RuntimeError("setting proposal conversation shell was not created")
        await db.execute(
            "UPDATE ai_agent_runs SET conversation_id = ?, "
            "update_time = CURRENT_TIMESTAMP WHERE id = ? "
            "AND conversation_id IS NULL",
            [int(conversation_id), str(run_id)],
        )
        bound = await db.fetch_one(
            "SELECT conversation_id FROM ai_agent_runs WHERE id = ?",
            [str(run_id)],
        )
        conversation_id = int((bound or {}).get("conversation_id") or conversation_id)

    existing = await db.fetch_one(
        "SELECT agent_process FROM ai_conversations WHERE id = ? AND session_id = ?",
        [int(conversation_id), int(session_id)],
    )
    if existing is None:
        raise BookSettingResolutionConflictError("setting proposal conversation ownership mismatch")
    current_process = _json_object(existing.get("agent_process"))
    existing_resolution = _existing_resolution(current_process, proposal_id)
    normalized = _normalized_resolution(resolution)
    if existing_resolution is not None:
        if _normalized_resolution(existing_resolution) != normalized:
            raise BookSettingResolutionConflictError(
                "setting proposal has already been resolved differently"
            )
        return BookSettingResolutionWrite(
            conversation_id=int(conversation_id),
            replayed=True,
        )
    if status == "committed" and not allow_new_committed:
        raise BookSettingResolutionConflictError(
            "committed setting proposal must be recorded by the setting transaction"
        )
    merged = merge_product_agent_process(
        current_process,
        {"settingDiff": {"version": 1, "resolutions": {proposal_id: resolution}}},
        run_id=str(run_id),
        conversation_id=int(conversation_id),
    )
    await db.execute(
        "UPDATE ai_conversations SET agent_process = ? WHERE id = ? AND session_id = ?",
        [
            json.dumps(merged, ensure_ascii=False),
            int(conversation_id),
            int(session_id),
        ],
    )
    return BookSettingResolutionWrite(
        conversation_id=int(conversation_id),
        replayed=False,
    )


async def validate_setting_diff_mutation(
    db,
    *,
    run_id: str,
    resolution: dict[str, Any],
    request_before: dict[str, Any],
    request_proposed: dict[str, Any],
    current: dict[str, Any],
    final: dict[str, Any],
) -> None:
    await setting_diff_mutation_digest(
        db,
        run_id=run_id,
        resolution=resolution,
        request_before=request_before,
        request_proposed=request_proposed,
        final=final,
    )
    if current != request_before:
        raise BookSettingResolutionConflictError(
            "setting proposal target changed after proposal"
        )


async def setting_diff_mutation_digest(
    db,
    *,
    run_id: str,
    resolution: dict[str, Any],
    request_before: dict[str, Any],
    request_proposed: dict[str, Any],
    final: dict[str, Any],
) -> str:
    proposal_id = str(resolution.get("proposalId") or "").strip()
    projected = await SqliteWritingProposalReadModel(db).list_for_run(str(run_id))
    occurrence = next(
        (event for event in projected if event.get("proposalId") == proposal_id),
        None,
    )
    proposal = occurrence.get("payload") if occurrence is not None else None
    if not isinstance(proposal, dict):
        raise BookSettingResolutionConflictError(
            "setting proposal is not journal-owned"
        )
    journal_before = proposal.get("before")
    journal_proposed = proposal.get("proposed")
    if request_before != journal_before or request_proposed != journal_proposed:
        raise BookSettingResolutionConflictError(
            "setting proposal snapshots changed from journal"
        )
    if not _legal_setting_mutation(journal_before, journal_proposed, final):
        raise BookSettingResolutionConflictError(
            "setting proposal final mutation is not a reviewed composition"
        )
    payload = {
        "kind": str(resolution.get("kind") or ""),
        "sessionKey": str(resolution.get("sessionKey") or ""),
        "before": journal_before,
        "proposed": journal_proposed,
        "final": final,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _legal_setting_mutation(
    before: object,
    proposed: object,
    final: dict[str, Any],
) -> bool:
    if not isinstance(before, dict) or not isinstance(proposed, dict):
        return False
    if set(before) != set(proposed) or set(final) != set(before):
        return False
    for field, before_value in before.items():
        proposed_value = proposed[field]
        final_value = final[field]
        if not all(isinstance(value, str) for value in (
            before_value,
            proposed_value,
            final_value,
        )):
            return False
        if field in {"profileMd", "content"}:
            if not _legal_text_composition(before_value, proposed_value, final_value):
                return False
        elif final_value not in {before_value, proposed_value}:
            return False
    return True


def _legal_text_composition(before: str, proposed: str, final: str) -> bool:
    before_lines = before.split("\n") if before else []
    proposed_lines = proposed.split("\n") if proposed else []
    final_lines = final.split("\n") if final else []
    raw = _paragraph_diff(before_lines, proposed_lines)
    choices_by_op: list[list[list[str]]] = []
    deletes: list[str] = []
    inserts: list[str] = []

    def flush() -> None:
        paired = min(len(deletes), len(inserts))
        for index in range(paired):
            choices_by_op.append([
                [deletes[index]] if deletes[index] else [],
                [inserts[index]] if inserts[index] else [],
            ])
        for value in deletes[paired:]:
            choices_by_op.append([[value] if value else [], []])
        for value in inserts[paired:]:
            choices_by_op.append([[], [value] if value else []])
        deletes.clear()
        inserts.clear()

    for tag, value in raw:
        if tag == "delete":
            deletes.append(value)
        elif tag == "insert":
            inserts.append(value)
        else:
            flush()
            choices_by_op.append([[value] if value else []])
    flush()

    reachable = {0}
    for choices in choices_by_op:
        next_reachable: set[int] = set()
        for position in reachable:
            for choice in choices:
                end = position + len(choice)
                if final_lines[position:end] == choice:
                    next_reachable.add(end)
        reachable = next_reachable
        if not reachable:
            return False
    return len(final_lines) in reachable


def _paragraph_diff(before: list[str], proposed: list[str]):
    rows = len(before)
    columns = len(proposed)
    lengths = [[0] * (columns + 1) for _ in range(rows + 1)]
    for row in range(1, rows + 1):
        for column in range(1, columns + 1):
            if before[row - 1] == proposed[column - 1]:
                lengths[row][column] = lengths[row - 1][column - 1] + 1
            else:
                lengths[row][column] = max(
                    lengths[row - 1][column],
                    lengths[row][column - 1],
                )
    raw: list[tuple[str, str]] = []
    row, column = rows, columns
    while row > 0 or column > 0:
        if (
            row > 0
            and column > 0
            and before[row - 1] == proposed[column - 1]
        ):
            raw.append(("equal", before[row - 1]))
            row -= 1
            column -= 1
        elif column > 0 and (
            row == 0
            or lengths[row][column - 1] >= lengths[row - 1][column]
        ):
            raw.append(("insert", proposed[column - 1]))
            column -= 1
        else:
            raw.append(("delete", before[row - 1]))
            row -= 1
    raw.reverse()
    return raw


def _existing_resolution(
    process: dict[str, Any],
    proposal_id: str,
) -> dict[str, Any] | None:
    setting = process.get("settingDiff")
    resolutions = setting.get("resolutions") if isinstance(setting, dict) else None
    value = resolutions.get(proposal_id) if isinstance(resolutions, dict) else None
    return value if isinstance(value, dict) else None


def _normalized_resolution(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "proposalId": str(value.get("proposalId") or ""),
        "sessionKey": str(value.get("sessionKey") or ""),
        "kind": str(value.get("kind") or ""),
        "title": str(value.get("title") or ""),
        "status": str(value.get("status") or ""),
        "acceptedSegments": int(value.get("acceptedSegments") or 0),
        "rejectedSegments": int(value.get("rejectedSegments") or 0),
        "mutationDigest": str(value.get("mutationDigest") or ""),
    }


def _resolution_matches_proposal(
    resolution: dict[str, Any],
    proposal: dict[str, Any],
) -> bool:
    kind = str(proposal.get("kind") or "")
    if resolution.get("kind") != kind:
        return False
    if kind == "character":
        expected_key = f"character:{proposal.get('characterId')}"
    elif kind == "entity":
        expected_key = f"entity:{proposal.get('entityId')}"
    else:
        expected_key = f"background:{proposal.get('bookId')}"
    return resolution.get("sessionKey") == expected_key


def merge_product_agent_process(
    current: dict[str, Any],
    incoming_value: object,
    *,
    run_id: str,
    conversation_id: int,
) -> dict[str, Any]:
    incoming = incoming_value if isinstance(incoming_value, dict) else {}
    merged = {**current}
    current_setting = current.get("settingDiff")
    incoming_setting = incoming.get("settingDiff")
    setting = {**current_setting} if isinstance(current_setting, dict) else {}
    if isinstance(incoming_setting, dict) or setting:
        setting["version"] = 1
        current_aliases = setting.get("aliases")
        aliases = {**current_aliases} if isinstance(current_aliases, dict) else {}
        incoming_aliases = (
            incoming_setting.get("aliases")
            if isinstance(incoming_setting, dict)
            else None
        )
        if isinstance(incoming_aliases, dict):
            for proposal_id, value in incoming_aliases.items():
                if not isinstance(value, dict):
                    continue
                existing = aliases.get(proposal_id)
                alias = existing if isinstance(existing, dict) else {}
                aliases[proposal_id] = {
                    field: _ordered_union(alias.get(field), value.get(field))
                    for field in ALIAS_FIELDS
                }
        resolutions = setting.get("resolutions")
        resolutions = {**resolutions} if isinstance(resolutions, dict) else {}
        incoming_resolutions = (
            incoming_setting.get("resolutions")
            if isinstance(incoming_setting, dict)
            else None
        )
        if isinstance(incoming_resolutions, dict):
            for proposal_id, resolution in incoming_resolutions.items():
                if proposal_id in resolutions or not isinstance(resolution, dict):
                    continue
                if resolution.get("status") not in {"committed", "rejected"}:
                    continue
                resolutions[proposal_id] = {**resolution, "proposalId": proposal_id}
        for proposal_id in set(aliases) | set(resolutions):
            alias = aliases.get(proposal_id)
            alias = alias if isinstance(alias, dict) else {}
            aliases[proposal_id] = {
                "clientTurnIds": _ordered_union(alias.get("clientTurnIds")),
                "runIds": _ordered_union(alias.get("runIds"), [run_id]),
                "conversationIds": _ordered_union(
                    alias.get("conversationIds"),
                    [conversation_id],
                ),
            }
        if aliases:
            setting["aliases"] = aliases
        if resolutions:
            setting["resolutions"] = resolutions
        merged["settingDiff"] = setting
    return merged


def _ordered_union(*values: object) -> list[Any]:
    result: list[Any] = []
    for value in values:
        if not isinstance(value, list):
            continue
        for item in value:
            if item not in result:
                result.append(item)
    return result


def _json_object(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


__all__ = [
    "BookSettingResolutionConflictError",
    "BookSettingResolutionWrite",
    "merge_product_agent_process",
    "persist_setting_diff_resolution",
    "setting_diff_mutation_digest",
    "validate_setting_diff_mutation",
]
