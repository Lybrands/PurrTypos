"""Stable revisions and intent identities for replacement Writing tools."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping


def text_revision(value: object) -> str:
    return _digest(str(value or ""))


def record_revision(kind: str, values: Mapping[str, object]) -> str:
    canonical = json.dumps(
        {"kind": kind, "values": dict(values)},
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return _digest(canonical)


def intent_digest(kind: str, values: Mapping[str, object]) -> str:
    canonical = json.dumps(
        {"kind": kind, "values": dict(values)},
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return _digest(canonical)


def _digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


__all__ = ["intent_digest", "record_revision", "text_revision"]
