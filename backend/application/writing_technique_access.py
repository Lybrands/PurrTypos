"""Exact-version admission, grant generations and entry-first execution reads."""

from __future__ import annotations

import asyncio
import copy
import json
import uuid

from application.writing_technique_service import WritingTechniqueService
from domains.writing.techniques import TechniqueError, canonical_bytes, digest


class WritingTechniqueAccess:
    def __init__(self, db):
        self.db = db
        self._library = None

    @property
    def library(self):
        if self._library is None:
            self._library = WritingTechniqueService(self.db)
        return self._library

    async def reserve_input(self, *, operation_id: str, book_id: str, session_id: str, mode: str, manual: list[dict] | None):
        request_digest = digest(canonical_bytes({"bookId": book_id, "sessionId": session_id, "mode": mode, "manual": manual}))
        input_id = "input_" + uuid.uuid5(uuid.NAMESPACE_URL, "technique-input:" + operation_id).hex
        async with self.db.transaction():
            previous = await self.db.fetch_one("SELECT * FROM writing_technique_request_inputs WHERE id=?", [input_id])
            if previous:
                if previous["request_digest"] != request_digest:
                    raise TechniqueError("operation_conflict", "同一输入操作不能修改已冻结的技法选择")
                return {"inputId": input_id, "mode": mode}
            session = await self.db.fetch_one("SELECT book_id FROM ai_sessions WHERE id=?", [session_id])
            if not session or str(session["book_id"]) != book_id:
                raise TechniqueError("invalid_reference", "写作会话不属于当前小说")
            effective_manual = manual if manual is not None else await self.library.get_selection("session", session_id)
            snapshot = await self.freeze(book_id=book_id, mode=mode, manual=effective_manual, input_session_id=session_id)
            await self.db.execute("INSERT INTO writing_technique_request_inputs(id,book_id,session_id,request_digest,snapshot_json) VALUES (?,?,?,?,?)",
                [input_id, book_id, session_id, request_digest, canonical_bytes(snapshot).decode()])
        return {"inputId": input_id, "mode": mode}

    async def load_input(self, input_id: str, book_id: str, session_id: str):
        row = await self.db.fetch_one("SELECT * FROM writing_technique_request_inputs WHERE id=? AND book_id=? AND session_id=?", [input_id, book_id, session_id])
        if not row:
            raise TechniqueError("authorization_revoked", "本轮技法选择不存在或不属于当前会话")
        snapshot = json.loads(row["snapshot_json"])
        await self.validate(snapshot, selected=snapshot["manual"])
        return snapshot

    async def _expand(self, ref: dict, *, verify_files: bool = False, input_session_id: str | None = None) -> dict:
        store = self.library.store(ref.get("kind"))
        record = await asyncio.to_thread(store.get_record, ref.get("id"))
        upload = None
        if record["storageScope"] == "run_input" and input_session_id is not None:
            upload = await self.db.fetch_one("SELECT * FROM writing_technique_inputs WHERE id=? AND session_id=? AND removed=0", [ref["id"], input_session_id])
            if upload and json.loads(upload["ref_json"]) != ref:
                upload = None
        if record["status"] != "active" or (not upload and (record["storageScope"] != "library" or ref.get("versionId") not in record.get("publishedVersions", []))):
            raise TechniqueError("invalid_reference", "请选择活动库中的已发布技法或方案版本")
        if ref["kind"] == "technique":
            manifest = await asyncio.to_thread(store.get_version_manifest, ref, verify_files=verify_files)
            members, composition, metadata = [ref], "", manifest["metadata"]
        else:
            scheme = await asyncio.to_thread(store.read_scheme, ref, verify_members=verify_files)
            members, composition = scheme["members"], scheme["composition"]
            metadata = {key: scheme[key] for key in ("name", "description")}
        epochs, entry_bytes = {}, 0
        for member in members:
            member_record = await asyncio.to_thread(self.library.techniques.get_record, member["id"])
            if member_record["status"] != "active" or (not upload and member["versionId"] not in member_record.get("publishedVersions", [])):
                raise TechniqueError("invalid_reference", "方案包含不可用的技法版本")
            epochs[member["id"]] = member_record.get("statusGeneration", 0)
            manifest = await asyncio.to_thread(self.library.techniques.get_version_manifest, member)
            entry_bytes += next(item["size"] for item in manifest["files"] if item["path"] == "SKILL.md")
        return {"ref": copy.deepcopy(ref), "members": members, "composition": composition,
                "metadata": metadata, "statusGeneration": record.get("statusGeneration", 0), "memberEpochs": epochs, "entryBytes": entry_bytes,
                **({"inputSessionId": input_session_id} if upload else {})}

    async def grant(self, book_id: str, ref: dict) -> dict:
        if not await self.db.fetch_one("SELECT id FROM books WHERE id=?", [book_id]):
            raise TechniqueError("invalid_reference", "小说不存在")
        expanded = await self._expand(ref)
        async with self.db.transaction():
            existing = await self.db.fetch_one("SELECT * FROM writing_technique_grants WHERE book_id=? AND kind=? AND object_id=? AND version_id=?",
                [book_id, ref["kind"], ref["id"], ref["versionId"]])
            grant_id = existing["id"] if existing else "grant_" + uuid.uuid4().hex
            if existing and existing["active"] and existing["status_generation"] == expanded["statusGeneration"] and json.loads(existing["member_epochs_json"]) == expanded["memberEpochs"]:
                return {"id": grant_id, "grantId": grant_id, "generation": existing["generation"], "ref": ref, "metadata": expanded["metadata"]}
            generation = existing["generation"] + 1 if existing else 1
            await self.db.execute("INSERT INTO writing_technique_grants(id,book_id,kind,object_id,version_id,generation,active,status_generation,member_epochs_json) "
                "VALUES (?,?,?,?,?,?,1,?,?) ON CONFLICT(id) DO UPDATE SET generation=excluded.generation,active=1,status_generation=excluded.status_generation,member_epochs_json=excluded.member_epochs_json",
                [grant_id, book_id, ref["kind"], ref["id"], ref["versionId"], generation, expanded["statusGeneration"], canonical_bytes(expanded["memberEpochs"]).decode()])
        return {"id": grant_id, "grantId": grant_id, "generation": generation, "ref": ref, "metadata": expanded["metadata"]}

    async def revoke(self, book_id: str, grant_id: str):
        inherited = await self.db.fetch_one("SELECT manifest_json FROM continuation_operations WHERE book_id=?", [book_id])
        grant = await self.db.fetch_one("SELECT * FROM writing_technique_grants WHERE book_id=? AND id=?", [book_id, grant_id])
        if inherited and grant and any(ref["id"] == grant["object_id"] and ref["versionId"] == grant["version_id"] for ref in json.loads(inherited["manifest_json"])["defaultTechniques"]):
            raise TechniqueError("invalid_reference", "此版本被续写继承快照引用；可在会话中取消选择，不能撤销历史引用权限")
        await self.db.execute("UPDATE writing_technique_grants SET active=0,generation=generation+1 WHERE book_id=? AND id=? AND active=1", [book_id, grant_id])
        from application.writing_technique_lifecycle import cancel_invalid_technique_runs
        await cancel_invalid_technique_runs(self.db, book_id=book_id)

    async def list_grants(self, book_id: str):
        valid = {item["grantId"]: item for item in await self.candidates(book_id)}
        rows = await self.db.fetch_all("SELECT * FROM writing_technique_grants WHERE book_id=? AND active=1 ORDER BY rowid", [book_id])
        result = []
        for row in rows:
            if row["id"] in valid:
                result.append({**valid[row["id"]], "available": True})
                continue
            metadata = {"name": "不可用的写作技法", "description": ""}
            try:
                record = await asyncio.to_thread(self.library.store(row["kind"]).get_record, row["object_id"])
                metadata = record.get("metadata") or metadata
            except TechniqueError:
                pass
            result.append({"grantId": row["id"], "generation": row["generation"], "available": False, "metadata": metadata,
                "ref": {"kind": row["kind"], "id": row["object_id"], "versionId": row["version_id"]}})
        return result

    async def candidates(self, book_id: str) -> list[dict]:
        rows = await self.db.fetch_all("SELECT * FROM writing_technique_grants WHERE book_id=? AND active=1 ORDER BY rowid", [book_id])
        result = []
        for row in rows:
            ref = {"kind": row["kind"], "id": row["object_id"], "versionId": row["version_id"]}
            try:
                expanded = await self._expand(ref)
                if expanded["statusGeneration"] != row["status_generation"] or expanded["memberEpochs"] != json.loads(row["member_epochs_json"]):
                    continue
                result.append({**expanded, "grantId": row["id"], "generation": row["generation"]})
            except TechniqueError:
                continue
        return result

    async def freeze(self, *, book_id: str, mode: str = "manual", manual: list[dict] = (), input_session_id: str | None = None) -> dict:
        if mode not in {"manual", "auto"}:
            raise TechniqueError("invalid_reference", "无效的写作技法使用模式")
        selected = [dict(await self._expand(ref, verify_files=True, input_session_id=input_session_id), selection="manual") for ref in manual]
        inherited = await self.db.fetch_one("SELECT manifest_json FROM continuation_operations WHERE book_id=?", [book_id])
        defaults = json.loads(inherited["manifest_json"])["defaultTechniques"] if inherited else []
        for candidate in selected:
            if candidate["ref"] in defaults:
                ref = candidate["ref"]
                grant = await self.db.fetch_one("SELECT * FROM writing_technique_grants WHERE book_id=? AND kind=? AND object_id=? AND version_id=? AND active=1", [book_id, ref["kind"], ref["id"], ref["versionId"]])
                if not grant or grant["status_generation"] != candidate["statusGeneration"] or json.loads(grant["member_epochs_json"]) != candidate["memberEpochs"]:
                    raise TechniqueError("authorization_revoked", "来源默认技法授权已撤销，请处理授权或取消选择")
                candidate.update(grantId=grant["id"], generation=grant["generation"])
        snapshot = {"schemaVersion": 1, "bookId": book_id, "mode": mode,
                    "manual": selected, "candidates": await self.candidates(book_id) if mode == "auto" else []}
        self.resolve(snapshot)
        return snapshot

    @staticmethod
    def resolve(snapshot: dict, automatic_refs: list[dict] = ()) -> dict:
        versions, selected, skipped, members = {}, [], [], {}

        def admit(candidate, required):
            conflict = next((ref for ref in candidate["members"] if ref["id"] in versions and versions[ref["id"]] != ref["versionId"]), None)
            if conflict:
                if required:
                    raise TechniqueError("invalid_reference", "手动指定的写作技法存在版本冲突，请选择同一版本")
                skipped.append({"ref": candidate["ref"], "reason": "version_conflict"})
                return
            selected.append(candidate)
            for ref in candidate["members"]:
                versions[ref["id"]] = ref["versionId"]
                entry = members.setdefault(ref["id"], {"ref": ref, "sources": []})
                entry["sources"].append(candidate["ref"])

        for candidate in snapshot.get("manual", []):
            admit(candidate, True)
        if automatic_refs and snapshot.get("mode") != "auto":
            raise TechniqueError("authorization_revoked", "手动模式不允许自动选择写作技法")
        for ref in automatic_refs:
            candidate = next((c for c in snapshot.get("candidates", []) if c["ref"] == ref), None)
            if candidate is None:
                raise TechniqueError("authorization_revoked", "写作技法不在本轮授权候选中")
            admit(dict(candidate, selection="auto"), False)
        return {"selected": selected, "members": list(members.values()), "skipped": skipped}

    async def validate(self, snapshot: dict, *, selected: list[dict] | None = None):
        for candidate in selected if selected is not None else snapshot.get("manual", []):
            try:
                current = await self._expand(candidate["ref"], input_session_id=candidate.get("inputSessionId"))
            except TechniqueError as exc:
                raise TechniqueError("authorization_revoked", "本轮写作技法或方案已不可用，请重新选择") from exc
            if current["statusGeneration"] != candidate["statusGeneration"] or current["memberEpochs"] != candidate["memberEpochs"]:
                raise TechniqueError("authorization_revoked", "写作技法授权已失效，请重新选择")
            if candidate.get("grantId"):
                grant = await self.db.fetch_one("SELECT * FROM writing_technique_grants WHERE id=? AND book_id=?", [candidate["grantId"], snapshot["bookId"]])
                if not grant or not grant["active"] or grant["generation"] != candidate["generation"]:
                    raise TechniqueError("authorization_revoked", "本轮写作技法授权已撤销，请重新选择")

    async def read(self, snapshot: dict, ref: dict, path: str, *, automatic_refs=(), entry_refs=(), max_characters: int) -> dict:
        resolution = self.resolve(snapshot, automatic_refs)
        await self.validate(snapshot, selected=resolution["selected"])
        if not any(member["ref"] == ref for member in resolution["members"]):
            raise TechniqueError("authorization_revoked", "该技法未在本轮准入")
        if path != "SKILL.md" and ref not in entry_refs:
            raise TechniqueError("invalid_reference", "请先读取同一版本的 SKILL.md 入口")
        content = await self.library.read_version_file(ref, path)
        if len(content["content"]) > max_characters:
            raise TechniqueError("file_exceeds_budget", f"{path} 无法完整放入当前上下文，请减少选择或缩小文件")
        return content


class TechniqueEvidenceValidator:
    def __init__(self, access, snapshot, delegate):
        self.access, self.snapshot, self.delegate = access, snapshot, delegate

    async def validate_evidence(self, receipts, *, signal=None):
        refs = []
        for receipt in receipts:
            if receipt.source == "writing_technique/v1":
                for ref in receipt.metadata.get("selections", []):
                    if ref not in refs:
                        refs.append(dict(ref))
        manual = [c["ref"] for c in self.snapshot.get("manual", [])]
        resolution = self.access.resolve(self.snapshot, [ref for ref in refs if ref not in manual])
        await self.access.validate(self.snapshot, selected=resolution["selected"])
        if self.delegate:
            await self.delegate.validate_evidence(receipts, signal=signal)
