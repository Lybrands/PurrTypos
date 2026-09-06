"""Product-domain contracts for versioned writing methods and schemes."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal, Mapping, Sequence

MethodType = Literal["primary", "technique"]


class WritingMethodError(ValueError):
    status_code = 400


class WritingMethodNotFoundError(WritingMethodError):
    status_code = 404


class WritingMethodConflictError(WritingMethodError):
    status_code = 409


class WritingMethodReferenceError(WritingMethodConflictError):
    pass


@dataclass(frozen=True, slots=True)
class MethodDraft:
    name: str
    description: str
    method_type: MethodType
    tags: tuple[str, ...]
    markdown: str
    metadata: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class SchemeDraft:
    name: str
    description: str
    member_revision_ids: tuple[str, ...]


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def content_digest(markdown: str, metadata: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        canonical_json({"markdown": markdown, "metadata": dict(metadata)}).encode("utf-8")
    ).hexdigest()


def members_digest(member_revision_ids: Sequence[str]) -> str:
    return hashlib.sha256(
        canonical_json(list(member_revision_ids)).encode("utf-8")
    ).hexdigest()


def clean_ids(values: Sequence[str]) -> tuple[str, ...]:
    cleaned = tuple(str(value).strip() for value in values if str(value).strip())
    if len(cleaned) != len(set(cleaned)):
        raise WritingMethodConflictError("写作方案不能重复引用同一方法版本")
    return cleaned


__all__ = [
    "MethodDraft", "MethodType", "SchemeDraft",
    "WritingMethodConflictError", "WritingMethodError",
    "WritingMethodNotFoundError", "WritingMethodReferenceError",
    "canonical_json", "clean_ids", "content_digest", "members_digest",
]
