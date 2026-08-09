"""SQLite implementation of the screenplay Agent read-model port."""

from __future__ import annotations

from database.crud.screenplay_episode_documents import (
    list_episode_rows as list_structured_episode_rows,
)
from database.crud.screenplay_head_projection import (
    get_document as get_project_document,
    list_current_documents,
)


class SqliteScreenplayAgentQuery:
    def __init__(self, db) -> None:
        self._db = db

    async def get_project(self, project_id: str):
        return await self._db.fetch_one(
            "SELECT * FROM screenplay_projects WHERE id = ?",
            [str(project_id or "").strip()],
        )

    async def get_document(self, project_id: str, document_id: str):
        return await get_project_document(
            self._db,
            str(project_id or "").strip(),
            str(document_id or "").strip(),
        )

    async def list_current_documents(self, project_id: str):
        return await list_current_documents(
            self._db,
            str(project_id or "").strip(),
        )

    async def list_episode_rows(
        self,
        document_id: str,
        *,
        include_content: bool,
    ):
        return await list_structured_episode_rows(
            self._db,
            document_id=str(document_id or "").strip(),
            include_content=bool(include_content),
        )

    async def has_open_session(
        self,
        session_id: int | str,
        project_id: str,
    ) -> bool:
        session = await self._db.fetch_one(
            "SELECT id FROM ai_sessions WHERE id = ? "
            "AND screenplay_project_id = ? AND scope = 'screenplay' "
            "AND closed = 0",
            [session_id, str(project_id or "").strip()],
        )
        return session is not None


__all__ = ["SqliteScreenplayAgentQuery"]
