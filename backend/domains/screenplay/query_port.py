"""Read-model port consumed by screenplay domain policies."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ScreenplayQueryPort(Protocol):
    async def get_project(self, project_id: str) -> Mapping[str, Any] | None: ...

    async def get_document(
        self,
        project_id: str,
        document_id: str,
    ) -> Mapping[str, Any] | None: ...

    async def list_current_documents(
        self,
        project_id: str,
    ) -> Sequence[Mapping[str, Any]]: ...

    async def list_episode_rows(
        self,
        document_id: str,
        *,
        include_content: bool,
    ) -> Sequence[Mapping[str, Any]]: ...

    async def has_open_session(
        self,
        session_id: int | str,
        project_id: str,
    ) -> bool: ...


__all__ = ["ScreenplayQueryPort"]
