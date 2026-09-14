"""Canonical Operation-scoped context for one Screenplay replacement Unit."""

from __future__ import annotations

from agents.screenplay.contracts import (
    SCREENPLAY_REPLACEMENT_SCHEMA_VERSION,
    ScreenplayPartOperationScope,
)
from purra.json_values import thaw_json_mapping


def screenplay_unit_scope_from_request(request) -> ScreenplayPartOperationScope | None:
    payload = thaw_json_mapping(request.domain_context.payload)
    raw = payload.get("unit")
    if raw is None:
        return None
    if set(payload) != {"schemaVersion", "projectId", "unit"}:
        raise ValueError("Screenplay replacement Unit payload shape is invalid")
    if payload["schemaVersion"] != SCREENPLAY_REPLACEMENT_SCHEMA_VERSION:
        raise ValueError("Screenplay replacement Unit schema is invalid")
    scope = ScreenplayPartOperationScope.from_mapping(raw)
    if payload["projectId"] != scope.project_id:
        raise ValueError("Screenplay replacement Unit project scope conflicts")
    return scope


__all__ = ["screenplay_unit_scope_from_request"]
