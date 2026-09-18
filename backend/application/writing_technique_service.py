"""Unified file-backed commands for user and Agent writing-technique editing."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from domains.writing.techniques import TechniqueError, canonical_bytes
from infrastructure.persistence.writing.technique_file_store import TechniqueFileStore
from infrastructure.persistence.writing.scheme_file_store import SchemeFileStore
from infrastructure.persistence.writing.skill_file_store import SkillFileStore


async def _file_commit(operation, *args, **kwargs):
    task = asyncio.create_task(asyncio.to_thread(operation, *args, **kwargs))
    while True:
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.done():
                return task.result()


class WritingTechniqueService:
    def __init__(self, db, *, root: Path | None = None):
        self.db = db
        self.root = root or db.get_db_path().parent / "writing-library"
        self.techniques = TechniqueFileStore(self.root)
        self.schemes = SchemeFileStore(self.root)
        self.skills = SkillFileStore(self.root)

    def store(self, kind: str):
        if kind not in {"technique", "scheme", "skill"}:
            raise TechniqueError("invalid_reference", "无效的写作技法对象类型")
        return {"technique": self.techniques, "scheme": self.schemes, "skill": self.skills}[kind]

    async def rebuild_index(self):
        records = await asyncio.to_thread(self._all_records)
        async with self.db.transaction():
            await self.db.execute("DELETE FROM writing_technique_catalog")
            for record in records:
                await self.db.execute("INSERT INTO writing_technique_catalog(kind,id,record_json) VALUES (?,?,?)",
                    [record["kind"], record["id"], canonical_bytes(record).decode()])
        return records

    def _all_records(self):
        return (self.techniques.list_records(include_archived=True)
                + self.schemes.list_records(include_archived=True)
                + self.skills.list_records(include_archived=True))

    async def _index(self, kind: str, object_id: str):
        record = await asyncio.to_thread(self.store(kind).get_record, object_id)
        if record["storageScope"] == "library":
            await self.db.execute("INSERT INTO writing_technique_catalog(kind,id,record_json) VALUES (?,?,?) "
                "ON CONFLICT(kind,id) DO UPDATE SET record_json=excluded.record_json",
                [kind, object_id, canonical_bytes(record).decode()])

    async def list_objects(self, kind: str, *, include_archived=False):
        # Canonical heads remain authoritative if an index update was interrupted.
        records = await asyncio.to_thread(self.store(kind).list_records, include_archived=include_archived)
        for record in records:
            draft = await asyncio.to_thread(self.store(kind).get_draft, record["id"], record["draftHead"])
            if not record.get("metadata"):
                record["metadata"] = draft.get("manifest", {}).get("metadata") if kind in {"technique", "skill"} else {
                    key: draft["content"].get(key, "") for key in ("name", "description")}
        return records

    async def get_object(self, kind: str, object_id: str):
        store = self.store(kind)
        record = await asyncio.to_thread(store.get_record, object_id)
        record["draft"] = await asyncio.to_thread(store.get_draft, object_id, record["draftHead"])
        return record

    async def create_draft(self, *, kind="technique", **values):
        store = self.store(kind)
        if kind in {"technique", "skill"} and values.get("from_version") and values.get("owner") is None:
            ref = values["from_version"]
            record = await asyncio.to_thread(store.get_record, ref["id"])
            for draft_id in record["draftIds"]:
                draft = await asyncio.to_thread(store.get_draft, ref["id"], draft_id)
                if draft.get("sealedRef") == ref:
                    values["owner"] = draft.get("owner", {})
                    break
        # 内置技法只能由种子通道（internal=True）重新起草新版本。
        if not values.pop("internal", False) and kind in {"technique", "skill"} and values.get("technique_id"):
            await self._require_not_builtin(kind, values["technique_id"])
        result = await _file_commit(store.create_draft if kind in {"technique", "skill"} else store.create_scheme_draft, **values)
        await self._index(kind, result["techniqueId" if kind != "scheme" else "schemeId"])
        return result

    async def _require_not_builtin(self, kind: str, object_id: str) -> None:
        try:
            record = await asyncio.to_thread(self.store(kind).get_record, object_id)
        except TechniqueError:
            return
        if record.get("origin") == "builtin":
            raise TechniqueError("invalid_reference", "内置技法由应用统一管理，不能编辑、发布或删除")

    async def references_sources(self, revision_ids: set[str]) -> bool:
        return await asyncio.to_thread(self.techniques.references_sources, revision_ids)

    async def apply_changes(self, object_id: str, draft_id: str, *, kind="technique", **values):
        if not values.pop("internal", False):
            await self._require_not_builtin(kind, object_id)
        result = await _file_commit(self.store(kind).apply_changes, object_id, draft_id, **values)
        await self._index(kind, object_id)
        return result

    async def update_scheme(self, object_id: str, draft_id: str, **values):
        result = await _file_commit(self.schemes.update_scheme_draft, object_id, draft_id, **values)
        await self._index("scheme", object_id)
        return result

    async def seal(self, kind: str, object_id: str, draft_id: str, **values):
        store = self.store(kind)
        if not values.pop("internal", False):
            await self._require_not_builtin(kind, object_id)
        result = await _file_commit(store.seal_draft if kind in {"technique", "skill"} else store.seal_scheme_draft,
                                         object_id, draft_id, **values)
        await self._index(kind, object_id)
        return result

    async def publish(self, kind: str, object_id: str, **values):
        if not values.pop("internal", False):
            await self._require_not_builtin(kind, object_id)
        result = await _file_commit(self.store(kind).publish, object_id, **values)
        await self._index(kind, object_id)
        if kind == "technique":
            from application.source_analysis_techniques import register_published
            await register_published(self, object_id)
        return result

    async def set_status(self, kind: str, object_id: str, status: str, *, operation_id: str):
        await self._require_not_builtin(kind, object_id)
        if status == "archived":
            await self._require_unreferenced_continuation(kind, object_id)
        result = await _file_commit(self.store(kind).set_status, object_id, status, operation_id=operation_id)
        await self._index(kind, object_id)
        from application.writing_technique_lifecycle import cancel_invalid_technique_runs
        await cancel_invalid_technique_runs(self.db)
        return result

    async def deletion_preview(self, kind: str, object_id: str):
        from application.writing_technique_deletion import deletion_preview
        return await asyncio.to_thread(deletion_preview, self, kind, object_id)

    async def delete_object(self, kind: str, object_id: str, *, operation_id: str, revision_token: str):
        await self._require_not_builtin(kind, object_id)
        await self._require_unreferenced_continuation(kind, object_id)
        from application.writing_technique_deletion import delete_files
        from application.writing_technique_lifecycle import cancel_invalid_technique_runs
        await cancel_invalid_technique_runs(self.db)
        result = await _file_commit(delete_files, self, kind, object_id,
                                    operation_id=operation_id, revision_token=revision_token)
        async with self.db.transaction():
            await self.db.execute('DELETE FROM writing_technique_catalog WHERE kind=? AND id=?', [kind, object_id])
            await self.db.execute('UPDATE writing_technique_grants SET active=0,generation=generation+1 WHERE kind=? AND object_id=? AND active=1', [kind, object_id])
        return result

    async def read_draft_file(self, object_id: str, draft_id: str, revision: int, path: str):
        return await asyncio.to_thread(self.techniques.read_draft_file, object_id, draft_id, revision, path)

    async def read_version_file(self, ref: dict, path: str):
        return await asyncio.to_thread(self.store(ref.get("kind", "technique")).read_version_file, ref, path)

    async def get_mode(self, scope_kind: str, scope_id: str) -> str:
        row = await self.db.fetch_one("SELECT mode FROM writing_technique_modes WHERE scope_kind=? AND scope_id=?", [scope_kind, scope_id])
        return row["mode"] if row else "manual"

    async def initialize_session(self, session_id: str, book_id: str) -> str:
        refs = await self.get_selection("book", book_id)
        await self.db.execute("INSERT OR IGNORE INTO writing_technique_selections VALUES ('session',?,?)", [session_id, json.dumps(refs)])
        mode = await self.get_mode("book", book_id)
        await self.db.execute("INSERT OR IGNORE INTO writing_technique_modes(scope_kind,scope_id,mode) VALUES ('session',?,?)", [session_id, mode])
        return await self.get_mode("session", session_id)

    async def set_mode(self, scope_kind: str, scope_id: str, mode: str):
        if scope_kind not in {"book", "session"} or mode not in {"manual", "auto"}:
            raise TechniqueError("invalid_reference", "无效的写作技法使用模式")
        await self.db.execute("INSERT INTO writing_technique_modes(scope_kind,scope_id,mode) VALUES (?,?,?) "
            "ON CONFLICT(scope_kind,scope_id) DO UPDATE SET mode=excluded.mode", [scope_kind, scope_id, mode])
        return {"mode": mode}

    async def get_selection(self, scope_kind, scope_id):
        row = await self.db.fetch_one("SELECT refs_json FROM writing_technique_selections WHERE scope_kind=? AND scope_id=?", [scope_kind, scope_id])
        return json.loads(row["refs_json"]) if row else []

    async def set_selection(self, scope_kind, scope_id, refs):
        from application.writing_technique_access import WritingTechniqueAccess
        if scope_kind == "session":
            session = await self.db.fetch_one("SELECT book_id FROM ai_sessions WHERE id=?", [scope_id])
            if not session:
                raise TechniqueError("invalid_reference", "会话不存在")
            book_id = str(session["book_id"])
        else:
            book_id = scope_id
        await WritingTechniqueAccess(self.db).freeze(book_id=book_id, manual=refs)
        await self.db.execute("INSERT INTO writing_technique_selections VALUES (?,?,?) ON CONFLICT(scope_kind,scope_id) DO UPDATE SET refs_json=excluded.refs_json", [scope_kind, scope_id, json.dumps(refs)])
        return refs

    async def _require_unreferenced_continuation(self, kind, object_id):
        rows = await self.db.fetch_all("SELECT manifest_json FROM continuation_operations")
        if any(any(ref["kind"] == kind and ref["id"] == object_id for ref in json.loads(row["manifest_json"])["defaultTechniques"]) for row in rows):
            raise TechniqueError("invalid_reference", "此技法被续写继承快照引用，不能归档或删除；已有续写可在会话中取消使用")
