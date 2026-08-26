"""SQLite persistence for writing methods, schemes, and book bindings."""

from __future__ import annotations

import json
from typing import Any, Sequence

from domains.writing.methods import (
    MethodDraft,
    SchemeDraft,
    WritingMethodConflictError,
    WritingMethodNotFoundError,
    WritingMethodReferenceError,
    canonical_json,
    clean_ids,
    content_digest,
    members_digest,
)
from utils.id_utils import short_id8


class SqliteWritingMethodRepository:
    def __init__(self, db) -> None:
        self._db = db

    async def list_methods(self, *, status: str | None = "active") -> list[dict[str, Any]]:
        where = "" if status is None else "WHERE status = ?"
        params: list[Any] = [] if status is None else [status]
        rows = await self._db.fetch_all(
            f"SELECT * FROM writing_methods {where} "
            "ORDER BY is_builtin DESC, update_time DESC, name ASC",
            params,
        )
        result = []
        for row in rows:
            item = _method_row(row)
            revision_id = row.get("current_published_revision_id")
            item["revisions"] = (
                [await self.get_method_revision(str(revision_id))]
                if revision_id else []
            )
            result.append(item)
        return result

    async def get_method(self, method_id: str) -> dict[str, Any]:
        row = await self._db.fetch_one(
            "SELECT * FROM writing_methods WHERE id = ?", [method_id]
        )
        if row is None:
            raise WritingMethodNotFoundError("写作方法不存在")
        result = _method_row(row)
        result["revisions"] = [
            _method_revision_row(item)
            for item in await self._db.fetch_all(
                "SELECT * FROM writing_method_revisions WHERE method_id = ? "
                "ORDER BY version_no DESC",
                [method_id],
            )
        ]
        return result

    async def create_method(self, draft: MethodDraft) -> dict[str, Any]:
        method_id = short_id8()
        await self._db.execute(
            "INSERT INTO writing_methods "
            "(id, name, description, method_type, tags_json, draft_markdown, "
            "draft_metadata_json, draft_revision) VALUES (?, ?, ?, ?, ?, ?, ?, 0)",
            [
                method_id, draft.name, draft.description, draft.method_type,
                canonical_json(draft.tags), draft.markdown,
                canonical_json(dict(draft.metadata)),
            ],
        )
        return await self.get_method(method_id)

    async def update_method_draft(
        self,
        method_id: str,
        draft: MethodDraft,
        *,
        expected_draft_revision: int,
    ) -> dict[str, Any]:
        async with self._db.transaction():
            row = await self._require_method(method_id)
            self._require_mutable(row, "内置写作方法不可编辑，请先复制")
            actual = int(row.get("draft_revision") or 0)
            if actual != expected_draft_revision:
                raise WritingMethodConflictError(
                    f"写作方法草稿已更新，当前修订为 {actual}"
                )
            await self._db.execute(
                "UPDATE writing_methods SET name = ?, description = ?, "
                "method_type = ?, tags_json = ?, draft_markdown = ?, "
                "draft_metadata_json = ?, draft_revision = draft_revision + 1, "
                "update_time = CURRENT_TIMESTAMP WHERE id = ?",
                [
                    draft.name, draft.description, draft.method_type,
                    canonical_json(draft.tags), draft.markdown,
                    canonical_json(dict(draft.metadata)), method_id,
                ],
            )
        return await self.get_method(method_id)

    async def publish_method(self, method_id: str) -> dict[str, Any]:
        async with self._db.transaction():
            row = await self._require_method(method_id)
            self._require_mutable(row, "内置写作方法不可重新发布")
            markdown = str(row.get("draft_markdown") or "").strip()
            if not markdown:
                raise WritingMethodConflictError("写作方法正文不能为空")
            latest = await self._db.fetch_one(
                "SELECT COALESCE(MAX(version_no), 0) AS version_no "
                "FROM writing_method_revisions WHERE method_id = ?",
                [method_id],
            )
            version_no = int((latest or {}).get("version_no") or 0) + 1
            revision_id = short_id8()
            metadata = _json_object(row.get("draft_metadata_json"))
            await self._db.execute(
                "INSERT INTO writing_method_revisions "
                "(id, method_id, version_no, name, description, method_type, "
                "tags_json, markdown_body, metadata_json, content_digest) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    revision_id, method_id, version_no, row["name"],
                    row.get("description") or "", row["method_type"],
                    row.get("tags_json") or "[]", markdown,
                    canonical_json(metadata), content_digest(markdown, metadata),
                ],
            )
            await self._db.execute(
                "UPDATE writing_methods SET current_published_revision_id = ?, "
                "update_time = CURRENT_TIMESTAMP WHERE id = ?",
                [revision_id, method_id],
            )
        return await self.get_method_revision(revision_id)

    async def get_method_revision(self, revision_id: str) -> dict[str, Any]:
        row = await self._db.fetch_one(
            "SELECT * FROM writing_method_revisions WHERE id = ?", [revision_id]
        )
        if row is None:
            raise WritingMethodNotFoundError("写作方法版本不存在")
        return _method_revision_row(row)

    async def search_published_methods(
        self,
        query: str,
        *,
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        normalized = str(query or "").strip()
        pattern = f"%{normalized}%"
        rows = await self._db.fetch_all(
            "SELECT r.id, r.method_id, r.version_no, r.name, r.description, "
            "r.method_type, r.tags_json, r.content_digest "
            "FROM writing_methods m JOIN writing_method_revisions r "
            "ON r.id = m.current_published_revision_id "
            "WHERE m.status = 'active' AND (? = '' OR r.name LIKE ? "
            "OR r.description LIKE ? OR r.tags_json LIKE ?) "
            "ORDER BY m.is_builtin DESC, m.update_time DESC, r.name ASC LIMIT ?",
            [normalized, pattern, pattern, pattern, max(1, min(int(limit), 20))],
        )
        return [_method_revision_row(row) for row in rows]

    async def copy_method(self, method_id: str) -> dict[str, Any]:
        source = await self._require_method(method_id)
        revision_id = source.get("current_published_revision_id")
        published = (
            await self._db.fetch_one(
                "SELECT * FROM writing_method_revisions WHERE id = ?", [revision_id]
            )
            if revision_id else None
        )
        method_id_copy = short_id8()
        await self._db.execute(
            "INSERT INTO writing_methods "
            "(id, name, description, method_type, tags_json, source_type, "
            "source_ref_json, draft_markdown, draft_metadata_json, draft_revision) "
            "VALUES (?, ?, ?, ?, ?, 'copy', ?, ?, ?, 0)",
            [
                method_id_copy, f"{source['name']}（副本）",
                source.get("description") or "", source["method_type"],
                source.get("tags_json") or "[]",
                canonical_json({"methodId": method_id, "revisionId": revision_id}),
                (published or {}).get("markdown_body") or source.get("draft_markdown") or "",
                (published or {}).get("metadata_json") or source.get("draft_metadata_json") or "{}",
            ],
        )
        return await self.get_method(method_id_copy)

    async def set_method_status(self, method_id: str, status: str) -> dict[str, Any]:
        if status not in {"active", "archived"}:
            raise WritingMethodConflictError("无效的写作方法状态")
        row = await self._require_method(method_id)
        self._require_mutable(row, "内置写作方法不可归档")
        await self._db.execute(
            "UPDATE writing_methods SET status = ?, update_time = CURRENT_TIMESTAMP WHERE id = ?",
            [status, method_id],
        )
        return await self.get_method(method_id)

    async def delete_method(self, method_id: str) -> None:
        async with self._db.transaction():
            row = await self._require_method(method_id)
            self._require_mutable(row, "内置写作方法不可删除")
            referenced = await self._db.fetch_one(
                "SELECT 1 AS found FROM writing_method_revisions r "
                "WHERE r.method_id = ? AND ("
                "EXISTS (SELECT 1 FROM writing_scheme_revision_members m "
                "WHERE m.method_revision_id = r.id) OR "
                "EXISTS (SELECT 1 FROM book_writing_method_bindings b "
                "WHERE b.method_revision_id = r.id)) LIMIT 1",
                [method_id],
            )
            if referenced:
                raise WritingMethodReferenceError("写作方法已有方案或作品引用，只能归档")
            await self._db.execute(
                "DELETE FROM writing_method_revisions WHERE method_id = ?", [method_id]
            )
            await self._db.execute("DELETE FROM writing_methods WHERE id = ?", [method_id])

    async def list_schemes(self, *, status: str | None = "active") -> list[dict[str, Any]]:
        where = "" if status is None else "WHERE status = ?"
        params: list[Any] = [] if status is None else [status]
        rows = await self._db.fetch_all(
            f"SELECT * FROM writing_schemes {where} "
            "ORDER BY is_builtin DESC, update_time DESC, name ASC",
            params,
        )
        return [_scheme_row(row) for row in rows]

    async def get_scheme(self, scheme_id: str) -> dict[str, Any]:
        row = await self._db.fetch_one(
            "SELECT * FROM writing_schemes WHERE id = ?", [scheme_id]
        )
        if row is None:
            raise WritingMethodNotFoundError("写作方案不存在")
        result = _scheme_row(row)
        revisions = await self._db.fetch_all(
            "SELECT * FROM writing_scheme_revisions WHERE scheme_id = ? "
            "ORDER BY version_no DESC",
            [scheme_id],
        )
        result["revisions"] = [await self._scheme_revision(row) for row in revisions]
        return result

    async def create_scheme(self, draft: SchemeDraft) -> dict[str, Any]:
        member_ids = clean_ids(draft.member_revision_ids)
        await self._validate_method_revisions(member_ids)
        scheme_id = short_id8()
        await self._db.execute(
            "INSERT INTO writing_schemes "
            "(id, name, description, draft_members_json, draft_revision) "
            "VALUES (?, ?, ?, ?, 0)",
            [scheme_id, draft.name, draft.description, canonical_json(member_ids)],
        )
        return await self.get_scheme(scheme_id)

    async def update_scheme_draft(
        self,
        scheme_id: str,
        draft: SchemeDraft,
        *,
        expected_draft_revision: int,
    ) -> dict[str, Any]:
        member_ids = clean_ids(draft.member_revision_ids)
        async with self._db.transaction():
            row = await self._require_scheme(scheme_id)
            self._require_mutable(row, "内置写作方案不可编辑，请先复制")
            actual = int(row.get("draft_revision") or 0)
            if actual != expected_draft_revision:
                raise WritingMethodConflictError(
                    f"写作方案草稿已更新，当前修订为 {actual}"
                )
            await self._validate_method_revisions(member_ids)
            await self._db.execute(
                "UPDATE writing_schemes SET name = ?, description = ?, "
                "draft_members_json = ?, draft_revision = draft_revision + 1, "
                "update_time = CURRENT_TIMESTAMP WHERE id = ?",
                [draft.name, draft.description, canonical_json(member_ids), scheme_id],
            )
        return await self.get_scheme(scheme_id)

    async def publish_scheme(self, scheme_id: str) -> dict[str, Any]:
        async with self._db.transaction():
            row = await self._require_scheme(scheme_id)
            self._require_mutable(row, "内置写作方案不可重新发布")
            member_ids = clean_ids(_json_list(row.get("draft_members_json")))
            if not member_ids:
                raise WritingMethodConflictError("写作方案至少需要一个已发布方法")
            await self._validate_method_revisions(member_ids)
            latest = await self._db.fetch_one(
                "SELECT COALESCE(MAX(version_no), 0) AS version_no "
                "FROM writing_scheme_revisions WHERE scheme_id = ?",
                [scheme_id],
            )
            version_no = int((latest or {}).get("version_no") or 0) + 1
            revision_id = short_id8()
            source_ref = _json_object(row.get("source_ref_json"))
            revision_metadata = {
                "schemaVersion": 1,
                **({"sourceRef": source_ref} if source_ref else {}),
            }
            await self._db.execute(
                "INSERT INTO writing_scheme_revisions "
                "(id, scheme_id, version_no, name, description, metadata_json, members_digest) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    revision_id, scheme_id, version_no, row["name"],
                    row.get("description") or "", canonical_json(revision_metadata),
                    members_digest(member_ids),
                ],
            )
            for ordinal, method_revision_id in enumerate(member_ids):
                await self._db.execute(
                    "INSERT INTO writing_scheme_revision_members "
                    "(scheme_revision_id, ordinal, method_revision_id) VALUES (?, ?, ?)",
                    [revision_id, ordinal, method_revision_id],
                )
            await self._db.execute(
                "UPDATE writing_schemes SET current_published_revision_id = ?, "
                "update_time = CURRENT_TIMESTAMP WHERE id = ?",
                [revision_id, scheme_id],
            )
        return await self.get_scheme_revision(revision_id)

    async def get_scheme_revision(self, revision_id: str) -> dict[str, Any]:
        row = await self._db.fetch_one(
            "SELECT * FROM writing_scheme_revisions WHERE id = ?", [revision_id]
        )
        if row is None:
            raise WritingMethodNotFoundError("写作方案版本不存在")
        return await self._scheme_revision(row)

    async def copy_scheme(self, scheme_id: str) -> dict[str, Any]:
        source = await self._require_scheme(scheme_id)
        revision_id = source.get("current_published_revision_id")
        member_ids = (
            [item["method_revision_id"] for item in await self._db.fetch_all(
                "SELECT method_revision_id FROM writing_scheme_revision_members "
                "WHERE scheme_revision_id = ? ORDER BY ordinal", [revision_id]
            )]
            if revision_id else _json_list(source.get("draft_members_json"))
        )
        copy_id = short_id8()
        await self._db.execute(
            "INSERT INTO writing_schemes "
            "(id, name, description, draft_members_json, draft_revision, source_type, source_ref_json) "
            "VALUES (?, ?, ?, ?, 0, 'copy', ?)",
            [
                copy_id, f"{source['name']}（副本）", source.get("description") or "",
                canonical_json(member_ids),
                canonical_json({"schemeId": scheme_id, "revisionId": revision_id}),
            ],
        )
        return await self.get_scheme(copy_id)

    async def set_scheme_status(self, scheme_id: str, status: str) -> dict[str, Any]:
        if status not in {"active", "archived"}:
            raise WritingMethodConflictError("无效的写作方案状态")
        row = await self._require_scheme(scheme_id)
        self._require_mutable(row, "内置写作方案不可归档")
        await self._db.execute(
            "UPDATE writing_schemes SET status = ?, update_time = CURRENT_TIMESTAMP WHERE id = ?",
            [status, scheme_id],
        )
        return await self.get_scheme(scheme_id)

    async def delete_scheme(self, scheme_id: str) -> None:
        async with self._db.transaction():
            row = await self._require_scheme(scheme_id)
            self._require_mutable(row, "内置写作方案不可删除")
            referenced = await self._db.fetch_one(
                "SELECT 1 AS found FROM writing_scheme_revisions r "
                "JOIN book_writing_method_bindings b ON b.scheme_revision_id = r.id "
                "WHERE r.scheme_id = ? LIMIT 1",
                [scheme_id],
            )
            if referenced:
                raise WritingMethodReferenceError("写作方案已有作品引用，只能归档")
            revision_rows = await self._db.fetch_all(
                "SELECT id FROM writing_scheme_revisions WHERE scheme_id = ?", [scheme_id]
            )
            for revision in revision_rows:
                await self._db.execute(
                    "DELETE FROM writing_scheme_revision_members WHERE scheme_revision_id = ?",
                    [revision["id"]],
                )
            await self._db.execute(
                "DELETE FROM writing_scheme_revisions WHERE scheme_id = ?", [scheme_id]
            )
            await self._db.execute("DELETE FROM writing_schemes WHERE id = ?", [scheme_id])

    async def list_book_bindings(
        self,
        book_id: str,
        *,
        require_book: bool = True,
    ) -> list[dict[str, Any]]:
        if require_book:
            await self._require_book(book_id)
        rows = await self._db.fetch_all(
            "SELECT * FROM book_writing_method_bindings WHERE book_id = ? "
            "ORDER BY priority ASC, create_time ASC",
            [book_id],
        )
        return [await self._binding_row(row) for row in rows]

    async def bind_book_revision(
        self,
        *,
        book_id: str,
        binding_type: str,
        revision_id: str,
        source: str = "user",
    ) -> dict[str, Any]:
        async with self._db.transaction():
            await self._require_book(book_id)
            if binding_type == "method":
                await self.get_method_revision(revision_id)
            elif binding_type == "scheme":
                await self.get_scheme_revision(revision_id)
            else:
                raise WritingMethodConflictError("无效的作品写作方法绑定类型")
            position = await self._db.fetch_one(
                "SELECT COALESCE(MAX(priority), -1) + 1 AS priority "
                "FROM book_writing_method_bindings WHERE book_id = ?",
                [book_id],
            )
            binding_id = short_id8()
            try:
                await self._db.execute(
                    "INSERT INTO book_writing_method_bindings "
                    "(id, book_id, binding_type, method_revision_id, scheme_revision_id, priority, source) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [
                        binding_id, book_id, binding_type,
                        revision_id if binding_type == "method" else None,
                        revision_id if binding_type == "scheme" else None,
                        int((position or {}).get("priority") or 0), source,
                    ],
                )
            except Exception as error:
                if "UNIQUE constraint failed" in str(error):
                    raise WritingMethodConflictError("该版本已经绑定到作品") from error
                raise
        row = await self._require_binding(book_id, binding_id)
        return await self._binding_row(row)

    async def reorder_book_bindings(self, book_id: str, binding_ids: Sequence[str]) -> list[dict[str, Any]]:
        ordered = clean_ids(binding_ids)
        async with self._db.transaction():
            current = await self._db.fetch_all(
                "SELECT id FROM book_writing_method_bindings WHERE book_id = ?", [book_id]
            )
            current_ids = {str(row["id"]) for row in current}
            if set(ordered) != current_ids:
                raise WritingMethodConflictError("排序必须包含作品当前的全部顶层绑定")
            for priority, binding_id in enumerate(ordered):
                await self._db.execute(
                    "UPDATE book_writing_method_bindings SET priority = ?, "
                    "update_time = CURRENT_TIMESTAMP WHERE id = ? AND book_id = ?",
                    [priority, binding_id, book_id],
                )
        return await self.list_book_bindings(book_id)

    async def unbind_book_revision(self, book_id: str, binding_id: str) -> None:
        await self._require_binding(book_id, binding_id)
        async with self._db.transaction():
            await self._db.execute(
                "DELETE FROM book_writing_method_bindings WHERE id = ? AND book_id = ?",
                [binding_id, book_id],
            )
            rows = await self._db.fetch_all(
                "SELECT id FROM book_writing_method_bindings WHERE book_id = ? "
                "ORDER BY priority, create_time",
                [book_id],
            )
            for priority, row in enumerate(rows):
                await self._db.execute(
                    "UPDATE book_writing_method_bindings SET priority = ? WHERE id = ?",
                    [priority, row["id"]],
                )

    async def upgrade_book_binding(
        self, book_id: str, binding_id: str, revision_id: str
    ) -> dict[str, Any]:
        async with self._db.transaction():
            binding = await self._require_binding(book_id, binding_id)
            if binding["binding_type"] == "method":
                old = await self.get_method_revision(binding["method_revision_id"])
                new = await self.get_method_revision(revision_id)
                if old["method_id"] != new["method_id"]:
                    raise WritingMethodConflictError("只能升级同一个写作方法的版本")
                await self._db.execute(
                    "UPDATE book_writing_method_bindings SET method_revision_id = ?, "
                    "update_time = CURRENT_TIMESTAMP WHERE id = ?",
                    [revision_id, binding_id],
                )
            else:
                old = await self.get_scheme_revision(binding["scheme_revision_id"])
                new = await self.get_scheme_revision(revision_id)
                if old["scheme_id"] != new["scheme_id"]:
                    raise WritingMethodConflictError("只能升级同一个写作方案的版本")
                await self._db.execute(
                    "UPDATE book_writing_method_bindings SET scheme_revision_id = ?, "
                    "update_time = CURRENT_TIMESTAMP WHERE id = ?",
                    [revision_id, binding_id],
                )
        return await self._binding_row(await self._require_binding(book_id, binding_id))

    async def _validate_method_revisions(self, revision_ids: Sequence[str]) -> None:
        for revision_id in revision_ids:
            await self.get_method_revision(revision_id)

    async def _require_method(self, method_id: str) -> dict[str, Any]:
        row = await self._db.fetch_one("SELECT * FROM writing_methods WHERE id = ?", [method_id])
        if row is None:
            raise WritingMethodNotFoundError("写作方法不存在")
        return row

    async def _require_scheme(self, scheme_id: str) -> dict[str, Any]:
        row = await self._db.fetch_one("SELECT * FROM writing_schemes WHERE id = ?", [scheme_id])
        if row is None:
            raise WritingMethodNotFoundError("写作方案不存在")
        return row

    async def _require_book(self, book_id: str) -> dict[str, Any]:
        row = await self._db.fetch_one("SELECT * FROM books WHERE id = ?", [book_id])
        if row is None:
            raise WritingMethodNotFoundError("作品不存在")
        return row

    async def _require_binding(self, book_id: str, binding_id: str) -> dict[str, Any]:
        row = await self._db.fetch_one(
            "SELECT * FROM book_writing_method_bindings WHERE id = ? AND book_id = ?",
            [binding_id, book_id],
        )
        if row is None:
            raise WritingMethodNotFoundError("作品写作方法绑定不存在")
        return row

    async def _scheme_revision(self, row: dict[str, Any]) -> dict[str, Any]:
        result = _scheme_revision_row(row)
        members = await self._db.fetch_all(
            "SELECT m.ordinal, m.method_revision_id, r.id, r.method_id, r.name, "
            "r.description, r.method_type, r.tags_json, r.markdown_body, "
            "r.metadata_json, r.version_no, r.content_digest "
            "FROM writing_scheme_revision_members m "
            "JOIN writing_method_revisions r ON r.id = m.method_revision_id "
            "WHERE m.scheme_revision_id = ? ORDER BY m.ordinal",
            [row["id"]],
        )
        result["members"] = [_method_revision_row(member) for member in members]
        return result

    async def _binding_row(self, row: dict[str, Any]) -> dict[str, Any]:
        result = dict(row)
        revision_id = row.get("method_revision_id") or row.get("scheme_revision_id")
        result["revision"] = (
            await self.get_method_revision(str(revision_id))
            if row["binding_type"] == "method"
            else await self.get_scheme_revision(str(revision_id))
        )
        return result

    @staticmethod
    def _require_mutable(row: dict[str, Any], message: str) -> None:
        if bool(row.get("is_builtin")):
            raise WritingMethodConflictError(message)


def _json_object(value: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _json_list(value: Any) -> list[str]:
    try:
        parsed = json.loads(str(value or "[]"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


def _method_row(row: dict[str, Any]) -> dict[str, Any]:
    result = dict(row)
    result["tags"] = _json_list(row.get("tags_json"))
    result["source_ref"] = _json_object(row.get("source_ref_json"))
    result["draft_metadata"] = _json_object(row.get("draft_metadata_json"))
    return result


def _method_revision_row(row: dict[str, Any]) -> dict[str, Any]:
    result = dict(row)
    result["tags"] = _json_list(row.get("tags_json"))
    result["metadata"] = _json_object(row.get("metadata_json"))
    return result


def _scheme_row(row: dict[str, Any]) -> dict[str, Any]:
    result = dict(row)
    result["draft_member_revision_ids"] = _json_list(row.get("draft_members_json"))
    result["source_ref"] = _json_object(row.get("source_ref_json"))
    return result


def _scheme_revision_row(row: dict[str, Any]) -> dict[str, Any]:
    result = dict(row)
    result["metadata"] = _json_object(row.get("metadata_json"))
    return result


__all__ = ["SqliteWritingMethodRepository"]
