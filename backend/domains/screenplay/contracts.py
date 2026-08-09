"""Screenplay-only request data kept outside the business-agnostic Core."""

from __future__ import annotations

import re
from dataclasses import dataclass
from collections.abc import Sequence

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
    task_intent: str = "chat"
    draft_scene_count: int = 1
    draft_scope: str = "planner"
    bound_draft_scene_ids: tuple[str, ...] = ()
    # Host-hydrated from the persisted project before Core resolves tools.
    # The renderer cannot set this capability flag.
    source_scope_restricted: bool = False

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
        task_intent = str(self.task_intent or "chat").strip()
        if task_intent not in {
            "chat",
            "stage_deliverable",
        }:
            raise ValueError("unsupported screenplay task intent")
        object.__setattr__(self, "task_intent", task_intent)
        draft_scene_count = int(self.draft_scene_count)
        if not 1 <= draft_scene_count <= 100:
            raise ValueError("draft scene count must be between 1 and 100")
        object.__setattr__(self, "draft_scene_count", draft_scene_count)
        draft_scope = str(self.draft_scope or "planner").strip()
        if draft_scope not in {
            "planner",
            "next_scene",
            "next_episode",
            "all_remaining",
            "count",
        } and re.fullmatch(
            r"next_(?:[2-9]|[1-9]\d|100)_episodes",
            draft_scope,
        ) is None:
            raise ValueError("unsupported screenplay draft scope")
        object.__setattr__(self, "draft_scope", draft_scope)
        if isinstance(self.bound_draft_scene_ids, (str, bytes, bytearray)):
            raise TypeError("bound draft scene ids must be a sequence")
        bound_scene_ids = tuple(
            str(item or "").strip()
            for item in self.bound_draft_scene_ids
        )
        if (
            any(not item for item in bound_scene_ids)
            or len(bound_scene_ids) != len(set(bound_scene_ids))
        ):
            raise ValueError("bound draft scene ids must be non-empty and unique")
        object.__setattr__(
            self,
            "bound_draft_scene_ids",
            bound_scene_ids,
        )
        object.__setattr__(
            self,
            "source_scope_restricted",
            self.source_scope_restricted is True,
        )

    def to_core_context(self) -> DomainContext:
        return DomainContext(
            namespace=SCREENPLAY_DOMAIN_NAMESPACE,
            payload={
                "project_id": self.project_id,
                "requested_source_book_id": self.requested_source_book_id,
                "active_document_id": self.active_document_id,
                "requested_stage": self.requested_stage,
                "context_window_label": self.context_window_label,
                "task_intent": self.task_intent,
                "draft_scene_count": self.draft_scene_count,
                "draft_scope": self.draft_scope,
                "bound_draft_scene_ids": list(self.bound_draft_scene_ids),
                "source_scope_restricted": self.source_scope_restricted,
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
            task_intent=str(payload.get("task_intent") or "chat"),
            draft_scene_count=int(payload.get("draft_scene_count") or 1),
            draft_scope=str(payload.get("draft_scope") or "planner"),
            bound_draft_scene_ids=tuple(
                str(item)
                for item in (
                    payload.get("bound_draft_scene_ids")
                    if isinstance(payload.get("bound_draft_scene_ids"), Sequence)
                    and not isinstance(
                        payload.get("bound_draft_scene_ids"),
                        (str, bytes, bytearray),
                    )
                    else ()
                )
            ),
            source_scope_restricted=(
                payload.get("source_scope_restricted") is True
            ),
        )


def _optional_text(value: object) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None
