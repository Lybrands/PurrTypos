"""Author-file contracts shared by editing, generation, import and reading."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Mapping



class TechniqueError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.status_code = 409 if code in {
            "draft_conflict", "operation_conflict", "draft_cancelled", "authorization_revoked"
        } else 400


@dataclass(frozen=True)
class TechniqueLimits:
    file_bytes: int = 256 * 1024
    package_bytes: int = 4 * 1024 * 1024
    file_count: int = 128
    directory_depth: int = 8
    scheme_members: int = 64


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def author_path(value: str, limits: TechniqueLimits = TechniqueLimits()) -> str:
    if not isinstance(value, str) or not value or "\\" in value or value.startswith("/"):
        raise TechniqueError("invalid_reference", f"无效的技法文件路径：{value}")
    parts = value.split("/")
    if any(p in {"", ".", ".."} or p.endswith((" ", ".")) or
           re.search(r'[\x00-\x1f<>:"|?*]', p) or
           re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?", p) for p in parts):
        raise TechniqueError("invalid_reference", f"无效的技法文件路径：{value}")
    if len(parts) - 1 > limits.directory_depth or PurePosixPath(value).suffix.lower() not in {".md", ".txt"}:
        raise TechniqueError("invalid_reference", f"不支持的技法文件类型或目录深度：{value}")
    return value


def validate_scheme(value: Mapping[str, Any], limits: TechniqueLimits = TechniqueLimits()) -> dict:
    if value.get("schemaVersion") != 1:
        raise TechniqueError("invalid_entry", "不支持的写作方案格式")
    for key, maximum in (("name", 120), ("description", 1000)):
        if not isinstance(value.get(key), str) or not value[key].strip() or len(value[key]) > maximum:
            raise TechniqueError("invalid_entry", f"方案 {key} 无效")
    if not isinstance(value.get("composition"), str):
        raise TechniqueError("invalid_entry", "方案组合说明必须为文本")
    if len(value["composition"].encode("utf-8")) > limits.file_bytes:
        raise TechniqueError("file_exceeds_budget", "方案组合说明超过文件大小限制")
    members = value.get("members")
    if not isinstance(members, list) or not 1 <= len(members) <= limits.scheme_members:
        raise TechniqueError("invalid_reference", "方案需要至少一个技法成员且不能超过成员上限")
    seen = set()
    for ref in members:
        if not isinstance(ref, dict) or ref.get("kind") != "technique" or not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", str(ref.get("id", ""))) or not re.fullmatch(r"[0-9a-f]{64}", str(ref.get("versionId", ""))):
            raise TechniqueError("invalid_reference", "方案成员必须引用准确的技法版本")
        if ref["id"] in seen:
            raise TechniqueError("invalid_reference", "方案不能重复引用同一技法")
        seen.add(ref["id"])
    return {key: value[key] for key in ("schemaVersion", "name", "description", "composition", "members")}
