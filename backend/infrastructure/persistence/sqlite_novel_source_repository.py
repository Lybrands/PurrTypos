"""SQLite repository for immutable source works, revisions, and sections."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from domains.novel_sources import (
    NovelSourceConflictError,
    NovelSourceNotFoundError,
    PARSER_VERSION,
    ParsedSourceSection,
    parse_source_sections,
    sha256_text,
)
from utils.book_structure import get_ordered_leaf_chapters, load_chapter_texts
from utils.id_utils import short_id8


class SqliteNovelSourceRepository:
    def __init__(self, db) -> None:
        self._db = db

    async def list_works(self, *, include_archived: bool = False) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT w.*, (SELECT COUNT(*) FROM novel_source_revisions r "
            "WHERE r.work_id = w.id) AS revision_count, "
            "(SELECT r.id FROM novel_source_revisions r WHERE r.work_id = w.id "
            "ORDER BY r.version_no DESC LIMIT 1) AS latest_revision_id "
            "FROM novel_source_works w "
            + ("" if include_archived else "WHERE w.status = 'active' ")
            + "ORDER BY w.update_time DESC, w.title ASC"
        )
        return [_work_row(row) for row in rows]

    async def get_work(self, work_id: str) -> dict[str, Any]:
        row = await self._db.fetch_one(
            "SELECT * FROM novel_source_works WHERE id = ?", [work_id]
        )
        if row is None:
            raise NovelSourceNotFoundError("来源作品不存在")
        result = _work_row(row)
        result["revisions"] = [
            _revision_row(item)
            for item in await self._db.fetch_all(
                "SELECT * FROM novel_source_revisions WHERE work_id = ? "
                "ORDER BY version_no DESC",
                [work_id],
            )
        ]
        return result

    async def create_external_revision(
        self,
        *,
        title: str,
        sections: Sequence[ParsedSourceSection],
        content_digest: str,
        byte_count: int,
        character_count: int,
        source_metadata: Mapping[str, Any],
        work_id: str | None = None,
    ) -> dict[str, Any]:
        async with self._db.transaction(cancellation_linearizable=True):
            resolved_work_id = str(work_id or "").strip()
            if resolved_work_id:
                await self._require_work(resolved_work_id)
            else:
                resolved_work_id = short_id8()
                await self._db.execute(
                    "INSERT INTO novel_source_works "
                    "(id, title, source_type, metadata_json) VALUES (?, ?, 'external_text', ?)",
                    [resolved_work_id, title, _json(source_metadata)],
                )
            revision = await self._insert_revision(
                work_id=resolved_work_id,
                sections=sections,
                content_digest=content_digest,
                byte_count=byte_count,
                character_count=character_count,
                source_metadata=source_metadata,
            )
            await self._db.execute(
                "UPDATE novel_source_works SET title = ?, update_time = CURRENT_TIMESTAMP "
                "WHERE id = ?",
                [title, resolved_work_id],
            )
        return revision

    async def freeze_book(self, book_id: str) -> dict[str, Any]:
        async with self._db.transaction(cancellation_linearizable=True):
            book = await self._db.fetch_one("SELECT * FROM books WHERE id = ?", [book_id])
            if book is None:
                raise NovelSourceNotFoundError("要冻结的作品不存在")
            chapters = await get_ordered_leaf_chapters(self._db, book_id)
            if not chapters:
                raise NovelSourceConflictError("作品没有可冻结的写作章节")
            texts = await load_chapter_texts(self._db, [item["id"] for item in chapters])
            sections = tuple(
                ParsedSourceSection(
                    ordinal=index,
                    title=str(chapter.get("title") or f"第 {index + 1} 章"),
                    text=str(texts.get(chapter["id"], "")),
                    start_character=0,
                    end_character=len(str(texts.get(chapter["id"], ""))),
                )
                for index, chapter in enumerate(chapters)
            )
            combined = "\n\n".join(section.text for section in sections)
            existing = await self._db.fetch_one(
                "SELECT id FROM novel_source_works WHERE source_type = 'frozen_book' "
                "AND origin_book_id = ? ORDER BY create_time LIMIT 1",
                [book_id],
            )
            work_id = str((existing or {}).get("id") or short_id8())
            if existing is None:
                await self._db.execute(
                    "INSERT INTO novel_source_works "
                    "(id, title, source_type, origin_book_id, metadata_json) "
                    "VALUES (?, ?, 'frozen_book', ?, ?)",
                    [work_id, book["title"], book_id, _json({"originBookId": book_id})],
                )
            revision = await self._insert_revision(
                work_id=work_id,
                sections=sections,
                content_digest=sha256_text(combined),
                byte_count=len(combined.encode("utf-8")),
                character_count=len(combined),
                source_metadata={"originBookId": book_id, "explicitFreeze": True},
            )
            await self._db.execute(
                "UPDATE novel_source_works SET title = ?, update_time = CURRENT_TIMESTAMP "
                "WHERE id = ?",
                [book["title"], work_id],
            )
        return revision

    async def get_revision(self, revision_id: str) -> dict[str, Any]:
        row = await self._db.fetch_one(
            "SELECT * FROM novel_source_revisions WHERE id = ?", [revision_id]
        )
        if row is None:
            raise NovelSourceNotFoundError("来源版本不存在")
        result = _revision_row(row)
        result["sections"] = [
            _section_row(item, include_text=False)
            for item in await self._db.fetch_all(
                "SELECT * FROM novel_source_sections WHERE revision_id = ? "
                "ORDER BY ordinal",
                [revision_id],
            )
        ]
        return result

    async def get_section(self, revision_id: str, section_id: str) -> dict[str, Any]:
        row = await self._db.fetch_one(
            "SELECT * FROM novel_source_sections WHERE id = ? AND revision_id = ?",
            [section_id, revision_id],
        )
        if row is None:
            raise NovelSourceNotFoundError("来源章节不存在")
        return _section_row(row, include_text=True)

    async def search_sections(
        self,
        revision_id: str,
        query: str,
        *,
        limit: int = 12,
    ) -> list[dict[str, Any]]:
        await self.get_revision(revision_id)
        normalized = str(query or "").strip()
        if not normalized:
            return []
        bounded_limit = max(1, min(int(limit), 30))
        try:
            rows = await self._db.fetch_all(
                "SELECT s.id, s.ordinal, s.title, "
                "snippet(novel_source_sections_fts, 3, '[', ']', '…', 24) AS excerpt "
                "FROM novel_source_sections_fts f "
                "JOIN novel_source_sections s ON s.id = f.section_id "
                "WHERE novel_source_sections_fts MATCH ? AND f.revision_id = ? "
                "ORDER BY rank LIMIT ?",
                [normalized, revision_id, bounded_limit],
            )
            if rows:
                return rows
        except Exception:
            pass
        pattern = f"%{normalized}%"
        rows = await self._db.fetch_all(
            "SELECT id, ordinal, title, text_content FROM novel_source_sections "
            "WHERE revision_id = ? AND (title LIKE ? OR text_content LIKE ?) "
            "ORDER BY ordinal LIMIT ?",
            [revision_id, pattern, pattern, bounded_limit],
        )
        return [{
            "id": row["id"],
            "ordinal": row["ordinal"],
            "title": row["title"],
            "excerpt": _excerpt(str(row["text_content"]), normalized),
        } for row in rows]

    async def archive_work(self, work_id: str) -> dict[str, Any]:
        await self._require_work(work_id)
        await self._db.execute(
            "UPDATE novel_source_works SET status = 'archived', "
            "update_time = CURRENT_TIMESTAMP WHERE id = ?",
            [work_id],
        )
        return await self.get_work(work_id)

    async def delete_revision(self, revision_id: str) -> None:
        async with self._db.transaction():
            revision = await self._require_revision(revision_id)
            referenced = await self._db.fetch_one(
                "SELECT 1 AS found FROM continuation_bindings WHERE source_revision_id = ? "
                "UNION SELECT 1 AS found FROM novel_source_analyses "
                "WHERE source_revision_id = ? LIMIT 1",
                [revision_id, revision_id],
            )
            if referenced:
                raise NovelSourceConflictError("来源版本已被分析或续写引用，不能删除")
            section_rows = await self._db.fetch_all(
                "SELECT id FROM novel_source_sections WHERE revision_id = ?", [revision_id]
            )
            for section in section_rows:
                try:
                    await self._db.execute(
                        "DELETE FROM novel_source_sections_fts WHERE section_id = ?",
                        [section["id"]],
                    )
                except Exception:
                    pass
            await self._db.execute(
                "DELETE FROM novel_source_sections WHERE revision_id = ?", [revision_id]
            )
            await self._db.execute(
                "DELETE FROM novel_source_revisions WHERE id = ?", [revision_id]
            )
            await self._db.execute(
                "UPDATE novel_source_works SET update_time = CURRENT_TIMESTAMP WHERE id = ?",
                [revision["work_id"]],
            )

    async def _insert_revision(
        self,
        *,
        work_id: str,
        sections: Sequence[ParsedSourceSection],
        content_digest: str,
        byte_count: int,
        character_count: int,
        source_metadata: Mapping[str, Any],
    ) -> dict[str, Any]:
        latest = await self._db.fetch_one(
            "SELECT COALESCE(MAX(version_no), 0) AS version_no "
            "FROM novel_source_revisions WHERE work_id = ?", [work_id]
        )
        version_no = int((latest or {}).get("version_no") or 0) + 1
        revision_id = short_id8()
        await self._db.execute(
            "INSERT INTO novel_source_revisions "
            "(id, work_id, version_no, content_digest, parser_version, "
            "source_metadata_json, byte_count, character_count) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [revision_id, work_id, version_no, content_digest, PARSER_VERSION,
             _json(source_metadata), byte_count, character_count],
        )
        for section in sections:
            section_id = short_id8()
            digest = sha256_text(section.text)
            locator = {
                "startCharacter": section.start_character,
                "endCharacter": section.end_character,
            }
            await self._db.execute(
                "INSERT INTO novel_source_sections "
                "(id, revision_id, ordinal, title, text_content, content_digest, locator_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [section_id, revision_id, section.ordinal, section.title,
                 section.text, digest, _json(locator)],
            )
            try:
                await self._db.execute(
                    "INSERT INTO novel_source_sections_fts "
                    "(section_id, revision_id, title, text_content) VALUES (?, ?, ?, ?)",
                    [section_id, revision_id, section.title, section.text],
                )
            except Exception:
                pass
        return await self.get_revision(revision_id)

    async def _require_work(self, work_id: str) -> dict[str, Any]:
        row = await self._db.fetch_one(
            "SELECT * FROM novel_source_works WHERE id = ?", [work_id]
        )
        if row is None:
            raise NovelSourceNotFoundError("来源作品不存在")
        return row

    async def _require_revision(self, revision_id: str) -> dict[str, Any]:
        row = await self._db.fetch_one(
            "SELECT * FROM novel_source_revisions WHERE id = ?", [revision_id]
        )
        if row is None:
            raise NovelSourceNotFoundError("来源版本不存在")
        return row


def _json(value: Mapping[str, Any]) -> str:
    return json.dumps(dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_object(value: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _work_row(row: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(row)
    result["metadata"] = _json_object(row.get("metadata_json"))
    return result


def _revision_row(row: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(row)
    result["source_metadata"] = _json_object(row.get("source_metadata_json"))
    return result


def _section_row(row: Mapping[str, Any], *, include_text: bool) -> dict[str, Any]:
    result = dict(row)
    result["locator"] = _json_object(row.get("locator_json"))
    if not include_text:
        result.pop("text_content", None)
    return result


def _excerpt(text: str, query: str, *, radius: int = 180) -> str:
    index = text.casefold().find(query.casefold())
    if index < 0:
        return text[: radius * 2]
    start = max(0, index - radius)
    end = min(len(text), index + len(query) + radius)
    return ("…" if start else "") + text[start:end] + ("…" if end < len(text) else "")


__all__ = ["SqliteNovelSourceRepository"]
