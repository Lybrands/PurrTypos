"""Canon preview and atomic continuation-book creation."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from uuid import uuid4

from constants import BOOK_COLORS
from application.canonical_json import canonical_json_digest
from exceptions import AppError, NotFoundError
from utils.id_utils import short_id8


CANON_FACT_KINDS = frozenset({
    "background",
    "character_summary", "relationship_summary",
    "story_summary",
    "setting",
    "location",
    "faction",
    "item",
    "character_identity",
    "character_state",
    "relationship",
    "world_rule",
    "event",
    "timeline",
    "unresolved_plot",
    "foreshadowing",
    "character_knowledge",
})


class ContinuationService:
    def __init__(self, db) -> None:
        self._db = db

    async def preview_canon(
        self,
        *,
        source_revision_id: str,
        source_analysis_id: str,
        fork_section_id: str,
    ) -> dict:
        return await self._preview_canon(
            source_revision_id=source_revision_id,
            source_analysis_id=source_analysis_id,
            fork_section_id=fork_section_id,
        )

    async def _preview_canon(
        self,
        *,
        source_revision_id: str,
        source_analysis_id: str,
        fork_section_id: str,
    ) -> dict:
        revision = await self._db.fetch_one(
            "SELECT r.*, w.id AS source_work_id, w.title AS source_title "
            "FROM novel_source_revisions AS r "
            "JOIN novel_source_works AS w ON w.id = r.work_id WHERE r.id = ?",
            [source_revision_id],
        )
        if revision is None:
            raise NotFoundError("来源版本不存在")
        analysis = await self._db.fetch_one(
            "SELECT * FROM novel_source_analyses WHERE id = ? "
            "AND source_revision_id = ?",
            [source_analysis_id, source_revision_id],
        )
        if analysis is None:
            raise AppError("正式来源分析与来源版本不匹配", 409)
        fork = await self._db.fetch_one(
            "SELECT id, revision_id, ordinal, title, content_digest, section_type "
            "FROM novel_source_sections WHERE id = ? AND revision_id = ?",
            [fork_section_id, source_revision_id],
        )
        if fork is None or fork["section_type"] != "chapter":
            raise AppError("分叉点必须是来源版本中的完整章节末尾", 409)
        fork_ordinal = int(fork["ordinal"])
        if fork_ordinal > int(analysis["coverage_end_ordinal"]):
            raise AppError("正式分析尚未覆盖所选分叉章节", 409)
        facts = await self._db.fetch_all(
            "SELECT * FROM novel_source_analysis_facts "
            "WHERE analysis_id = ? AND last_section_ordinal <= ? ORDER BY id",
            [source_analysis_id, fork_ordinal],
        )
        records: list[dict] = []
        for fact in facts:
            fact_kind = str(fact["fact_kind"])
            if fact_kind not in CANON_FACT_KINDS:
                continue
            evidence = await self._db.fetch_all(
                "SELECT e.section_id, e.excerpt_digest, e.excerpt, e.locator_json, s.ordinal, s.title "
                "FROM novel_source_analysis_evidence AS e "
                "JOIN novel_source_sections AS s ON s.id = e.section_id "
                "WHERE e.analysis_id = ? AND e.owner_type = 'fact' "
                "AND e.owner_id = ? AND s.revision_id = ? AND s.ordinal <= ? "
                "ORDER BY s.ordinal, e.id",
                [
                    source_analysis_id,
                    fact["id"],
                    source_revision_id,
                    fork_ordinal,
                ],
            )
            if not evidence:
                raise AppError("正史事实缺少分叉点以前的合法证据", 409)
            records.append({
                "sourceFactId": str(fact["id"]),
                "factKind": fact_kind,
                "claimNature": fact.get("claim_nature") or "fact",
                "subjectKey": str(fact["subject_key"]),
                "predicate": str(fact["predicate"]),
                "value": json.loads(str(fact["value_json"])),
                "lifecycleStatus": str(fact.get("lifecycle_status") or "active"),
                "contentDigest": str(fact["content_digest"]),
                "evidence": [{
                    "sectionId": str(item["section_id"]),
                    "sectionOrdinal": int(item["ordinal"]),
                    "excerptDigest": str(item["excerpt_digest"]),
                    "sectionTitle": str(item["title"]),
                    "excerpt": str(item["excerpt"]),
                    "referenceKind": json.loads(item["locator_json"]).get("referenceKind", "quote"),
                } for item in evidence],
            })
        from application.source_analysis_techniques import results
        techniques = await results(self._db, source_analysis_id, fork_ordinal, include_candidates=True)
        sections = await self._db.fetch_all(
            "SELECT id,ordinal,section_type,title,content_digest,locator_json FROM novel_source_sections WHERE revision_id=? AND ordinal<=? ORDER BY ordinal",
            [source_revision_id, fork_ordinal])
        canonical = {
            "sourceRevisionId": source_revision_id,
            "sourceAnalysisId": source_analysis_id,
            "forkSectionId": fork_section_id,
            "forkOrdinal": fork_ordinal,
            "records": records,
            "startingPoint": {
                "forkSectionId": fork_section_id,
                "sourceFactIds": [record["sourceFactId"] for record in records if record["factKind"] in {
                    "character_state", "character_knowledge", "event", "timeline", "unresolved_plot", "foreshadowing"
                }],
            },
            "sections": sections,
            "techniques": techniques,
            "defaultTechniques": [item["ref"] for item in techniques if item["available"]],
            "materialMapping": [{"sourceFactId": r["sourceFactId"], "sourceKey": r["subjectKey"],
                "kind": "character" if r["factKind"] in {"character_identity", "character_state", "character_knowledge", "relationship", "character_summary", "relationship_summary"} else "background" if r["factKind"] in {"background", "story_summary"} else "plot" if r["factKind"] in {"event", "timeline", "unresolved_plot", "foreshadowing"} else "entity"} for r in records],
        }
        return {
            **canonical,
            "sourceWorkId": str(revision["source_work_id"]),
            "sourceTitle": str(revision["source_title"]),
            "sourceVersionNo": int(revision["version_no"]),
            "forkSectionTitle": str(fork["title"]),
            "snapshotDigest": canonical_json_digest(canonical),
        }

    async def create_continuation(
        self,
        *,
        title: str,
        source_revision_id: str,
        source_analysis_id: str,
        fork_section_id: str,
        expected_snapshot_digest: str,
        enable_volume: bool = False,
        operation_id: str,
        use_source_techniques: bool = True,
    ) -> dict:
        if not str(operation_id or "").strip():
            raise AppError("创建需要稳定的 operationId", 422)
        normalized_title = str(title or "").strip()
        if not normalized_title:
            raise AppError("续写作品名称不能为空", 422)
        request_digest = canonical_json_digest({"title": normalized_title, "revision": source_revision_id,
            "analysis": source_analysis_id, "fork": fork_section_id, "digest": expected_snapshot_digest,
            "volume": enable_volume, "useSourceTechniques": use_source_techniques})
        async with self._db.transaction(cancellation_linearizable=True):
            previous = await self._db.fetch_one("SELECT * FROM continuation_operations WHERE operation_id=?", [operation_id])
            if previous:
                if previous["request_digest"] != request_digest:
                    raise AppError("同一创建操作不能修改继承清单", 409)
                return await self.get_continuation(previous["book_id"])
            preview = await self._preview_canon(
                source_revision_id=source_revision_id,
                source_analysis_id=source_analysis_id,
                fork_section_id=fork_section_id,
            )
            if preview["snapshotDigest"] != str(expected_snapshot_digest or "").strip():
                raise AppError("正史预览已变化，请重新确认", 409)
            from application.writing_technique_service import WritingTechniqueService
            library = WritingTechniqueService(self._db)
            selected = []
            for item in preview["techniques"] if use_source_techniques else []:
                if not item["available"]:
                    continue
                ref = item["ref"]
                if item["stage"] == "candidate":
                    # Copy the frozen candidate: do not publish/mutate the source
                    # result or change its preview digest. File operations replay
                    # deterministically if the surrounding SQL transaction fails.
                    token = f"continuation:{operation_id}:{ref['id']}:{ref['versionId']}"
                    draft = await library.create_draft(operation_id=token + ":copy", from_version=ref,
                        owner={"sourceAnalysisId": source_analysis_id, "sourceRevisionId": source_revision_id, "sourceRef": ref})
                    sealed = await library.seal("technique", draft["techniqueId"], draft["draftId"],
                        expected_revision=0, expected_tree_digest=draft["treeDigest"], operation_id=token + ":seal")
                    ref = sealed["sealedRef"]
                    await library.publish("technique", ref["id"], ref=ref,
                        expected_published_head=None, operation_id=token + ":publish")
                selected.append(ref)
            preview = {**preview, "defaultTechniques": selected, "useSourceTechniques": use_source_techniques}
            count = await self._db.fetch_one("SELECT COUNT(*) AS count FROM books")
            color = BOOK_COLORS[int((count or {}).get("count") or 0) % len(BOOK_COLORS)]
            book_id = short_id8()
            snapshot_id = f"canon_{uuid4().hex}"
            binding_id = f"continuation_{uuid4().hex}"
            await self._db.execute(
                "INSERT INTO continuation_canon_snapshots "
                "(id, source_revision_id, source_analysis_id, fork_section_id, "
                "fork_ordinal, content_digest) VALUES (?, ?, ?, ?, ?, ?)",
                [
                    snapshot_id,
                    source_revision_id,
                    source_analysis_id,
                    fork_section_id,
                    preview["forkOrdinal"],
                    preview["snapshotDigest"],
                ],
            )
            for record in preview["records"]:
                await self._db.execute(
                    "INSERT INTO continuation_canon_records "
                    "(id, snapshot_id, source_fact_id, fact_kind, subject_key, "
                    "predicate, value_json, content_digest) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        f"canon_record_{uuid4().hex}",
                        snapshot_id,
                        record["sourceFactId"],
                        record["factKind"],
                        record["subjectKey"],
                        record["predicate"],
                        json.dumps(record["value"], ensure_ascii=False, separators=(",", ":")),
                        record["contentDigest"],
                    ],
                )
            await self._db.execute(
                "INSERT INTO books "
                "(id, title, cover_color, enable_volume, creation_mode) "
                "VALUES (?, ?, ?, ?, 'continuation')",
                [book_id, normalized_title, color, int(bool(enable_volume))],
            )
            await self._db.execute(
                "INSERT INTO outlines (id, title, type, sort, book_id) "
                "VALUES (?, ?, 'writing', 0, ?)",
                [short_id8(), normalized_title, book_id],
            )
            binding_canonical = {
                "targetBookId": book_id,
                "sourceWorkId": preview["sourceWorkId"],
                "sourceRevisionId": source_revision_id,
                "forkSectionId": fork_section_id,
                "forkOrdinal": preview["forkOrdinal"],
                "canonSnapshotId": snapshot_id,
            }
            await self._db.execute(
                "INSERT INTO continuation_bindings "
                "(id, target_book_id, source_work_id, source_revision_id, "
                "fork_section_id, fork_ordinal, canon_snapshot_id, binding_digest) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    binding_id,
                    book_id,
                    preview["sourceWorkId"],
                    source_revision_id,
                    fork_section_id,
                    preview["forkOrdinal"],
                    snapshot_id,
                    canonical_json_digest(binding_canonical),
                ],
            )
            await self._db.execute(
                "INSERT INTO continuation_source_sections SELECT ?,id,revision_id,ordinal,section_type,title,text_content,content_digest,locator_json FROM novel_source_sections WHERE revision_id=? AND ordinal<=?",
                [book_id, source_revision_id, preview["forkOrdinal"]])
            await self._db.execute("INSERT INTO continuation_operations VALUES (?,?,?,?)",
                [operation_id, request_digest, book_id, json.dumps(preview, ensure_ascii=False)])
            from application.writing_technique_access import WritingTechniqueAccess
            access = WritingTechniqueAccess(self._db)
            for ref in preview["defaultTechniques"]:
                await access.grant(book_id, ref)
            from application.writing_technique_service import WritingTechniqueService
            await WritingTechniqueService(self._db).set_selection("book", book_id, preview["defaultTechniques"])
            from application.continuation_materials import initialize_materials
            await initialize_materials(self._db, book_id, preview)
        return await self.get_continuation(book_id)

    async def get_continuation(self, book_id: str) -> dict:
        row = await self._db.fetch_one(
            "SELECT b.*, cb.id AS continuation_binding_id, cb.source_work_id, "
            "cb.source_revision_id, cb.fork_section_id, cb.fork_ordinal, "
            "cb.canon_snapshot_id, cb.binding_digest, sw.title AS source_title, "
            "ss.title AS fork_section_title, cs.source_analysis_id, "
            "cs.content_digest AS canon_snapshot_digest "
            "FROM books AS b JOIN continuation_bindings AS cb "
            "ON cb.target_book_id = b.id LEFT JOIN novel_source_works AS sw "
            "ON sw.id = cb.source_work_id LEFT JOIN novel_source_sections AS ss "
            "ON ss.id = cb.fork_section_id JOIN continuation_canon_snapshots AS cs "
            "ON cs.id = cb.canon_snapshot_id WHERE b.id = ? "
            "AND b.creation_mode = 'continuation'",
            [book_id],
        )
        if row is None:
            raise NotFoundError("续写作品不存在")
        records = await self._db.fetch_all(
            "SELECT * FROM continuation_canon_records WHERE snapshot_id = ? ORDER BY id",
            [row["canon_snapshot_id"]],
        )
        operation = await self._db.fetch_one("SELECT manifest_json FROM continuation_operations WHERE book_id=?", [book_id])
        manifest = json.loads(operation["manifest_json"]) if operation else None
        return {
            "inheritance": manifest,
            "book": {
                key: row[key]
                for key in (
                    "id", "title", "cover_color", "enable_volume",
                    "creation_mode", "create_time",
                )
            },
            "binding": {
                "id": row["continuation_binding_id"],
                "sourceWorkId": row["source_work_id"],
                "sourceRevisionId": row["source_revision_id"],
                "sourceAnalysisId": row["source_analysis_id"],
                "sourceTitle": row["source_title"] or (manifest or {}).get("sourceTitle", "已删除来源"),
                "forkSectionId": row["fork_section_id"],
                "forkSectionTitle": row["fork_section_title"] or (manifest or {}).get("forkSectionTitle", "原分叉章节已删除"),
                "forkOrdinal": row["fork_ordinal"],
                "canonSnapshotId": row["canon_snapshot_id"],
                "canonSnapshotDigest": row["canon_snapshot_digest"],
                "bindingDigest": row["binding_digest"],
            },
            "canonRecords": [{
                "id": item["id"],
                "sourceFactId": item["source_fact_id"],
                "factKind": item["fact_kind"],
                "subjectKey": item["subject_key"],
                "predicate": item["predicate"],
                "value": json.loads(str(item["value_json"])),
                "contentDigest": item["content_digest"],
            } for item in records],
        }


__all__ = ["CANON_FACT_KINDS", "ContinuationService"]
