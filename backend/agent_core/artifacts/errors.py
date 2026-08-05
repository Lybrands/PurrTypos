"""Typed failures for artifact lifecycle and persistence boundaries."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from agent_core.errors import AgentCoreError


class ArtifactError(AgentCoreError):
    def __init__(
        self,
        message: str,
        *,
        code: str,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = str(code or "artifact_error")
        self.details = MappingProxyType(dict(details or {}))


class ArtifactNotFoundError(ArtifactError):
    pass


class ArtifactConflictError(ArtifactError):
    pass


class ArtifactStateError(ArtifactError):
    pass


class ArtifactValidationError(ArtifactError):
    pass


class ArtifactAccessDeniedError(ArtifactError):
    pass


__all__ = [
    "ArtifactAccessDeniedError",
    "ArtifactConflictError",
    "ArtifactError",
    "ArtifactNotFoundError",
    "ArtifactStateError",
    "ArtifactValidationError",
]
