"""Application service for writing method commands and queries."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from domains.writing.methods import MethodDraft, SchemeDraft, WritingMethodConflictError
from domains.writing.method_resolution import build_writing_method_binding_snapshot
from infrastructure.persistence.writing.sqlite_writing_method_repository import (
    SqliteWritingMethodRepository,
)

MAX_METHOD_MARKDOWN_CHARS = 120_000


class WritingMethodService:
    def __init__(self, db) -> None:
        self._db = db
        self._repository = SqliteWritingMethodRepository(db)

    async def list_methods(self, *, include_archived: bool = False):
        return await self._repository.list_methods(
            status=None if include_archived else "active"
        )

    async def get_method(self, method_id: str):
        return await self._repository.get_method(method_id)

    async def create_method(self, **values):
        return await self._repository.create_method(_method_draft(**values))

    async def update_method(self, method_id: str, *, expected_draft_revision: int, **values):
        return await self._repository.update_method_draft(
            method_id,
            _method_draft(**values),
            expected_draft_revision=expected_draft_revision,
        )

    async def publish_method(self, method_id: str):
        return await self._repository.publish_method(method_id)

    async def copy_method(self, method_id: str):
        return await self._repository.copy_method(method_id)

    async def set_method_status(self, method_id: str, status: str):
        return await self._repository.set_method_status(method_id, status)

    async def delete_method(self, method_id: str) -> None:
        await self._repository.delete_method(method_id)

    async def list_schemes(self, *, include_archived: bool = False):
        return await self._repository.list_schemes(
            status=None if include_archived else "active"
        )

    async def get_scheme(self, scheme_id: str):
        return await self._repository.get_scheme(scheme_id)

    async def create_scheme(self, **values):
        return await self._repository.create_scheme(_scheme_draft(**values))

    async def update_scheme(self, scheme_id: str, *, expected_draft_revision: int, **values):
        return await self._repository.update_scheme_draft(
            scheme_id,
            _scheme_draft(**values),
            expected_draft_revision=expected_draft_revision,
        )

    async def publish_scheme(self, scheme_id: str):
        return await self._repository.publish_scheme(scheme_id)

    async def copy_scheme(self, scheme_id: str):
        return await self._repository.copy_scheme(scheme_id)

    async def set_scheme_status(self, scheme_id: str, status: str):
        return await self._repository.set_scheme_status(scheme_id, status)

    async def delete_scheme(self, scheme_id: str) -> None:
        await self._repository.delete_scheme(scheme_id)

    async def resolve_book_binding_snapshot(
        self,
        book_id: str,
        *,
        force_revision_ids: Sequence[str] = (),
        exclude_revision_ids: Sequence[str] = (),
    ) -> dict[str, Any]:
        # Writing request hydration is compatible with legacy/deleted scopes:
        # an absent book has no bindings. API commands still require the book.
        bindings = await self._repository.list_book_bindings(
            book_id,
            require_book=False,
        )
        return build_writing_method_binding_snapshot(
            book_id,
            bindings,
            force_revision_ids=force_revision_ids,
            exclude_revision_ids=exclude_revision_ids,
        )

    async def publish_batch(
        self,
        *,
        method_ids: Sequence[str],
        scheme_ids: Sequence[str],
    ) -> dict[str, Any]:
        method_revisions = []
        scheme_revisions = []
        async with self._db.transaction(cancellation_linearizable=True):
            for method_id in method_ids:
                method_revisions.append(
                    await self._repository.publish_method(str(method_id).strip())
                )
            for scheme_id in scheme_ids:
                scheme_revisions.append(
                    await self._repository.publish_scheme(str(scheme_id).strip())
                )
        return {
            "methodRevisions": method_revisions,
            "schemeRevisions": scheme_revisions,
        }

    async def list_book_bindings(self, book_id: str):
        return await self._repository.list_book_bindings(book_id)

    async def bind_book_revision(self, *, book_id: str, binding_type: str, revision_id: str):
        return await self._repository.bind_book_revision(
            book_id=book_id,
            binding_type=binding_type,
            revision_id=revision_id,
        )

    async def reorder_book_bindings(self, book_id: str, binding_ids: Sequence[str]):
        return await self._repository.reorder_book_bindings(book_id, binding_ids)

    async def unbind_book_revision(self, book_id: str, binding_id: str) -> None:
        await self._repository.unbind_book_revision(book_id, binding_id)

    async def upgrade_book_binding(self, book_id: str, binding_id: str, revision_id: str):
        return await self._repository.upgrade_book_binding(
            book_id, binding_id, revision_id
        )


def _method_draft(
    *,
    name: str,
    description: str = "",
    method_type: str,
    tags: Sequence[str] = (),
    markdown: str = "",
    metadata: Mapping[str, Any] | None = None,
) -> MethodDraft:
    cleaned_name = str(name or "").strip()
    if not cleaned_name:
        raise WritingMethodConflictError("写作方法名称不能为空")
    if method_type not in {"primary", "technique"}:
        raise WritingMethodConflictError("无效的写作方法类型")
    if len(markdown) > MAX_METHOD_MARKDOWN_CHARS:
        raise WritingMethodConflictError("写作方法正文超过 120000 字符上限")
    cleaned_tags = tuple(dict.fromkeys(
        str(tag).strip() for tag in tags if str(tag).strip()
    ))
    return MethodDraft(
        name=cleaned_name,
        description=str(description or "").strip(),
        method_type=method_type,
        tags=cleaned_tags,
        markdown=str(markdown or ""),
        metadata=dict(metadata or {}),
    )


def _scheme_draft(
    *,
    name: str,
    description: str = "",
    member_revision_ids: Sequence[str] = (),
) -> SchemeDraft:
    cleaned_name = str(name or "").strip()
    if not cleaned_name:
        raise WritingMethodConflictError("写作方案名称不能为空")
    return SchemeDraft(
        name=cleaned_name,
        description=str(description or "").strip(),
        member_revision_ids=tuple(member_revision_ids),
    )


__all__ = ["MAX_METHOD_MARKDOWN_CHARS", "WritingMethodService"]
