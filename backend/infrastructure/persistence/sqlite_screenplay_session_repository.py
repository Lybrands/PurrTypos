"""Native screenplay Agent-session persistence."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from exceptions import AppError, NotFoundError


def _dump(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _object(value: object) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


class SqliteScreenplaySessionRepository:
    def __init__(self, db) -> None:
        self._db = db

    async def ensure_current(self, project_id: str) -> dict[str, Any]:
        project = await self._require_project(project_id)
        async with self._db.transaction():
            session = await self._db.fetch_one(
                "SELECT * FROM ai_sessions WHERE screenplay_project_id = ? "
                "AND scope = 'screenplay' AND closed = 0 "
                "ORDER BY id DESC LIMIT 1",
                [project_id],
            )
            if session is None:
                session = await self._insert(project)
        return dict(session)

    async def list(
        self,
        project_id: str,
        *,
        include_closed: bool,
    ) -> list[dict[str, Any]]:
        await self._require_project(project_id)
        where = "" if include_closed else "AND closed = 0 "
        return await self._db.fetch_all(
            "SELECT * FROM ai_sessions WHERE screenplay_project_id = ? "
            "AND scope = 'screenplay' "
            f"{where}ORDER BY id DESC",
            [project_id],
        )

    async def create(
        self,
        *,
        command_id: str,
        project_id: str,
    ) -> dict[str, Any]:
        normalized_command_id = str(command_id or "").strip()
        if not normalized_command_id:
            raise AppError("创建剧本会话必须提供 Idempotency-Key", 422)
        if len(normalized_command_id) > 200:
            raise AppError("Idempotency-Key 不能超过 200 个字符", 422)
        request_digest = hashlib.sha256(
            _dump({"projectId": project_id}).encode("utf-8")
        ).hexdigest()
        project = await self._require_project(project_id)
        async with self._db.transaction():
            receipt = await self._db.fetch_one(
                "SELECT command_type, request_digest, response_json "
                "FROM screenplay_command_receipts WHERE command_id = ?",
                [normalized_command_id],
            )
            if receipt is not None:
                if (
                    str(receipt.get("command_type") or "")
                    != "createAgentSession"
                    or str(receipt.get("request_digest") or "")
                    != request_digest
                ):
                    raise AppError(
                        "同一个 Idempotency-Key 不能用于不同的会话请求",
                        409,
                    )
                session_id = _object(receipt.get("response_json")).get(
                    "sessionId"
                )
                session = await self._db.fetch_one(
                    "SELECT * FROM ai_sessions WHERE id = ?",
                    [session_id],
                )
                if session is None:
                    raise AppError("会话命令回执指向的数据不存在", 409)
                return dict(session)

            session = await self._insert(project)
            session_id = int(session["id"])
            response = {"sessionId": session_id}
            await self._db.execute(
                "INSERT INTO screenplay_command_receipts "
                "(command_id, command_type, project_id, request_digest, "
                "result_ref, response_json) VALUES "
                "(?, 'createAgentSession', ?, ?, ?, ?)",
                [
                    normalized_command_id,
                    project_id,
                    request_digest,
                    f"ai-session://{session_id}",
                    _dump(response),
                ],
            )
            return dict(session)

    async def _require_project(self, project_id: str) -> dict[str, Any]:
        project = await self._db.fetch_one(
            "SELECT id, title, source_book_id FROM screenplay_projects "
            "WHERE id = ? AND source_snapshot_json IS NOT NULL",
            [str(project_id or "").strip()],
        )
        if project is None:
            raise NotFoundError("剧本项目不存在")
        return project

    async def _insert(self, project: dict[str, Any]) -> dict[str, Any]:
        session_id = await self._db.execute_and_get_id(
            "INSERT INTO ai_sessions "
            "(title, scope, screenplay_project_id, book_id, chapter_id) "
            "VALUES (?, 'screenplay', ?, ?, NULL)",
            [
                f"{str(project.get('title') or '剧本项目')} · Agent",
                str(project["id"]),
                project.get("source_book_id"),
            ],
        )
        session = await self._db.fetch_one(
            "SELECT * FROM ai_sessions WHERE id = ?",
            [session_id],
        )
        if session is None:
            raise RuntimeError("剧本 Agent 会话创建后无法读取")
        return session


__all__ = ["SqliteScreenplaySessionRepository"]
