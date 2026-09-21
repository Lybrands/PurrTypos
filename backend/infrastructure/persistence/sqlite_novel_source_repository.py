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
            "CASE WHEN EXISTS(SELECT 1 FROM novel_source_analyses a "
            "JOIN novel_source_revisions ar ON ar.id = a.source_revision_id "
            "WHERE ar.work_id = w.id) THEN 1 ELSE 0 END AS analysis_count, "
            "(SELECT r.id FROM novel_source_revisions r WHERE r.work_id = w.id "
            "ORDER BY r.version_no DESC LIMIT 1) AS latest_revision_id "
            "FROM novel_source_works w "
            + ("" if include_archived else "WHERE w.status = 'active' ")
            + "ORDER BY w.update_time DESC, w.title ASC"
        )
        drafts = await self._db.fetch_all(
            "WITH RECURSIVE saved(id) AS ("
            "SELECT json_extract(summary_json, '$.artifactId') FROM novel_source_analyses "
            "UNION SELECT json_extract(a.metadata_json, '$.sourceArtifactId') "
            "FROM ai_agent_artifacts a JOIN saved s ON a.id=s.id "
            "WHERE json_extract(a.metadata_json, '$.sourceArtifactId') IS NOT NULL) "
            "SELECT r.work_id, r.id AS revision_id, a.id AS artifact_id "
            "FROM ai_agent_long_task_units u "
            "JOIN ai_agent_long_tasks t ON t.id=u.task_id "
            "JOIN novel_source_revisions r ON r.id=t.owner_id "
            "JOIN ai_agent_artifacts a ON u.output_ref='novel-analysis://' || a.id "
            "WHERE t.namespace='purrtypos.novel_analysis' AND t.status='completed' "
            "AND u.unit_id='artifact:review' AND u.status='completed' "
            "AND NOT EXISTS(SELECT 1 FROM saved WHERE saved.id=a.id) "
            "ORDER BY a.rowid DESC")
        by_work = {}
        for draft in drafts:
            by_work.setdefault(draft['work_id'], draft)
        return [{**_work_row(row),
            'unsaved_analysis_artifact_id': by_work.get(row['id'], {}).get('artifact_id'),
            'unsaved_analysis_revision_id': by_work.get(row['id'], {}).get('revision_id')}
            for row in rows]

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
                    volume_id=chapter.get("volume_id"),
                    volume_title=chapter.get("volume_title"),
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

    async def get_section(
        self,
        revision_id: str,
        section_id: str,
        *,
        start_character: int = 0,
        character_limit: int | None = None,
    ) -> dict[str, Any]:
        start = max(0, int(start_character))
        limit = (
            None
            if character_limit is None
            else max(1, min(int(character_limit), 100_000))
        )
        if limit is None:
            row = await self._db.fetch_one(
                "SELECT *, length(text_content) AS total_character_count "
                "FROM novel_source_sections WHERE id = ? AND revision_id = ?",
                [section_id, revision_id],
            )
        else:
            row = await self._db.fetch_one(
                "SELECT id, revision_id, ordinal, title, "
                "substr(text_content, ?, ?) AS text_content, content_digest, "
                "locator_json, length(text_content) AS total_character_count "
                "FROM novel_source_sections WHERE id = ? AND revision_id = ?",
                [start + 1, limit, section_id, revision_id],
            )
        if row is None:
            raise NovelSourceNotFoundError("来源章节不存在")
        result = _section_row(row, include_text=True)
        total = int(result.get("total_character_count") or 0)
        window_start = min(start, total) if limit is not None else 0
        window_end = min(total, window_start + len(str(result["text_content"])))
        result.update({
            "text_start_character": window_start,
            "text_end_character": window_end,
            "has_more_text": window_end < total,
        })
        return result

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
                "max(instr(lower(s.text_content), lower(?)) - 1, 0) "
                "AS start_character, "
                "snippet(novel_source_sections_fts, 3, '[', ']', '…', 24) AS excerpt "
                "FROM novel_source_sections_fts f "
                "JOIN novel_source_sections s ON s.id = f.section_id "
                "WHERE novel_source_sections_fts MATCH ? AND f.revision_id = ? "
                "ORDER BY rank LIMIT ?",
                [normalized, normalized, revision_id, bounded_limit],
            )
            if rows:
                return rows
        except Exception:
            pass
        pattern = f"%{normalized}%"
        rows = await self._db.fetch_all(
            "SELECT id, ordinal, title, text_content, "
            "max(instr(lower(text_content), lower(?)) - 1, 0) "
            "AS start_character FROM novel_source_sections "
            "WHERE revision_id = ? AND (title LIKE ? OR text_content LIKE ?) "
            "ORDER BY ordinal LIMIT ?",
            [normalized, revision_id, pattern, pattern, bounded_limit],
        )
        return [{
            "id": row["id"],
            "ordinal": row["ordinal"],
            "title": row["title"],
            "excerpt": _excerpt(str(row["text_content"]), normalized),
            "start_character": int(row["start_character"]),
        } for row in rows]

    async def archive_work(self, work_id: str) -> dict[str, Any]:
        await self._require_work(work_id)
        await self._db.execute(
            "UPDATE novel_source_works SET status = 'archived', "
            "update_time = CURRENT_TIMESTAMP WHERE id = ?",
            [work_id],
        )
        return await self.get_work(work_id)

    async def delete_work(self, work_id: str) -> None:
        async with self._db.transaction(cancellation_linearizable=True):
            await self._require_work(work_id)
            import asyncio
            from infrastructure.persistence.writing.technique_file_store import TechniqueFileStore
            revisions = await self._db.fetch_all("SELECT id FROM novel_source_revisions WHERE work_id=?", [work_id])
            evidence_referenced = await asyncio.to_thread(TechniqueFileStore(self._db.get_db_path().parent / "writing-library").references_sources, {row["id"] for row in revisions})
            if evidence_referenced:
                raise NovelSourceConflictError(
                    "来源已被写作技法引用，不能删除；可以将来源归档"
                )
            revisions = await self._db.fetch_all(
                "SELECT id FROM novel_source_revisions WHERE work_id = ?", [work_id]
            )
            for revision in revisions:
                revision_id = str(revision["id"])
                await self._db.execute(
                    "UPDATE ai_agent_runs SET cancel_requested_at_ms = CASE "
                    "WHEN status IN ('pending', 'queued', 'running', 'paused') "
                    "THEN COALESCE(cancel_requested_at_ms, "
                    "CAST(strftime('%s', 'now') AS INTEGER) * 1000) "
                    "ELSE cancel_requested_at_ms END, "
                    "status = CASE WHEN status IN ('pending', 'queued', 'running', 'paused') "
                    "THEN 'canceled' ELSE status END, execution_owner_id = NULL, "
                    "lease_expires_at_ms = NULL, heartbeat_at_ms = NULL, "
                    "update_time = CURRENT_TIMESTAMP "
                    "WHERE (binding_namespace = 'novel_source_analysis' "
                    "AND binding_aggregate_id = ?) OR id IN ("
                    "SELECT runs.run_id FROM ai_agent_long_task_runs AS runs "
                    "JOIN ai_agent_long_tasks AS tasks ON tasks.id = runs.task_id "
                    "WHERE tasks.namespace = 'purrtypos.novel_analysis' "
                    "AND tasks.owner_id = ?)",
                    [revision_id, revision_id],
                )
                for table in (
                    "ai_agent_long_task_usage",
                    "ai_agent_long_task_units",
                    "ai_agent_long_task_runs",
                ):
                    await self._db.execute(
                        f"DELETE FROM {table} WHERE task_id IN ("
                        "SELECT id FROM ai_agent_long_tasks "
                        "WHERE namespace = 'purrtypos.novel_analysis' AND owner_id = ?)",
                        [revision_id],
                    )
                await self._db.execute(
                    "DELETE FROM ai_agent_long_tasks "
                    "WHERE namespace = 'purrtypos.novel_analysis' AND owner_id = ?",
                    [revision_id],
                )
                for table in (
                    "ai_agent_artifact_projections",
                    "ai_agent_artifact_claims",
                    "ai_agent_artifact_batches",
                ):
                    await self._db.execute(
                        f"DELETE FROM {table} WHERE artifact_id IN ("
                        "SELECT id FROM ai_agent_artifacts "
                        "WHERE namespace = 'purrtypos.novel_analysis' AND owner_id = ?)",
                        [revision_id],
                    )
                await self._db.execute(
                    "DELETE FROM ai_agent_artifacts "
                    "WHERE namespace = 'purrtypos.novel_analysis' AND owner_id = ?",
                    [revision_id],
                )
            for table in (
                "novel_source_analysis_evidence",
                "novel_source_analysis_facts",
                "novel_source_craft_cards",
            ):
                await self._db.execute(
                    f"DELETE FROM {table} WHERE analysis_id IN ("
                    "SELECT a.id FROM novel_source_analyses AS a "
                    "JOIN novel_source_revisions AS r ON r.id = a.source_revision_id "
                    "WHERE r.work_id = ?)",
                    [work_id],
                )
            await self._db.execute(
                "DELETE FROM novel_source_analyses WHERE source_revision_id IN ("
                "SELECT id FROM novel_source_revisions WHERE work_id = ?)",
                [work_id],
            )
            section_rows = await self._db.fetch_all(
                "SELECT s.id FROM novel_source_sections AS s "
                "JOIN novel_source_revisions AS r ON r.id = s.revision_id "
                "WHERE r.work_id = ?",
                [work_id],
            )
            for section in section_rows:
                try:
                    await self._db.execute(
                        "DELETE FROM novel_source_sections_fts WHERE section_id = ?",
                        [section["id"]],
                    )
                except Exception:
                    pass
            for revision in revisions:
                await self._db.execute(
                    "DELETE FROM novel_source_section_token_metrics "
                    "WHERE source_revision_id = ?",
                    [revision["id"]],
                )
                await self._db.execute(
                    "DELETE FROM novel_source_sections WHERE revision_id = ?",
                    [revision["id"]],
                )
            await self._db.execute(
                "DELETE FROM novel_source_revisions WHERE work_id = ?", [work_id]
            )
            await self._db.execute(
                "DELETE FROM novel_source_works WHERE id = ?", [work_id]
            )

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
                "DELETE FROM novel_source_section_token_metrics "
                "WHERE source_revision_id = ?",
                [revision_id],
            )
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
                **({"volumeId": section.volume_id, "volumeTitle": section.volume_title} if section.volume_id else {}),
            }
            await self._db.execute(
                "INSERT INTO novel_source_sections "
                "(id, revision_id, ordinal, title, text_content, content_digest, "
                "locator_json, section_type, byte_count, character_count) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [section_id, revision_id, section.ordinal, section.title,
                 section.text, digest, _json(locator), section.section_type,
                 len(section.text.encode("utf-8")), len(section.text)],
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
