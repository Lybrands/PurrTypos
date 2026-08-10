"""Stable ownership scopes for durable Agent artifacts."""

from __future__ import annotations

from enum import StrEnum


class ArtifactScope(StrEnum):
    RUN = "run"
    WORK_ITEM = "work_item"


__all__ = ["ArtifactScope"]
