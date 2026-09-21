"""Stable JSON digests for persisted product contracts."""

from __future__ import annotations

import hashlib
import json


def canonical_json_digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")).hexdigest()


__all__ = ["canonical_json_digest"]
