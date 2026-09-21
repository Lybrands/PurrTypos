"""Local writing schemes, with frozen member references and file-owned heads."""

from __future__ import annotations

import uuid

from domains.writing.techniques import TechniqueError, canonical_bytes, digest, validate_scheme
from infrastructure.persistence.writing.technique_file_store import TechniqueFileStore


class SchemeFileStore(TechniqueFileStore):
    def _record_path(self, scheme_id):
        return self._path("schemes", self._id(scheme_id), "record.json")

    def _draft_path(self, scheme_id, draft_id):
        return self._path("schemes", self._id(scheme_id), "drafts", self._id(draft_id), "state.json")

    def _check_deleted_members(self, content):
        techniques = TechniqueFileStore(self.root, limits=self.limits)
        for ref in content.get('members', []):
            if isinstance(ref, dict) and isinstance(ref.get('id'), str):
                techniques._assert_not_deleted(ref['id'])

    def create_scheme_draft(self, *, operation_id: str, content: dict, scheme_id: str | None = None) -> dict:
        request = {"action": "create", "schemeId": scheme_id, "content": content}
        scheme_id = scheme_id or "scheme_" + uuid.uuid5(uuid.NAMESPACE_URL, "scheme:" + operation_id).hex
        draft_id = "draft_" + uuid.uuid5(uuid.NAMESPACE_URL, scheme_id + ":" + operation_id).hex
        with self.barrier():
            self._assert_not_deleted(scheme_id)
            self._check_deleted_members(content)
            path = self._record_path(scheme_id)
            record = self._json(path) if path.exists() else {
                "id": scheme_id, "kind": "scheme", "storageScope": "library", "status": "active",
                "publishedHead": None, "draftIds": [], "operations": {},
            }
            replay = self._replay(record, operation_id, request)
            if replay is not None:
                return replay
            state = {"schemeId": scheme_id, "draftId": draft_id, "draftRevision": 0, "state": "editing",
                     "content": content, "treeDigest": digest(canonical_bytes(content)), "operations": {}}
            self._write_json(self._draft_path(scheme_id, draft_id), state)
            record["draftIds"].append(draft_id)
            record["draftHead"] = draft_id
            result = self._public(state)
            self._receipt(record, operation_id, request, result)
            self._write_json(path, record)
            return result

    def update_scheme_draft(self, scheme_id: str, draft_id: str, *, expected_revision: int,
                            operation_id: str, content: dict) -> dict:
        request = {"action": "update", "expectedRevision": expected_revision, "content": content}

        def update(state, state_path):
            self._check_deleted_members(content)
            previous = state_path.parent / "generations" / str(state["draftRevision"]) / "state.json"
            if not previous.exists():
                self._write_json(previous, self._public(state))
            state.update(content=content, draftRevision=state["draftRevision"] + 1,
                         treeDigest=digest(canonical_bytes(content)))
            return self._public(state)

        return self._mutate_draft(scheme_id, draft_id, expected_revision, operation_id, request, update)

    def _validate_members(self, content: dict) -> None:
        techniques = TechniqueFileStore(self.root, limits=self.limits)
        for ref in content["members"]:
            record = techniques._json(techniques._record_path(ref["id"]))
            if record["status"] != "active" or record["storageScope"] != "library":
                raise TechniqueError("invalid_reference", "方案成员必须为活动技法库中的技法")
            if ref["versionId"] not in record.get("publishedVersions", []):
                raise TechniqueError("invalid_reference", "方案成员必须为已发布技法版本")
            techniques._version_files(ref)

    def seal_scheme_draft(self, scheme_id: str, draft_id: str, *, expected_revision: int,
                          expected_tree_digest: str, operation_id: str) -> dict:
        request = {"action": "seal", "expectedRevision": expected_revision, "expectedTreeDigest": expected_tree_digest}

        def seal(state, _):
            if state["treeDigest"] != expected_tree_digest:
                raise TechniqueError("draft_conflict", "方案草稿摘要已改变")
            content = validate_scheme(state["content"], self.limits)
            self._validate_members(content)
            version = digest(canonical_bytes(content))
            path = self._path("schemes", scheme_id, "versions", version, "scheme.json")
            if path.exists():
                if self._json(path) != content:
                    raise TechniqueError("version_missing", "方案版本已损坏")
            else:
                self._write_json(path, content)
            state.update(state="sealed", sealedRef={"kind": "scheme", "id": scheme_id, "versionId": version})
            return self._public(state)

        return self._mutate_draft(scheme_id, draft_id, expected_revision, operation_id, request, seal)

    def _scheme_content(self, ref: dict) -> dict:
        self._assert_not_deleted(ref.get("id"))
        if ref.get("kind") != "scheme":
            raise TechniqueError("invalid_reference", "需要写作方案版本引用")
        path = self._path("schemes", self._id(ref.get("id")), "versions", self._id(ref.get("versionId")), "scheme.json")
        content = validate_scheme(self._json(path), self.limits)
        if digest(canonical_bytes(content)) != ref["versionId"]:
            raise TechniqueError("version_missing", "方案版本摘要不一致")
        return content

    def read_scheme(self, ref: dict, *, verify_members: bool = False) -> dict:
        with self.barrier():
            content = self._scheme_content(ref)
            if verify_members:
                self._validate_members(content)
            return content

    def publish(self, scheme_id: str, ref: dict, *, expected_published_head: str | None, operation_id: str) -> dict:
        request = {"action": "publish", "ref": ref, "expectedPublishedHead": expected_published_head}
        with self.barrier():
            self._assert_not_deleted(scheme_id)
            path = self._record_path(scheme_id)
            record = self._json(path)
            replay = self._replay(record, operation_id, request)
            if replay is not None:
                return replay
            if record["publishedHead"] != expected_published_head:
                raise TechniqueError("draft_conflict", "已发布方案版本已改变")
            if record["status"] != "active":
                raise TechniqueError("invalid_reference", "请先恢复已归档方案，再发布版本")
            if ref.get("id") != scheme_id or not any(self._json(self._draft_path(scheme_id, d)).get("sealedRef") == ref for d in record["draftIds"]):
                raise TechniqueError("invalid_reference", "方案版本未完成封存")
            content = self._scheme_content(ref)
            self._validate_members(content)
            record.update(publishedHead=ref["versionId"], metadata={k: content[k] for k in ("name", "description")})
            if ref["versionId"] not in record.setdefault("publishedVersions", []):
                record["publishedVersions"].append(ref["versionId"])
            result = self._public(record)
            self._receipt(record, operation_id, request, result)
            self._write_json(path, record)
            return result

    def list_records(self, *, include_archived: bool = False, **_) -> list[dict]:
        with self.barrier():
            directory = self._path("schemes")
            if not directory.exists():
                return []
            records = [self._json(self._record_path(p.name)) for p in sorted(directory.iterdir()) if not p.name.startswith(".") and not self._deletion_path(p.name).exists()]
            return [self._public(r) for r in records if include_archived or r["status"] == "active"]
