"""Durable author files with immutable generations and atomic receipt-bearing heads.

The same library lock is used by file mutations and full-backup capture. A crashed
write may leave an unreferenced immutable directory, but never a partial head.
"""

from __future__ import annotations

from infrastructure.persistence.writing.technique_document_parser import file_manifest

import copy
import json
import os
import re
import tempfile
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Callable

from domains.writing.techniques import (
    TechniqueError, TechniqueLimits, author_path, canonical_bytes, digest,
)

_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()
_HELD_LOCKS = threading.local()


def _sync_dir(path: Path) -> None:
    if os.name != "nt":
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


class TechniqueFileStore:
    # 集合目录与记录类型：子类（如 SkillFileStore）覆写这两个属性即可获得
    # 独立的存储命名空间、版本目录与 kind 校验，无需复制任何逻辑。
    collection = "techniques"
    record_kind = "technique"

    def __init__(self, root: Path, *, limits: TechniqueLimits = TechniqueLimits()):
        self.root = Path(root).absolute()
        self.limits = limits
        self.root.parent.mkdir(parents=True, exist_ok=True)
        with _LOCKS_GUARD:
            self._thread_lock = _LOCKS.setdefault(str(self.root), threading.RLock())
        already_held = str(self.root) in getattr(_HELD_LOCKS, "roots", set())
        with self.barrier():
            if self.root.is_symlink():
                raise TechniqueError("invalid_reference", "技法存储目录不能为符号链接")
            if not already_held:
                self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, *parts: str) -> Path:
        path = self.root
        for part in parts:
            for component in part.split("/"):
                if component in {"", ".", ".."} or not re.fullmatch(r"[^\\\x00]+", component):
                    raise TechniqueError("invalid_reference", "无效的存储身份或文件路径")
                path = path / component
                if path.is_symlink():
                    raise TechniqueError("invalid_reference", "技法存储不允许符号链接")
        return path

    @contextmanager
    def barrier(self):
        with self._thread_lock:
            roots = getattr(_HELD_LOCKS, "roots", None)
            if roots is None:
                roots = _HELD_LOCKS.roots = set()
            key = str(self.root)
            if key in roots:
                yield
                return
            lock = self.root.parent / ("." + self.root.name + ".lock")
            if lock.is_symlink():
                raise TechniqueError("invalid_reference", "技法存储锁不能为符号链接")
            with lock.open("a+b") as handle:
                if os.name == "nt":
                    import msvcrt
                    handle.seek(0, 2)
                    if handle.tell() == 0:
                        handle.write(b"0")
                        handle.flush()
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    roots.add(key)
                    yield
                finally:
                    roots.remove(key)
                    if os.name == "nt":
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _write_json(self, path: Path, value: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(canonical_bytes(value))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, path)
            _sync_dir(path.parent)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)

    def _json(self, path: Path) -> dict:
        try:
            return json.loads(path.read_bytes())
        except FileNotFoundError as exc:
            raise TechniqueError("version_missing", "指定的技法、草稿或版本不存在") from exc

    @staticmethod
    def _id(value: str) -> str:
        if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", value):
            raise TechniqueError("invalid_reference", "无效的技法或草稿身份")
        return value

    def _record_path(self, technique_id: str) -> Path:
        return self._path(self.collection, self._id(technique_id), "record.json")

    def _draft_path(self, technique_id: str, draft_id: str) -> Path:
        return self._path(self.collection, self._id(technique_id), "drafts", self._id(draft_id), "state.json")

    def _deletion_path(self, object_id: str) -> Path:
        collection = self._record_path(object_id).parent.parent.name
        return self._path('deletions', collection, self._id(object_id) + '.json')

    def _assert_not_deleted(self, object_id: str) -> None:
        if self._deletion_path(object_id).exists():
            raise TechniqueError('version_missing', '此技法或方案已永久删除')

    @staticmethod
    def _replay(head: dict, operation_id: str, request: dict):
        if not isinstance(operation_id, str) or not operation_id or len(operation_id) > 512:
            raise TechniqueError("operation_conflict", "操作需要稳定的 operationId")
        receipt = head.get("operations", {}).get(operation_id)
        if receipt is None:
            return None
        if receipt["requestDigest"] != digest(canonical_bytes(request)):
            raise TechniqueError("operation_conflict", "相同操作 ID 不能用于不同内容")
        return copy.deepcopy(receipt["result"])

    @staticmethod
    def _receipt(head: dict, operation_id: str, request: dict, result: dict) -> None:
        head.setdefault("operations", {})[operation_id] = {
            "requestDigest": digest(canonical_bytes(request)), "result": copy.deepcopy(result),
        }

    @staticmethod
    def _public(state: dict) -> dict:
        return {k: copy.deepcopy(v) for k, v in state.items() if k not in {"operations", "history", "generation"}}

    def _read_files(self, base: Path, manifest: dict) -> dict[str, str]:
        files = {}
        for entry in manifest["files"]:
            path = author_path(entry["path"], self.limits)
            target = self._path(str(base.relative_to(self.root)), path)
            try:
                raw = target.read_bytes()
                if len(raw) != entry["size"] or digest(raw) != entry["sha256"]:
                    raise TechniqueError("version_missing", f"技法文件已损坏：{path}")
                files[path] = raw.decode("utf-8")
            except (FileNotFoundError, UnicodeError) as exc:
                raise TechniqueError("version_missing", f"技法文件缺失或已损坏：{path}") from exc
        computed = file_manifest(files, validate=False, limits=self.limits)
        if computed["versionId"] != manifest["versionId"]:
            raise TechniqueError("version_missing", "技法文件清单摘要不一致")
        return files

    def _install_files(self, destination: Path, files: dict, manifest: dict) -> None:
        if destination.exists():
            self._read_files(destination / "files", manifest)
            return
        destination.parent.mkdir(parents=True, exist_ok=True)
        temp = Path(tempfile.mkdtemp(prefix=".pending-", dir=destination.parent))
        try:
            (temp / "files").mkdir()
            for path, text in files.items():
                target = temp / "files" / author_path(path, self.limits)
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("xb") as handle:
                    handle.write(text.encode("utf-8"))
                    handle.flush()
                    os.fsync(handle.fileno())
            self._write_json(temp / "manifest.json", manifest)
            for directory, _, _ in os.walk(temp, topdown=False):
                _sync_dir(Path(directory))
            os.replace(temp, destination)
            _sync_dir(destination.parent)
        finally:
            if temp.exists():
                import shutil
                shutil.rmtree(temp)

    def create_draft(self, *, operation_id: str, owner: dict | None = None,
                     technique_id: str | None = None, from_version: dict | None = None,
                     storage_scope: str = "library", origin: str | None = None) -> dict:
        if storage_scope not in {"library", "analysis_candidate", "run_input"}:
            raise TechniqueError("invalid_reference", "无效的技法存储作用域")
        request = {"action": "create", "owner": owner, "techniqueId": technique_id,
                   "fromVersion": from_version, "storageScope": storage_scope}
        # A deterministic identity makes create retries recoverable even if the
        # first response was lost before a caller learned the generated ID.
        technique_id = technique_id or "tech_" + uuid.uuid5(uuid.NAMESPACE_URL, "technique:" + operation_id).hex
        draft_id = "draft_" + uuid.uuid5(uuid.NAMESPACE_URL, technique_id + ":" + operation_id).hex
        with self.barrier():
            self._assert_not_deleted(technique_id)
            record_path = self._record_path(technique_id)
            if record_path.exists():
                record = self._json(record_path)
                if origin is not None and record.get("origin") != origin:
                    raise TechniqueError("invalid_reference", "技法来源标记不可变更")
            else:
                record = {
                    "id": technique_id, "kind": self.record_kind, "storageScope": storage_scope,
                    "status": "active", "publishedHead": None, "draftIds": [], "operations": {},
                }
                if origin is not None:
                    record["origin"] = origin
            replay = self._replay(record, operation_id, request)
            if replay is not None:
                return replay
            files = {}
            if from_version:
                files = self._version_files(from_version)
            manifest = file_manifest(files, validate=False, limits=self.limits)
            generation = "0-" + manifest["versionId"]
            self._assert_not_deleted(technique_id)
            state_path = self._draft_path(technique_id, draft_id)
            state = {"techniqueId": technique_id, "draftId": draft_id, "draftRevision": 0,
                     "state": "editing", "owner": owner or {}, "treeDigest": manifest["versionId"],
                     "manifest": manifest, "generation": generation, "history": {"0": generation}, "operations": {}}
            self._install_files(state_path.parent / "generations" / generation, files, manifest)
            # A pre-existing draft here can only be an uncommitted create from
            # this operation. It is never advertised until record.json commits.
            self._write_json(state_path, state)
            record["draftIds"].append(draft_id)
            record["draftHead"] = draft_id
            result = self._public(state)
            self._receipt(record, operation_id, request, result)
            self._write_json(record_path, record)
            return result

    def get_draft(self, technique_id: str, draft_id: str) -> dict:
        with self.barrier():
            self._assert_not_deleted(technique_id)
            return self._public(self._json(self._draft_path(technique_id, draft_id)))

    def read_draft_file(self, technique_id: str, draft_id: str, revision: int, path: str) -> dict:
        with self.barrier():
            self._assert_not_deleted(technique_id)
            state_path = self._draft_path(technique_id, draft_id)
            state = self._json(state_path)
            generation = state["history"].get(str(revision))
            if generation is None:
                raise TechniqueError("version_missing", "指定草稿代次不存在")
            base = self._path(str(state_path.parent.relative_to(self.root)), "generations", generation)
            manifest = self._json(base / "manifest.json")
            author_path(path, self.limits)
            entry = next((item for item in manifest["files"] if item["path"] == path), None)
            if entry is None:
                raise TechniqueError("version_missing", f"技法文件不存在：{path}")
            target = self._path(str(base.relative_to(self.root)), "files", path)
            try:
                raw = target.read_bytes()
                if len(raw) != entry["size"] or digest(raw) != entry["sha256"]:
                    raise TechniqueError("version_missing", f"技法文件已损坏：{path}")
                content = raw.decode("utf-8")
            except (FileNotFoundError, UnicodeError) as exc:
                raise TechniqueError("version_missing", f"技法文件缺失或已损坏：{path}") from exc
            return {"path": path, "content": content, "sha256": entry["sha256"], "versionId": manifest["versionId"]}

    @staticmethod
    def _file_result(files: dict, path: str, version: str) -> dict:
        author_path(path)
        if path not in files:
            raise TechniqueError("version_missing", f"技法文件不存在：{path}")
        return {"path": path, "content": files[path], "sha256": digest(files[path].encode("utf-8")), "versionId": version}

    def _mutate_draft(self, technique_id: str, draft_id: str, expected_revision: int,
                      operation_id: str, request: dict, action: Callable) -> dict:
        with self.barrier():
            self._assert_not_deleted(technique_id)
            state_path = self._draft_path(technique_id, draft_id)
            state = self._json(state_path)
            replay = self._replay(state, operation_id, request)
            if replay is not None:
                return replay
            if state["state"] == "cancelled":
                raise TechniqueError("draft_cancelled", "草稿已取消，请复制后继续编辑")
            if state["draftRevision"] != expected_revision or state["state"] != "editing":
                raise TechniqueError("draft_conflict", "草稿已被修改或封存，请重新读取")
            result = action(state, state_path)
            self._receipt(state, operation_id, request, result)
            self._write_json(state_path, state)
            return result

    def apply_changes(self, technique_id: str, draft_id: str, *, expected_revision: int,
                      operation_id: str, changes: list[dict]) -> dict:
        request = {"action": "changes", "expectedRevision": expected_revision, "changes": changes}

        def apply(state, state_path):
            files = self._read_files(state_path.parent / "generations" / state["generation"] / "files", state["manifest"])
            for change in changes:
                path = author_path(change.get("path"), self.limits)
                action = change.get("action")
                if action == "put":
                    files[path] = change.get("content")
                elif action in {"delete", "move"}:
                    if path not in files:
                        raise TechniqueError("invalid_reference", f"待修改文件不存在：{path}")
                    if action == "move":
                        target = author_path(change.get("target"), self.limits)
                        if target in files:
                            raise TechniqueError("invalid_reference", f"移动目标已存在：{target}")
                        files[target] = files[path]
                    del files[path]
                else:
                    raise TechniqueError("invalid_reference", "不支持的文件修改操作")
            manifest = file_manifest(files, validate=False, limits=self.limits)
            revision = state["draftRevision"] + 1
            generation = f"{revision}-{manifest['versionId']}"
            self._install_files(state_path.parent / "generations" / generation, files, manifest)
            state.update(draftRevision=revision, generation=generation, treeDigest=manifest["versionId"], manifest=manifest)
            state["history"][str(revision)] = generation
            return self._public(state)

        return self._mutate_draft(technique_id, draft_id, expected_revision, operation_id, request, apply)

    def seal_draft(self, technique_id: str, draft_id: str, *, expected_revision: int,
                   expected_tree_digest: str, operation_id: str) -> dict:
        request = {"action": "seal", "expectedRevision": expected_revision, "expectedTreeDigest": expected_tree_digest}

        def seal(state, state_path):
            if state["treeDigest"] != expected_tree_digest:
                raise TechniqueError("draft_conflict", "草稿内容摘要已改变")
            files = self._read_files(state_path.parent / "generations" / state["generation"] / "files", state["manifest"])
            manifest = file_manifest(files, limits=self.limits)
            manifest.update(id=technique_id, kind=self.record_kind)
            destination = self._path(self.collection, technique_id, "versions", manifest["versionId"])
            self._install_files(destination, files, manifest)
            state.update(state="sealed", sealedRef={"kind": self.record_kind, "id": technique_id, "versionId": manifest["versionId"]})
            return self._public(state)

        return self._mutate_draft(technique_id, draft_id, expected_revision, operation_id, request, seal)

    def cancel_draft(self, technique_id: str, draft_id: str, *, expected_revision: int, operation_id: str) -> dict:
        request = {"action": "cancel", "expectedRevision": expected_revision}

        def cancel(state, _):
            state["state"] = "cancelled"
            return self._public(state)

        return self._mutate_draft(technique_id, draft_id, expected_revision, operation_id, request, cancel)

    def _version_files(self, ref: dict) -> dict:
        self._assert_not_deleted(ref.get("id"))
        if ref.get("kind") != self.record_kind or not re.fullmatch(r"[a-f0-9]{64}", str(ref.get("versionId", ""))):
            raise TechniqueError("invalid_reference", "需要准确的技法版本引用")
        base = self._path(self.collection, self._id(ref.get("id")), "versions", ref["versionId"])
        manifest = self._json(base / "manifest.json")
        if manifest.get("id") != ref["id"] or manifest.get("versionId") != ref["versionId"]:
            raise TechniqueError("version_missing", "技法版本清单身份不一致")
        return self._read_files(base / "files", manifest)

    def read_version_file(self, ref: dict, path: str) -> dict:
        """Storage read only; execution authorization belongs to the service."""
        with self.barrier():
            author_path(path, self.limits)
            manifest = self._version_manifest(ref)
            entry = next((entry for entry in manifest["files"] if entry["path"] == path), None)
            if entry is None:
                raise TechniqueError("version_missing", f"技法文件不存在：{path}")
            target = self._path(self.collection, ref["id"], "versions", ref["versionId"], "files", path)
            try:
                raw = target.read_bytes()
                if len(raw) != entry["size"] or digest(raw) != entry["sha256"]:
                    raise TechniqueError("version_missing", f"技法文件已损坏：{path}")
                content = raw.decode("utf-8")
            except (FileNotFoundError, UnicodeError) as exc:
                raise TechniqueError("version_missing", f"技法文件缺失或已损坏：{path}") from exc
            return {"path": path, "content": content, "sha256": entry["sha256"], "versionId": ref["versionId"]}

    def _version_manifest(self, ref: dict) -> dict:
        self._assert_not_deleted(ref.get("id"))
        if ref.get("kind") != self.record_kind or not re.fullmatch(r"[a-f0-9]{64}", str(ref.get("versionId", ""))):
            raise TechniqueError("invalid_reference", "需要准确的技法版本引用")
        manifest = self._json(self._path(self.collection, self._id(ref.get("id")), "versions", ref["versionId"], "manifest.json"))
        tree = {"formatVersion": 1, "files": [{"path": e["path"], "sha256": e["sha256"]} for e in manifest["files"]]}
        if manifest.get("id") != ref["id"] or manifest.get("versionId") != ref["versionId"] or digest(canonical_bytes(tree)) != ref["versionId"]:
            raise TechniqueError("version_missing", "技法版本清单身份或摘要不一致")
        return manifest

    def get_version_manifest(self, ref: dict, *, verify_files: bool = False) -> dict:
        with self.barrier():
            manifest = self._version_manifest(ref)
            if verify_files:
                self._version_files(ref)
            return manifest

    def get_record(self, technique_id: str) -> dict:
        with self.barrier():
            self._assert_not_deleted(technique_id)
            return self._public(self._json(self._record_path(technique_id)))

    def references_sources(self, revision_ids: set[str]) -> bool:
        with self.barrier():
            for record in self.list_records(include_archived=True):
                for draft_id in record["draftIds"]:
                    draft = self.get_draft(record["id"], draft_id)
                    if draft.get("owner", {}).get("sourceRevisionId") in revision_ids:
                        return True
            return False

    def set_status(self, technique_id: str, status: str, *, operation_id: str) -> dict:
        if status not in {"active", "archived"}:
            raise TechniqueError("invalid_reference", "无效的技法状态")
        request = {"action": "status", "status": status}
        with self.barrier():
            self._assert_not_deleted(technique_id)
            path = self._record_path(technique_id)
            record = self._json(path)
            replay = self._replay(record, operation_id, request)
            if replay is not None:
                return replay
            # Persist an epoch in the canonical record so re-enabling visibility
            # cannot resurrect an older execution grant even after index rebuild.
            if record["status"] != status:
                record["statusGeneration"] = record.get("statusGeneration", 0) + 1
            record["status"] = status
            result = self._public(record)
            self._receipt(record, operation_id, request, result)
            self._write_json(path, record)
            return result

    def publish(self, technique_id: str, ref: dict, *, expected_published_head: str | None, operation_id: str) -> dict:
        request = {"action": "publish", "ref": ref, "expectedPublishedHead": expected_published_head}
        with self.barrier():
            self._assert_not_deleted(technique_id)
            path = self._record_path(technique_id)
            record = self._json(path)
            replay = self._replay(record, operation_id, request)
            if replay is not None:
                return replay
            if record["publishedHead"] != expected_published_head:
                raise TechniqueError("draft_conflict", "已发布版本已改变")
            if record["status"] != "active":
                raise TechniqueError("invalid_reference", "请先恢复已归档技法，再发布版本")
            if ref.get("id") != technique_id or record["storageScope"] != "library":
                raise TechniqueError("invalid_reference", "请先将候选或上传内容保存为库草稿")
            if not any(self._json(self._draft_path(technique_id, d)).get("sealedRef") == ref for d in record["draftIds"]):
                raise TechniqueError("invalid_reference", "技法版本未完成封存")
            manifest = file_manifest(self._version_files(ref), limits=self.limits)
            record.update(publishedHead=ref["versionId"], metadata=manifest["metadata"])
            if ref["versionId"] not in record.setdefault("publishedVersions", []):
                record["publishedVersions"].append(ref["versionId"])
            result = self._public(record)
            self._receipt(record, operation_id, request, result)
            self._write_json(path, record)
            return result

    def list_records(self, *, include_archived: bool = False, include_candidates: bool = False) -> list[dict]:
        with self.barrier():
            records = []
            directory = self._path(self.collection)
            if not directory.exists():
                return records
            for child in sorted(directory.iterdir()):
                if child.name.startswith(".") or self._deletion_path(child.name).exists():
                    continue
                record = self._json(self._record_path(child.name))
                if (include_candidates or record["storageScope"] == "library") and (include_archived or record["status"] == "active"):
                    records.append(self._public(record))
            return records
