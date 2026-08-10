"""Application boundary for screenplay project API v2."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from domains.screenplay.project_aggregate import legacy_format
from domains.screenplay.source_scope import parse_source_scope, resolve_source_scope
from exceptions import AppError, NotFoundError
from infrastructure.persistence.sqlite_screenplay_v2_repository import (
    SqliteScreenplayV2Repository,
)
from infrastructure.persistence.sqlite_screenplay_session_repository import (
    SqliteScreenplaySessionRepository,
)
from schemas.screenplay_v2 import (
    AdjudicateScreenplayV2ReviewRequest,
    AcceptScreenplayV2RevisionRequest,
    ChangeScreenplayV2ProjectLifecycleRequest,
    CreateScreenplayV2ProjectRequest,
    CreateScreenplayV2WorkingCopyFromRevisionRequest,
    DeleteScreenplayV2ProjectRequest,
    FinalizeScreenplayV2ProjectRequest,
    PublishScreenplayV2WorkingCopyRequest,
    ScreenplayV2BookSourceRequest,
    UpdateScreenplayV2ProjectRequest,
    UpdateScreenplayV2WorkingCopyRequest,
)


_SCOPE_MODE_TO_LEGACY = {
    "wholeBook": "whole_book",
    "firstChapters": "first_chapters",
    "firstVolumes": "first_volumes",
    "selectedChapters": "selected_chapters",
    "selectedVolumes": "selected_volumes",
}
_SCOPE_MODE_TO_PUBLIC = {
    legacy: public for public, legacy in _SCOPE_MODE_TO_LEGACY.items()
}


class ScreenplayV2ProjectService:
    def __init__(self, db) -> None:
        self._db = db
        self._repository = SqliteScreenplayV2Repository(db)
        self._sessions = SqliteScreenplaySessionRepository(db)

    async def list_projects(
        self,
        *,
        include_archived: bool,
    ) -> list[dict[str, Any]]:
        return await self._repository.list_projects(
            include_archived=include_archived,
        )

    async def ensure_current_session(self, project_id: str) -> dict[str, Any]:
        return await self._sessions.ensure_current(project_id)

    async def list_sessions(
        self,
        project_id: str,
        *,
        include_closed: bool,
    ) -> list[dict[str, Any]]:
        return await self._sessions.list(
            project_id,
            include_closed=include_closed,
        )

    async def create_session(
        self,
        *,
        command_id: str,
        project_id: str,
    ) -> dict[str, Any]:
        return await self._sessions.create(
            command_id=command_id,
            project_id=project_id,
        )

    async def create_project(
        self,
        *,
        command_id: str,
        request: CreateScreenplayV2ProjectRequest,
    ) -> dict[str, Any]:
        normalized_command_id = str(command_id or "").strip()
        if not normalized_command_id:
            raise AppError("创建剧本项目必须提供 Idempotency-Key", 422)
        if len(normalized_command_id) > 200:
            raise AppError("Idempotency-Key 不能超过 200 个字符", 422)

        request_digest = _digest(request.model_dump(mode="json"))
        existing_project_id = (
            await self._repository.find_create_project_receipt(
                command_id=normalized_command_id,
                request_digest=request_digest,
            )
        )
        if existing_project_id is not None:
            return await self._repository.get_workspace(existing_project_id)

        source_kind = request.source.type
        source_book_id: str | None = None
        if isinstance(request.source, ScreenplayV2BookSourceRequest):
            source_book_id = request.source.bookId
            book = await self._db.fetch_one(
                "SELECT id, title FROM books WHERE id = ?",
                [source_book_id],
            )
            if book is None:
                raise NotFoundError("来源书籍不存在")
            raw_scope = request.source.scope.model_dump()
            raw_scope["mode"] = _SCOPE_MODE_TO_LEGACY[request.source.scope.mode]
            source_scope = await resolve_source_scope(
                self._db,
                source_book_id,
                raw_scope,
            )
            source_snapshot: dict[str, Any] = {
                "type": "book",
                "bookId": source_book_id,
                "bookTitle": str(book.get("title") or ""),
                "scope": _public_source_scope(source_scope),
            }
        else:
            source_scope = parse_source_scope(None)
            source_snapshot = {"type": "original"}

        project_id = await self._repository.create_project(
            command_id=normalized_command_id,
            request_digest=request_digest,
            title=request.title,
            source_kind=source_kind,
            source_book_id=source_book_id,
            source_scope=source_scope,
            source_snapshot=source_snapshot,
            screenplay_format=legacy_format(request.format),
            approach=request.brief.approach,
            premise=request.brief.premise,
        )
        return await self._repository.get_workspace(project_id)

    async def get_workspace(self, project_id: str) -> dict[str, Any]:
        normalized_project_id = str(project_id or "").strip()
        if not normalized_project_id:
            raise NotFoundError("剧本项目不存在")
        return await self._repository.get_workspace(normalized_project_id)

    async def update_project(
        self,
        *,
        command_id: str,
        project_id: str,
        request: UpdateScreenplayV2ProjectRequest,
    ) -> dict[str, Any]:
        normalized_command_id = _command_id(command_id)
        normalized_project_id = str(project_id or "").strip()
        if not normalized_project_id:
            raise NotFoundError("剧本项目不存在")
        request_digest = _digest({
            "projectId": normalized_project_id,
            **request.model_dump(mode="json"),
        })
        await self._repository.update_project(
            command_id=normalized_command_id,
            request_digest=request_digest,
            project_id=normalized_project_id,
            expected_project_revision=request.expectedProjectRevision,
            title=request.title,
        )
        return await self._repository.get_workspace(normalized_project_id)

    async def set_project_lifecycle(
        self,
        *,
        command_id: str,
        project_id: str,
        lifecycle: str,
        request: ChangeScreenplayV2ProjectLifecycleRequest,
    ) -> dict[str, Any]:
        normalized_command_id = _command_id(command_id)
        normalized_project_id = str(project_id or "").strip()
        if not normalized_project_id:
            raise NotFoundError("剧本项目不存在")
        request_digest = _digest({
            "projectId": normalized_project_id,
            "lifecycle": lifecycle,
            **request.model_dump(mode="json"),
        })
        await self._repository.set_project_lifecycle(
            command_id=normalized_command_id,
            request_digest=request_digest,
            project_id=normalized_project_id,
            expected_project_revision=request.expectedProjectRevision,
            lifecycle=lifecycle,
        )
        return await self._repository.get_workspace(normalized_project_id)

    async def delete_project(
        self,
        *,
        command_id: str,
        project_id: str,
        request: DeleteScreenplayV2ProjectRequest,
    ) -> dict[str, Any]:
        normalized_command_id = _command_id(command_id)
        normalized_project_id = str(project_id or "").strip()
        if not normalized_project_id:
            raise NotFoundError("剧本项目不存在")
        request_digest = _digest({
            "projectId": normalized_project_id,
            **request.model_dump(mode="json"),
        })
        return await self._repository.delete_project(
            command_id=normalized_command_id,
            request_digest=request_digest,
            project_id=normalized_project_id,
            expected_project_revision=request.expectedProjectRevision,
        )

    async def get_revision(
        self,
        revision_id: str,
        *,
        view: str,
    ) -> dict[str, Any]:
        normalized_revision_id = str(revision_id or "").strip()
        if not normalized_revision_id:
            raise NotFoundError("剧本版本不存在")
        if view not in {"summary", "full"}:
            raise AppError("Revision view 只能是 summary 或 full", 422)
        return await self._repository.get_revision(
            normalized_revision_id,
            include_content=view == "full",
        )

    async def list_revision_history(
        self,
        *,
        project_id: str,
        role: str,
        cursor: str | None,
        limit: int,
    ) -> dict[str, Any]:
        normalized_project_id = str(project_id or "").strip()
        normalized_role = str(role or "").strip()
        if not normalized_project_id or not normalized_role:
            raise NotFoundError("剧本交付物不存在")
        before_revision_no: int | None = None
        if cursor is not None and str(cursor).strip():
            try:
                before_revision_no = int(str(cursor).strip())
            except ValueError as error:
                raise AppError("Revision cursor 无效", 422) from error
            if before_revision_no <= 0:
                raise AppError("Revision cursor 无效", 422)
        return await self._repository.list_revision_history(
            project_id=normalized_project_id,
            role=normalized_role,
            before_revision_no=before_revision_no,
            limit=max(1, min(100, int(limit))),
        )

    async def get_latest_review_for_draft(
        self,
        *,
        project_id: str,
        draft_revision_id: str,
    ) -> dict[str, Any] | None:
        normalized_project_id = str(project_id or "").strip()
        normalized_draft_id = str(draft_revision_id or "").strip()
        if not normalized_project_id or not normalized_draft_id:
            raise NotFoundError("剧本版本不存在")
        return await self._repository.get_latest_review_for_draft(
            project_id=normalized_project_id,
            draft_revision_id=normalized_draft_id,
        )

    async def update_working_copy(
        self,
        *,
        working_copy_id: str,
        request: UpdateScreenplayV2WorkingCopyRequest,
    ) -> dict[str, Any]:
        normalized_id = str(working_copy_id or "").strip()
        if not normalized_id:
            raise NotFoundError("剧本 Working Copy 不存在")
        encoded = json.dumps(
            request.content,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        if len(encoded) > 2_000_000:
            raise AppError("Working Copy 内容不能超过 2,000,000 个字符", 422)
        return await self._repository.update_working_copy(
            working_copy_id=normalized_id,
            expected_revision=request.expectedRevision,
            content=request.content,
        )

    async def create_working_copy_from_revision(
        self,
        *,
        command_id: str,
        project_id: str,
        revision_id: str,
        request: CreateScreenplayV2WorkingCopyFromRevisionRequest,
    ) -> dict[str, Any]:
        normalized_command_id = _command_id(command_id)
        normalized_project_id = str(project_id or "").strip()
        normalized_revision_id = str(revision_id or "").strip()
        if not normalized_project_id:
            raise NotFoundError("剧本项目不存在")
        if not normalized_revision_id:
            raise NotFoundError("剧本版本不存在")
        return await self._repository.create_working_copy_from_revision(
            command_id=normalized_command_id,
            request_digest=_digest({
                "projectId": normalized_project_id,
                "revisionId": normalized_revision_id,
                **request.model_dump(mode="json"),
            }),
            project_id=normalized_project_id,
            revision_id=normalized_revision_id,
            expected_project_revision=request.expectedProjectRevision,
            expected_working_copy_revision=request.expectedWorkingCopyRevision,
        )

    async def publish_working_copy(
        self,
        *,
        command_id: str,
        working_copy_id: str,
        request: PublishScreenplayV2WorkingCopyRequest,
    ) -> dict[str, Any]:
        normalized_command_id = _command_id(command_id)
        normalized_copy_id = str(working_copy_id or "").strip()
        if not normalized_copy_id:
            raise NotFoundError("剧本 Working Copy 不存在")
        request_digest = _digest({
            "workingCopyId": normalized_copy_id,
            **request.model_dump(mode="json"),
        })
        project_id, revision_id = await self._repository.publish_working_copy(
            command_id=normalized_command_id,
            request_digest=request_digest,
            working_copy_id=normalized_copy_id,
            expected_project_revision=request.expectedProjectRevision,
            expected_working_copy_revision=request.expectedWorkingCopyRevision,
        )
        workspace = await self._repository.get_workspace(project_id)
        candidate = next(
            (
                item for item in workspace["candidates"]
                if item["id"] == revision_id
            ),
            None,
        )
        return {
            "revision": candidate,
            "workspace": workspace,
        }

    async def accept_revision(
        self,
        *,
        command_id: str,
        project_id: str,
        revision_id: str,
        request: AcceptScreenplayV2RevisionRequest,
    ) -> dict[str, Any]:
        normalized_command_id = _command_id(command_id)
        normalized_project_id = str(project_id or "").strip()
        normalized_revision_id = str(revision_id or "").strip()
        if not normalized_project_id:
            raise NotFoundError("剧本项目不存在")
        if not normalized_revision_id:
            raise NotFoundError("剧本版本不存在")
        request_digest = _digest({
            "projectId": normalized_project_id,
            "revisionId": normalized_revision_id,
            **request.model_dump(mode="json"),
        })
        result = await self._repository.accept_revision(
            command_id=normalized_command_id,
            request_digest=request_digest,
            project_id=normalized_project_id,
            revision_id=normalized_revision_id,
            expected_project_revision=request.expectedProjectRevision,
            confirm_invalidation=request.confirmInvalidation,
        )
        return {
            **result,
            "workspace": await self._repository.get_workspace(
                normalized_project_id
            ),
        }

    async def adjudicate_review(
        self,
        *,
        command_id: str,
        project_id: str,
        request: AdjudicateScreenplayV2ReviewRequest,
    ) -> dict[str, Any]:
        normalized_command_id = _command_id(command_id)
        normalized_project_id = str(project_id or "").strip()
        if not normalized_project_id:
            raise NotFoundError("剧本项目不存在")
        request_digest = _digest({
            "projectId": normalized_project_id,
            **request.model_dump(mode="json"),
        })
        await self._repository.adjudicate_review(
            command_id=normalized_command_id,
            request_digest=request_digest,
            project_id=normalized_project_id,
            expected_project_revision=request.expectedProjectRevision,
            review_revision_id=request.reviewRevisionId,
            decisions=[
                decision.model_dump(mode="json")
                for decision in request.decisions
            ],
            actor="user",
        )
        return await self._repository.get_workspace(normalized_project_id)

    async def finalize_project(
        self,
        *,
        command_id: str,
        project_id: str,
        request: FinalizeScreenplayV2ProjectRequest,
    ) -> dict[str, Any]:
        normalized_command_id = _command_id(command_id)
        normalized_project_id = str(project_id or "").strip()
        if not normalized_project_id:
            raise NotFoundError("剧本项目不存在")
        request_digest = _digest({
            "projectId": normalized_project_id,
            **request.model_dump(mode="json"),
        })
        await self._repository.finalize_project(
            command_id=normalized_command_id,
            request_digest=request_digest,
            project_id=normalized_project_id,
            expected_project_revision=request.expectedProjectRevision,
            draft_revision_id=request.draftRevisionId,
            review_revision_id=request.reviewRevisionId,
            actor="user",
        )
        return await self._repository.get_workspace(normalized_project_id)


def _digest(value: Mapping[str, Any]) -> str:
    payload = json.dumps(
        dict(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _public_source_scope(value: Mapping[str, Any]) -> dict[str, Any]:
    scope = dict(value)
    scope["mode"] = _SCOPE_MODE_TO_PUBLIC.get(
        str(scope.get("mode") or "whole_book"),
        "wholeBook",
    )
    return scope


def _command_id(value: object) -> str:
    command_id = str(value or "").strip()
    if not command_id:
        raise AppError("剧本写入必须提供 Idempotency-Key", 422)
    if len(command_id) > 200:
        raise AppError("Idempotency-Key 不能超过 200 个字符", 422)
    return command_id


__all__ = ["ScreenplayV2ProjectService"]
