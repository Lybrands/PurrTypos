"""Screenplay-only request data kept outside the business-agnostic Core."""

from __future__ import annotations

from dataclasses import dataclass

from agent_core.contracts import DomainContext
from agent_core.json_values import thaw_json_mapping


SCREENPLAY_DOMAIN_NAMESPACE = "purrtypos.screenplay"


@dataclass(frozen=True, slots=True)
class ScreenplayDomainContext:
    project_id: str
    requested_source_book_id: str | None = None
    active_document_id: str | None = None
    requested_stage: str | None = None
    context_window_label: str | None = None

    def __post_init__(self) -> None:
        project_id = str(self.project_id or "").strip()
        if not project_id:
            raise ValueError("screenplay project id is required")
        object.__setattr__(self, "project_id", project_id)
        for name in (
            "requested_source_book_id",
            "active_document_id",
            "requested_stage",
            "context_window_label",
        ):
            value = getattr(self, name)
            normalized = str(value or "").strip() or None
            object.__setattr__(self, name, normalized)

    def to_core_context(self) -> DomainContext:
        return DomainContext(
            namespace=SCREENPLAY_DOMAIN_NAMESPACE,
            payload={
                "project_id": self.project_id,
                "requested_source_book_id": self.requested_source_book_id,
                "active_document_id": self.active_document_id,
                "requested_stage": self.requested_stage,
                "context_window_label": self.context_window_label,
            },
        )

    @classmethod
    def from_core_context(cls, context: DomainContext) -> "ScreenplayDomainContext":
        if context.namespace != SCREENPLAY_DOMAIN_NAMESPACE:
            raise ValueError(
                f"unsupported screenplay domain namespace: {context.namespace}"
            )
        payload = thaw_json_mapping(context.payload)
        return cls(
            project_id=str(payload.get("project_id") or ""),
            requested_source_book_id=_optional_text(
                payload.get("requested_source_book_id")
            ),
            active_document_id=_optional_text(payload.get("active_document_id")),
            requested_stage=_optional_text(payload.get("requested_stage")),
            context_window_label=_optional_text(
                payload.get("context_window_label")
            ),
        )


def _optional_text(value: object) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None
