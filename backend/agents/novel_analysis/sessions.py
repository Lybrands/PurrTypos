"""Product-owned conversation persistence for Novel Analysis."""

from __future__ import annotations

from uuid import uuid4

from exceptions import AppError


class NovelAnalysisSessions:
    """Manage revision-scoped conversations and immutable command bindings."""

    def __init__(self, db) -> None:
        self._db = db

    async def list(self, revision_id: str) -> list[dict]:
        if await self._db.fetch_one(
            "SELECT id FROM novel_source_revisions WHERE id = ?",
            [revision_id],
        ) is None:
            raise AppError("来源版本不存在", 404)
        await self._db.execute(
            "INSERT OR IGNORE INTO novel_analysis_sessions "
            "(id, revision_id, title) VALUES (?, ?, ?)",
            [f"legacy:{revision_id}", revision_id, "来源对话"],
        )
        return await self._db.fetch_all(
            "SELECT id, title, closed, created_at AS createdAt "
            "FROM novel_analysis_sessions WHERE revision_id = ? "
            "ORDER BY created_at DESC, rowid DESC",
            [revision_id],
        )

    async def create(self, revision_id: str) -> dict[str, str]:
        await self.list(revision_id)
        identity = uuid4().hex
        await self._db.execute(
            "INSERT INTO novel_analysis_sessions "
            "(id, revision_id, title) VALUES (?, ?, ?)",
            [identity, revision_id, "新对话"],
        )
        return {"id": identity, "title": "新对话"}

    async def update(
        self,
        revision_id: str,
        identity: str,
        title: str | None = None,
        closed: bool | None = None,
    ) -> None:
        await self.require(revision_id, identity)
        if title is not None:
            normalized = title.strip()
            if not normalized:
                raise AppError("对话名称不能为空", 422)
            await self._db.execute(
                "UPDATE novel_analysis_sessions SET title = ? WHERE id = ?",
                [normalized[:200], identity],
            )
        if closed is not None:
            await self._db.execute(
                "UPDATE novel_analysis_sessions SET closed = ? WHERE id = ?",
                [int(closed), identity],
            )

    async def require(self, revision_id: str, identity: str) -> dict:
        row = await self._db.fetch_one(
            "SELECT * FROM novel_analysis_sessions "
            "WHERE id = ? AND revision_id = ?",
            [identity, revision_id],
        )
        if row is None:
            raise AppError("对话不属于当前来源版本", 409)
        return row

    async def bind(
        self,
        revision_id: str,
        identity: str | None,
        command_id: str,
    ) -> None:
        async with self._db.transaction():
            await self.list(revision_id)
            session_id = identity or f"legacy:{revision_id}"
            row = await self.require(revision_id, session_id)
            if row["closed"]:
                raise AppError("请先重新打开该对话", 409)
            existing = await self._db.fetch_one(
                "SELECT session_id FROM novel_analysis_session_commands "
                "WHERE command_id = ?",
                [command_id],
            )
            if existing and existing["session_id"] != session_id:
                raise AppError("请求已绑定其他对话", 409)
            await self._db.execute(
                "INSERT OR IGNORE INTO novel_analysis_session_commands "
                "(command_id, session_id, revision_id) VALUES (?, ?, ?)",
                [command_id, session_id, revision_id],
            )


__all__ = ["NovelAnalysisSessions"]
