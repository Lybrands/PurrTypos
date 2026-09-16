"""Authoritative, read-only book queries for the replacement Writing Agent."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from agents.writing.revisions import record_revision, text_revision
from utils.text import extract_text_from_lexical


WRITING_READ_SCHEMA_VERSION = 1
DEFAULT_PAGE_LIMIT = 50
MAX_PAGE_LIMIT = 100
MAX_BATCH_READ_ITEMS = 32


class WritingReadScopeError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = str(code)


@dataclass(frozen=True, slots=True)
class WritingReadScope:
    book_id: str
    session_id: int | None = None
    chapter_id: str | None = None

    def __post_init__(self) -> None:
        book_id = str(self.book_id or "").strip()
        if not book_id:
            raise WritingReadScopeError(
                "writing_scope_invalid",
                "Writing read scope requires bookId",
            )
        object.__setattr__(self, "book_id", book_id)
        if self.session_id is not None:
            if type(self.session_id) is not int or self.session_id < 1:
                raise WritingReadScopeError(
                    "writing_scope_invalid",
                    "Writing read scope sessionId must be a positive integer",
                )
        chapter_id = str(self.chapter_id or "").strip() or None
        object.__setattr__(self, "chapter_id", chapter_id)

    @classmethod
    def from_mapping(cls, value: object) -> "WritingReadScope":
        if not isinstance(value, dict):
            raise WritingReadScopeError(
                "writing_scope_invalid",
                "Writing read scope is missing",
            )
        allowed = {"bookId", "sessionId", "chapterId"}
        if not set(value).issubset(allowed):
            raise WritingReadScopeError(
                "writing_scope_invalid",
                "Writing read scope contains unknown fields",
            )
        return cls(
            book_id=value.get("bookId"),
            session_id=value.get("sessionId"),
            chapter_id=value.get("chapterId"),
        )

    def to_mapping(self) -> dict[str, object]:
        return {
            "bookId": self.book_id,
            **({"sessionId": self.session_id} if self.session_id else {}),
            **({"chapterId": self.chapter_id} if self.chapter_id else {}),
        }


class SqliteWritingReadRepository:
    """Read current-book owned data without legacy material projection."""

    def __init__(self, db) -> None:
        self._db = db

    async def validate_scope(self, scope: WritingReadScope) -> WritingReadScope:
        book = await self._db.fetch_one(
            "SELECT id FROM books WHERE id = ?",
            [scope.book_id],
        )
        if book is None:
            raise WritingReadScopeError(
                "writing_book_not_found",
                "Writing read scope book does not exist",
            )
        if scope.session_id is not None:
            session = await self._db.fetch_one(
                "SELECT book_id, chapter_id FROM ai_sessions WHERE id = ?",
                [scope.session_id],
            )
            if session is None or str(session.get("book_id") or "") != scope.book_id:
                raise WritingReadScopeError(
                    "writing_session_scope_conflict",
                    "Writing session does not belong to the bound book",
                )
            session_chapter = str(session.get("chapter_id") or "").strip()
            if (
                scope.chapter_id
                and session_chapter
                and session_chapter != scope.chapter_id
            ):
                raise WritingReadScopeError(
                    "writing_chapter_scope_conflict",
                    "Writing chapter conflicts with the bound session",
                )
        if scope.chapter_id:
            chapter = await self._db.fetch_one(
                "SELECT c.id FROM outline_chapters AS c "
                "JOIN outlines AS o ON o.id = c.outline_id "
                "WHERE c.id = ? AND o.book_id = ? AND o.type = 'writing'",
                [scope.chapter_id, scope.book_id],
            )
            if chapter is None:
                raise WritingReadScopeError(
                    "writing_chapter_scope_conflict",
                    "Writing chapter does not belong to the bound book",
                )
        return scope

    async def story_background(self, scope: WritingReadScope) -> dict[str, Any]:
        scope = await self.validate_scope(scope)
        row = await self._db.fetch_one(
            "SELECT content, update_time FROM story_background WHERE book_id = ?",
            [scope.book_id],
        )
        content = str((row or {}).get("content") or "")
        return self._result(scope, {
            "background": {
                "content": content,
                "hasContent": bool(content.strip()),
                "updatedAt": (row or {}).get("update_time"),
                "baseRevision": text_revision(content),
            },
        })

    async def characters(
        self,
        scope: WritingReadScope,
        *,
        offset: int = 0,
        limit: int = DEFAULT_PAGE_LIMIT,
        character_ids: Iterable[int] = (),
        names: Iterable[str] = (),
        include_profile: bool = False,
    ) -> dict[str, Any]:
        scope = await self.validate_scope(scope)
        normalized_offset, normalized_limit = _page(offset, limit)
        ids = _positive_ids(character_ids, "characterIds")
        normalized_names = _names(names)
        where = ["book_id = ?"]
        params: list[object] = [scope.book_id]
        if ids:
            where.append("id IN (" + ",".join("?" for _ in ids) + ")")
            params.extend(ids)
        elif normalized_names:
            where.append(
                "name IN (" + ",".join("?" for _ in normalized_names) + ")"
            )
            params.extend(normalized_names)
        predicate = " AND ".join(where)
        count = await self._db.fetch_one(
            f"SELECT COUNT(*) AS total FROM characters WHERE {predicate}",
            params,
        )
        rows = await self._db.fetch_all(
            "SELECT id, name, tags"
            + (", profile_md" if include_profile else "")
            + f" FROM characters WHERE {predicate} "
            "ORDER BY id ASC LIMIT ? OFFSET ?",
            [*params, normalized_limit, normalized_offset],
        )
        items = [
            {
                "id": int(row["id"]),
                "name": _one_line(row.get("name")) or "未命名",
                "tags": str(row.get("tags") or ""),
                **(
                    {
                        "profileMd": str(row.get("profile_md") or ""),
                        "baseRevision": record_revision("character", {
                            "name": str(row.get("name") or ""),
                            "tags": str(row.get("tags") or ""),
                            "profileMd": str(row.get("profile_md") or ""),
                        }),
                    }
                    if include_profile
                    else {}
                ),
            }
            for row in rows
        ]
        total = int((count or {}).get("total") or 0)
        return self._result(scope, {
            "countScope": "current_book_owned_characters",
            **_page_result(items, total, normalized_offset, normalized_limit),
        })

    async def writing_chapters(
        self,
        scope: WritingReadScope,
        *,
        offset: int = 0,
        limit: int = DEFAULT_PAGE_LIMIT,
    ) -> dict[str, Any]:
        scope = await self.validate_scope(scope)
        normalized_offset, normalized_limit = _page(offset, limit)
        count = await self._db.fetch_one(
            "SELECT COUNT(*) AS total FROM outline_chapters AS c "
            "JOIN outlines AS o ON o.id = c.outline_id "
            "WHERE o.book_id = ? AND o.type = 'writing'",
            [scope.book_id],
        )
        rows = await self._db.fetch_all(
            "SELECT c.id, c.title, c.level, c.progress, c.sort, c.parent_id, "
            "CASE WHEN a.id IS NULL THEN 0 ELSE 1 END AS article_exists, "
            "CASE WHEN COALESCE(a.content, '') = '' THEN 0 ELSE 1 END "
            "AS stored_content_nonempty "
            "FROM outline_chapters AS c "
            "JOIN outlines AS o ON o.id = c.outline_id "
            "LEFT JOIN articles AS a ON a.chapter_id = c.id "
            "WHERE o.book_id = ? AND o.type = 'writing' "
            "ORDER BY c.sort ASC, c.id ASC LIMIT ? OFFSET ?",
            [scope.book_id, normalized_limit, normalized_offset],
        )
        items = [{
            "id": str(row["id"]),
            "title": _one_line(row.get("title")) or "未命名章节",
            "level": int(row.get("level") or 1),
            "progress": str(row.get("progress") or "todo"),
            "sort": int(row.get("sort") or 0),
            "parentId": str(row["parent_id"]) if row.get("parent_id") else None,
            "articleExists": bool(row.get("article_exists")),
            "storedContentNonempty": bool(row.get("stored_content_nonempty")),
        } for row in rows]
        total = int((count or {}).get("total") or 0)
        return self._result(
            scope,
            _page_result(items, total, normalized_offset, normalized_limit),
        )

    async def writing_chapter_contents(
        self,
        scope: WritingReadScope,
        *,
        chapter_ids: Iterable[str] = (),
        max_text_length: int = 64_000,
    ) -> dict[str, Any]:
        scope = await self.validate_scope(scope)
        ids = _text_ids(chapter_ids, "chapterIds")
        if not ids:
            if not scope.chapter_id:
                raise ValueError("chapterIds is required without a bound chapter")
            ids = (scope.chapter_id,)
        maximum = _positive_int(
            max_text_length,
            "maxTextLength",
            maximum=64_000,
        )
        placeholders = ",".join("?" for _ in ids)
        rows = await self._db.fetch_all(
            "SELECT c.id, c.title, COALESCE(a.content, '') AS content, "
            "CASE WHEN a.id IS NULL THEN 0 ELSE 1 END AS article_exists "
            "FROM outline_chapters AS c "
            "JOIN outlines AS o ON o.id = c.outline_id "
            "LEFT JOIN articles AS a ON a.chapter_id = c.id "
            f"WHERE o.book_id = ? AND o.type = 'writing' "
            f"AND c.id IN ({placeholders})",
            [scope.book_id, *ids],
        )
        by_id = {str(row["id"]): row for row in rows}
        remaining = maximum
        items = []
        for chapter_id in ids:
            row = by_id.get(chapter_id)
            if row is None:
                continue
            stored = str(row.get("content") or "")
            try:
                plain = extract_text_from_lexical(stored) if stored else ""
            except Exception:
                plain = ""
            returned = plain[:remaining]
            items.append({
                "id": chapter_id,
                "title": _one_line(row.get("title")) or "未命名章节",
                "content": returned,
                "hasContent": bool(plain.strip()),
                "articleExists": bool(row.get("article_exists")),
                "truncated": len(returned) < len(plain),
                "returnedCharacters": len(returned),
                "totalCharacters": len(plain),
                "baseRevision": text_revision(stored),
            })
            remaining -= len(returned)
        return self._result(scope, {
            "items": items,
            "missingChapterIds": [
                chapter_id for chapter_id in ids if chapter_id not in by_id
            ],
            "maxTextLength": maximum,
            "returnedCharacters": sum(
                int(item["returnedCharacters"]) for item in items
            ),
        })

    async def writing_outlines(
        self,
        scope: WritingReadScope,
        *,
        offset: int = 0,
        limit: int = DEFAULT_PAGE_LIMIT,
    ) -> dict[str, Any]:
        scope = await self.validate_scope(scope)
        normalized_offset, normalized_limit = _page(offset, limit)
        allowed_types = ("global", "volume", "chapter", "writing")
        placeholders = ",".join("?" for _ in allowed_types)
        count = await self._db.fetch_one(
            f"SELECT COUNT(*) AS total FROM outlines WHERE book_id = ? "
            f"AND type IN ({placeholders})",
            [scope.book_id, *allowed_types],
        )
        rows = await self._db.fetch_all(
            "SELECT id, title, type, sort, "
            "CASE WHEN COALESCE(markdown_content, '') = '' THEN 0 ELSE 1 END "
            "AS stored_content_nonempty FROM outlines WHERE book_id = ? "
            f"AND type IN ({placeholders}) "
            "ORDER BY sort ASC, id ASC LIMIT ? OFFSET ?",
            [
                scope.book_id,
                *allowed_types,
                normalized_limit,
                normalized_offset,
            ],
        )
        items = [{
            "id": str(row["id"]),
            "title": _one_line(row.get("title")) or "未命名大纲",
            "outlineType": str(row.get("type") or ""),
            "sort": int(row.get("sort") or 0),
            "storedContentNonempty": bool(row.get("stored_content_nonempty")),
        } for row in rows]
        total = int((count or {}).get("total") or 0)
        return self._result(
            scope,
            _page_result(items, total, normalized_offset, normalized_limit),
        )

    async def writing_outline_contents(
        self,
        scope: WritingReadScope,
        *,
        outline_ids: Iterable[str],
        max_text_length: int = 64_000,
    ) -> dict[str, Any]:
        scope = await self.validate_scope(scope)
        ids = _text_ids(outline_ids, "outlineIds")
        if not ids:
            raise ValueError("outlineIds must contain at least one identifier")
        maximum = _positive_int(
            max_text_length,
            "maxTextLength",
            maximum=64_000,
        )
        placeholders = ",".join("?" for _ in ids)
        rows = await self._db.fetch_all(
            "SELECT id, title, type, COALESCE(markdown_content, '') AS content "
            "FROM outlines WHERE book_id = ? "
            f"AND type IN ('global', 'volume', 'chapter', 'writing') "
            f"AND id IN ({placeholders})",
            [scope.book_id, *ids],
        )
        by_id = {str(row["id"]): row for row in rows}
        remaining = maximum
        items = []
        for outline_id in ids:
            row = by_id.get(outline_id)
            if row is None:
                continue
            content = str(row.get("content") or "")
            returned = content[:remaining]
            items.append({
                "id": outline_id,
                "title": _one_line(row.get("title")) or "未命名大纲",
                "outlineType": str(row.get("type") or ""),
                "markdown": returned,
                "hasContent": bool(content.strip()),
                "truncated": len(returned) < len(content),
                "returnedCharacters": len(returned),
                "totalCharacters": len(content),
                "baseRevision": text_revision(content),
            })
            remaining -= len(returned)
        return self._result(scope, {
            "items": items,
            "missingOutlineIds": [
                outline_id for outline_id in ids if outline_id not in by_id
            ],
            "maxTextLength": maximum,
            "returnedCharacters": sum(
                int(item["returnedCharacters"]) for item in items
            ),
        })

    async def global_outline(
        self,
        scope: WritingReadScope,
        *,
        max_text_length: int = 32_000,
    ) -> dict[str, Any]:
        scope = await self.validate_scope(scope)
        maximum = _positive_int(max_text_length, "maxTextLength", maximum=64_000)
        row = await self._db.fetch_one(
            "SELECT id, title, markdown_content FROM outlines "
            "WHERE book_id = ? AND type = 'global' ORDER BY create_time, id LIMIT 1",
            [scope.book_id],
        )
        raw = str((row or {}).get("markdown_content") or "")
        content = raw[:maximum]
        return self._result(scope, {
            "outline": None if row is None else {
                "id": str(row["id"]),
                "title": _one_line(row.get("title")) or "总纲",
                "markdown": content,
                "hasContent": bool(raw.strip()),
                "truncated": len(raw) > len(content),
                "returnedCharacters": len(content),
                "totalCharacters": len(raw),
                "baseRevision": text_revision(raw),
            },
        })

    async def setting_entities(
        self,
        scope: WritingReadScope,
        *,
        offset: int = 0,
        limit: int = DEFAULT_PAGE_LIMIT,
        entity_ids: Iterable[int] = (),
        names: Iterable[str] = (),
        entity_type: str | None = None,
        include_profile: bool = False,
    ) -> dict[str, Any]:
        scope = await self.validate_scope(scope)
        normalized_offset, normalized_limit = _page(offset, limit)
        ids = _positive_ids(entity_ids, "entityIds")
        normalized_names = _names(names)
        normalized_type = str(entity_type or "").strip().lower()
        if normalized_type and normalized_type not in {
            "location", "faction", "item", "other"
        }:
            raise ValueError("entityType is invalid")
        where = ["book_id = ?"]
        params: list[object] = [scope.book_id]
        if normalized_type:
            where.append("entity_type = ?")
            params.append(normalized_type)
        if ids:
            where.append("id IN (" + ",".join("?" for _ in ids) + ")")
            params.extend(ids)
        elif normalized_names:
            where.append(
                "name IN (" + ",".join("?" for _ in normalized_names) + ")"
            )
            params.extend(normalized_names)
        predicate = " AND ".join(where)
        count = await self._db.fetch_one(
            f"SELECT COUNT(*) AS total FROM setting_entities WHERE {predicate}",
            params,
        )
        rows = await self._db.fetch_all(
            "SELECT id, entity_type, name, tags"
            + (", profile_md" if include_profile else "")
            + f" FROM setting_entities WHERE {predicate} "
            "ORDER BY id ASC LIMIT ? OFFSET ?",
            [*params, normalized_limit, normalized_offset],
        )
        items = [{
            "id": int(row["id"]),
            "entityType": str(row.get("entity_type") or "other"),
            "name": _one_line(row.get("name")) or "未命名设定",
            "tags": str(row.get("tags") or ""),
            **(
                {
                    "profileMd": str(row.get("profile_md") or ""),
                    "baseRevision": record_revision("setting_entity", {
                        "name": str(row.get("name") or ""),
                        "tags": str(row.get("tags") or ""),
                        "profileMd": str(row.get("profile_md") or ""),
                    }),
                }
                if include_profile
                else {}
            ),
        } for row in rows]
        total = int((count or {}).get("total") or 0)
        return self._result(
            scope,
            _page_result(items, total, normalized_offset, normalized_limit),
        )

    @staticmethod
    def _result(
        scope: WritingReadScope,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "schemaVersion": WRITING_READ_SCHEMA_VERSION,
            "scope": scope.to_mapping(),
            **payload,
        }


def _page(offset: int, limit: int) -> tuple[int, int]:
    if type(offset) is not int or offset < 0:
        raise ValueError("offset must be a non-negative integer")
    return offset, _positive_int(limit, "limit", maximum=MAX_PAGE_LIMIT)


def _positive_int(value: object, name: str, *, maximum: int) -> int:
    if type(value) is not int or value < 1 or value > maximum:
        raise ValueError(f"{name} must be between 1 and {maximum}")
    return value


def _positive_ids(values: Iterable[int], name: str) -> tuple[int, ...]:
    result = tuple(values or ())
    if len(result) > MAX_PAGE_LIMIT or any(
        type(value) is not int or value < 1 for value in result
    ):
        raise ValueError(f"{name} must contain at most 100 positive integers")
    return tuple(dict.fromkeys(result))


def _text_ids(values: Iterable[str], name: str) -> tuple[str, ...]:
    result = tuple(str(value or "").strip() for value in (values or ()))
    if (
        len(result) > MAX_BATCH_READ_ITEMS
        or any(not value or len(value) > 512 for value in result)
    ):
        raise ValueError(
            f"{name} must contain at most {MAX_BATCH_READ_ITEMS} identifiers"
        )
    return tuple(dict.fromkeys(result))


def _names(values: Iterable[str]) -> tuple[str, ...]:
    result = tuple(
        str(value or "").strip()
        for value in (values or ())
        if str(value or "").strip()
    )
    if len(result) > MAX_PAGE_LIMIT:
        raise ValueError("names must contain at most 100 values")
    return tuple(dict.fromkeys(result))


def _page_result(
    items: list[dict[str, Any]],
    total: int,
    offset: int,
    limit: int,
) -> dict[str, Any]:
    next_offset = offset + len(items)
    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "items": items,
        "nextOffset": next_offset if next_offset < total else None,
    }


def _one_line(value: object) -> str:
    return " ".join(str(value or "").split())


__all__ = [
    "DEFAULT_PAGE_LIMIT",
    "MAX_BATCH_READ_ITEMS",
    "MAX_PAGE_LIMIT",
    "SqliteWritingReadRepository",
    "WRITING_READ_SCHEMA_VERSION",
    "WritingReadScope",
    "WritingReadScopeError",
]
