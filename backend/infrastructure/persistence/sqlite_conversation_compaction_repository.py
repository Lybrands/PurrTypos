"""SQLite persistence for session-scoped conversation compaction."""

from __future__ import annotations

import json
from typing import Any, Mapping

from application.conversation_compaction_contracts import (
    ConversationSummary,
    ConversationTurn,
)


class SqliteConversationCompactionRepository:
    def __init__(self, db) -> None:
        self._db = db

    async def load_summary(
        self,
        session_id: str | int,
    ) -> ConversationSummary | None:
        normalized = _session_id(session_id)
        row = await self._db.fetch_one(
            "SELECT session_id, version, covered_through_conversation_id, "
            "covered_turn_count, source_digest, summary_json "
            "FROM ai_conversation_summaries WHERE session_id = ?",
            [normalized],
        )
        if row is None:
            return None
        try:
            payload = json.loads(str(row.get("summary_json") or "{}"))
            if not isinstance(payload, Mapping):
                return None
            return ConversationSummary(
                session_id=int(row["session_id"]),
                version=int(row["version"]),
                covered_through_conversation_id=int(
                    row["covered_through_conversation_id"]
                ),
                covered_turn_count=int(row["covered_turn_count"]),
                source_digest=str(row["source_digest"]),
                active_goal=_optional_text(payload.get("activeGoal")),
                targets=_mapping_rows(payload.get("targets")),
                decisions=_text_rows(payload.get("decisions")),
                constraints=_text_rows(payload.get("constraints")),
                unresolved_items=_text_rows(payload.get("unresolvedItems")),
                completed_actions=_text_rows(payload.get("completedActions")),
                summary=str(payload.get("summary") or ""),
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            return None

    async def list_turns(
        self,
        session_id: str | int,
        *,
        after_conversation_id: int = 0,
    ) -> tuple[ConversationTurn, ...]:
        normalized = _session_id(session_id)
        after_id = max(0, int(after_conversation_id))
        rows = await self._db.fetch_all(
            "SELECT id, prompt, response FROM ai_conversations "
            "WHERE session_id = ? AND id > ? ORDER BY id ASC",
            [normalized, after_id],
        )
        return tuple(ConversationTurn(
            id=int(row["id"]),
            prompt=str(row.get("prompt") or ""),
            response=str(row.get("response") or ""),
        ) for row in rows)

    async def save_summary(
        self,
        summary: ConversationSummary,
        *,
        model: str | None = None,
    ) -> None:
        session_id = _session_id(summary.session_id)
        payload = json.dumps(
            summary.to_mapping(include_persistence=False),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        await self._db.execute(
            "INSERT INTO ai_conversation_summaries "
            "(session_id, version, covered_through_conversation_id, "
            "covered_turn_count, source_digest, summary_json, model) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(session_id) DO UPDATE SET "
            "version = excluded.version, "
            "covered_through_conversation_id = "
            "excluded.covered_through_conversation_id, "
            "covered_turn_count = excluded.covered_turn_count, "
            "source_digest = excluded.source_digest, "
            "summary_json = excluded.summary_json, model = excluded.model, "
            "update_time = CURRENT_TIMESTAMP",
            [
                session_id,
                summary.version,
                summary.covered_through_conversation_id,
                summary.covered_turn_count,
                summary.source_digest,
                payload,
                str(model or "").strip() or None,
            ],
        )

    async def delete_summary(self, session_id: str | int) -> None:
        await self._db.execute(
            "DELETE FROM ai_conversation_summaries WHERE session_id = ?",
            [_session_id(session_id)],
        )


def _session_id(value: str | int) -> int:
    if isinstance(value, bool):
        raise ValueError("conversation session id must be numeric")
    try:
        normalized = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError("conversation session id must be numeric") from error
    if normalized <= 0:
        raise ValueError("conversation session id must be positive")
    return normalized


def _optional_text(value: Any) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def _text_rows(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())


def _mapping_rows(value: Any) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, list):
        return ()
    return tuple(dict(item) for item in value if isinstance(item, Mapping))


__all__ = ["SqliteConversationCompactionRepository"]
