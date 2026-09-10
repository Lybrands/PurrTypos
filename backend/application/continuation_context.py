"""Server-owned continuation binding and immutable canon lookup."""

from __future__ import annotations

import json

from exceptions import AppError, NotFoundError


class ContinuationContextService:
    def __init__(self, db) -> None:
        self._db = db

    async def load_for_writing(self, book_id: str) -> dict:
        """Read continuation tables only after the book declares that mode."""

        book = await self._db.fetch_one(
            "SELECT id, creation_mode FROM books WHERE id = ?",
            [book_id],
        )
        if book is None:
            # Existing unscoped/model-only requests may carry a stale book id.
            # They retain the legacy original path; every concrete write tool
            # still verifies the target book before mutation.
            return {"creationMode": "original", "binding": None, "canonRecords": []}
        mode = str(book.get("creation_mode") or "original")
        if mode != "continuation":
            return {"creationMode": "original", "binding": None, "canonRecords": []}

        row = await self._db.fetch_one(
            "SELECT cb.*, cs.source_analysis_id, "
            "cs.content_digest AS canon_snapshot_digest, sw.title AS source_title, "
            "ss.title AS fork_section_title "
            "FROM continuation_bindings AS cb "
            "JOIN continuation_canon_snapshots AS cs ON cs.id = cb.canon_snapshot_id "
            "LEFT JOIN novel_source_works AS sw ON sw.id = cb.source_work_id "
            "LEFT JOIN novel_source_sections AS ss ON ss.id = cb.fork_section_id "
            "WHERE cb.target_book_id = ?",
            [book_id],
        )
        if row is None:
            raise AppError("续写作品缺少冻结绑定，禁止启动写作运行", 409)
        records = await self._db.fetch_all(
            "SELECT * FROM continuation_canon_records WHERE snapshot_id = ? ORDER BY id",
            [row["canon_snapshot_id"]],
        )
        operation = await self._db.fetch_one("SELECT manifest_json FROM continuation_operations WHERE book_id=?", [book_id])
        manifest = json.loads(operation["manifest_json"]) if operation else {}
        binding = {
            "materialAuthority": "shared_markdown" if operation else "canon_snapshot",
            "id": str(row["id"]),
            "targetBookId": book_id,
            "sourceWorkId": str(row["source_work_id"]),
            "sourceRevisionId": str(row["source_revision_id"]),
            "sourceAnalysisId": str(row["source_analysis_id"]),
            "sourceTitle": str(row["source_title"] or manifest.get("sourceTitle", "已删除来源")),
            "forkSectionId": str(row["fork_section_id"]),
            "forkSectionTitle": str(row["fork_section_title"] or manifest.get("forkSectionTitle", "原分叉章节已删除")),
            "forkOrdinal": int(row["fork_ordinal"]),
            "canonSnapshotId": str(row["canon_snapshot_id"]),
            "canonSnapshotDigest": str(row["canon_snapshot_digest"]),
            "bindingDigest": str(row["binding_digest"]),
        }
        return {
            "creationMode": "continuation",
            "binding": binding,
            "canonRecords": [
                {
                    "id": str(item["id"]),
                    "sourceFactId": str(item["source_fact_id"]),
                    "factKind": str(item["fact_kind"]),
                    "subjectKey": str(item["subject_key"]),
                    "predicate": str(item["predicate"]),
                    "value": json.loads(str(item["value_json"])),
                    "contentDigest": str(item["content_digest"]),
                }
                for item in records
            ],
        }

    async def list_source_sections(self, *, book_id: str, offset: int = 0, limit: int = 100):
        context = await self.load_for_writing(book_id)
        binding = context.get("binding")
        if not binding:
            return {"items": [], "total": 0, "chapterCount": 0, "nextOffset": None}
        if offset < 0 or not 1 <= limit <= 200:
            raise AppError("无效的目录分页范围", 422)
        frozen = await self._db.fetch_one("SELECT book_id FROM continuation_operations WHERE book_id=?", [book_id])
        table = "continuation_source_sections" if frozen else "novel_source_sections"
        scope = "book_id=?" if frozen else "revision_id=? AND ordinal<=?"
        params = [book_id] if frozen else [binding["sourceRevisionId"], binding["forkOrdinal"]]
        rows = await self._db.fetch_all(f"SELECT id,title,ordinal,section_type,locator_json FROM {table} WHERE {scope} ORDER BY ordinal LIMIT ? OFFSET ?", [*params, limit, offset])
        count = await self._db.fetch_one(f"SELECT COUNT(*) AS n FROM {table} WHERE {scope}", params)
        chapters = await self._db.fetch_one(f"SELECT COUNT(*) AS n FROM {table} WHERE {scope} AND section_type='chapter'", params)
        return {"items": [{"nodeKind": "source_section", "sectionId": row["id"], "title": row["title"], "ordinal": row["ordinal"], "sectionType": row["section_type"], "locator": json.loads(row["locator_json"]), "readOnly": True} for row in rows],
                "total": count["n"], "chapterCount": chapters["n"], "nextOffset": offset + len(rows) if offset + len(rows) < count["n"] else None}

    async def read_source_section(self, *, book_id: str, section_id: str) -> dict:
        context = await self.load_for_writing(book_id)
        binding = context.get("binding")
        if context["creationMode"] != "continuation" or not isinstance(binding, dict):
            raise AppError("只有续写作品可以读取冻结来源", 409)
        frozen = await self._db.fetch_one("SELECT book_id FROM continuation_operations WHERE book_id=?", [book_id])
        if frozen:
            section = await self._db.fetch_one("SELECT * FROM continuation_source_sections WHERE book_id=? AND id=?", [book_id, section_id])
        else:
            section = await self._db.fetch_one(
            "SELECT id, revision_id, ordinal, title, text_content, content_digest "
            "FROM novel_source_sections WHERE id = ? AND revision_id = ?",
            [section_id, binding["sourceRevisionId"]],
        )
        if section is None:
            raise NotFoundError("来源章节不存在、不属于冻结版本或位于分叉点后")
        if int(section["ordinal"]) > int(binding["forkOrdinal"]):
            raise AppError("分叉点后的来源章节禁止被续写运行读取", 403)
        return {
            "sectionId": str(section["id"]),
            "title": str(section["title"]),
            "ordinal": int(section["ordinal"]),
            "text": str(section["text_content"]),
            "contentDigest": str(section["content_digest"]),
            "sourceRevisionId": str(binding["sourceRevisionId"]),
            "canonSnapshotId": str(binding["canonSnapshotId"]),
        }


__all__ = ["ContinuationContextService"]
