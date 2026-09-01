"""Writing-only request data kept outside the business-agnostic Core."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from purra.contracts import DomainContext
from purra.json_values import thaw_json_mapping


WRITING_DOMAIN_NAMESPACE = "purrtypos.writing"


@dataclass(frozen=True, slots=True)
class WritingDomainContext:
    book_id: str | None = None
    chapter_id: str | None = None
    current_chapter_title: str | None = None
    writing_chapters: tuple[Mapping[str, Any], ...] = ()
    available_outlines: tuple[Mapping[str, Any], ...] = ()
    associated_chapter_ids: tuple[str, ...] = ()
    associated_outline_ids: tuple[str, ...] = ()
    selected_memory_ids: tuple[Any, ...] = ()
    selected_long_term_memory_ids: tuple[str, ...] = ()
    selected_foreshadowing_ids: tuple[Any, ...] = ()
    context_window_label: str | None = None
    writing_method_overrides: Mapping[str, Any] | None = None
    writing_method_binding_snapshot: Mapping[str, Any] | None = None
    writing_method_recommendation_requested: bool = False
    creation_mode: str = "original"
    continuation_binding: Mapping[str, Any] | None = None
    inherited_canon_records: tuple[Mapping[str, Any], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "writing_chapters", tuple(dict(item) for item in self.writing_chapters))
        object.__setattr__(self, "available_outlines", tuple(dict(item) for item in self.available_outlines))
        object.__setattr__(self, "associated_chapter_ids", tuple(self.associated_chapter_ids))
        object.__setattr__(self, "associated_outline_ids", tuple(self.associated_outline_ids))
        object.__setattr__(self, "selected_memory_ids", tuple(self.selected_memory_ids))
        object.__setattr__(
            self,
            "selected_long_term_memory_ids",
            tuple(self.selected_long_term_memory_ids),
        )
        object.__setattr__(self, "selected_foreshadowing_ids", tuple(self.selected_foreshadowing_ids))
        object.__setattr__(
            self,
            "writing_method_overrides",
            dict(self.writing_method_overrides or {}),
        )
        object.__setattr__(
            self,
            "writing_method_binding_snapshot",
            dict(self.writing_method_binding_snapshot or {}),
        )
        object.__setattr__(self, "creation_mode", str(self.creation_mode or "original"))
        object.__setattr__(
            self, "continuation_binding", dict(self.continuation_binding or {})
        )
        object.__setattr__(
            self,
            "inherited_canon_records",
            tuple(dict(item) for item in self.inherited_canon_records),
        )

    def to_core_context(self) -> DomainContext:
        return DomainContext(
            namespace=WRITING_DOMAIN_NAMESPACE,
            payload={
                "book_id": self.book_id,
                "chapter_id": self.chapter_id,
                "current_chapter_title": self.current_chapter_title,
                "writing_chapters": self.writing_chapters,
                "available_outlines": self.available_outlines,
                "associated_chapter_ids": self.associated_chapter_ids,
                "associated_outline_ids": self.associated_outline_ids,
                "selected_memory_ids": self.selected_memory_ids,
                "selected_long_term_memory_ids": self.selected_long_term_memory_ids,
                "selected_foreshadowing_ids": self.selected_foreshadowing_ids,
                "context_window_label": self.context_window_label,
                "writing_method_overrides": self.writing_method_overrides,
                "writing_method_binding_snapshot": self.writing_method_binding_snapshot,
                "writing_method_recommendation_requested": (
                    self.writing_method_recommendation_requested
                ),
                "creation_mode": self.creation_mode,
                "continuation_binding": self.continuation_binding,
                "inherited_canon_records": self.inherited_canon_records,
            },
        )

    @classmethod
    def from_core_context(cls, context: DomainContext) -> "WritingDomainContext":
        if context.namespace != WRITING_DOMAIN_NAMESPACE:
            raise ValueError(f"unsupported writing domain namespace: {context.namespace}")
        payload = thaw_json_mapping(context.payload)
        return cls(
            book_id=_optional_identifier(payload.get("book_id")),
            chapter_id=_optional_identifier(payload.get("chapter_id")),
            current_chapter_title=_optional_identifier(
                payload.get("current_chapter_title")
            ),
            writing_chapters=_mapping_tuple(payload.get("writing_chapters")),
            available_outlines=_mapping_tuple(payload.get("available_outlines")),
            associated_chapter_ids=_text_tuple(payload.get("associated_chapter_ids")),
            associated_outline_ids=_text_tuple(payload.get("associated_outline_ids")),
            selected_memory_ids=_value_tuple(payload.get("selected_memory_ids")),
            selected_long_term_memory_ids=_text_tuple(
                payload.get("selected_long_term_memory_ids")
            ),
            selected_foreshadowing_ids=_value_tuple(
                payload.get("selected_foreshadowing_ids")
            ),
            context_window_label=_optional_identifier(
                payload.get("context_window_label")
            ),
            writing_method_overrides=_mapping(payload.get("writing_method_overrides")),
            writing_method_binding_snapshot=_mapping(
                payload.get("writing_method_binding_snapshot")
            ),
            writing_method_recommendation_requested=bool(
                payload.get("writing_method_recommendation_requested")
            ),
            creation_mode=str(payload.get("creation_mode") or "original"),
            continuation_binding=_mapping(payload.get("continuation_binding")),
            inherited_canon_records=_mapping_tuple(
                payload.get("inherited_canon_records")
            ),
        )


def _optional_identifier(value: object) -> str | None:
    """Normalize an optional identifier while preserving supplied strings."""

    if value is None:
        return None
    return value if isinstance(value, str) else str(value)


def _value_tuple(value: object) -> tuple[Any, ...]:
    return tuple(value) if isinstance(value, (list, tuple)) else ()


def _text_tuple(value: object) -> tuple[str, ...]:
    return tuple(str(item) for item in _value_tuple(value))


def _mapping_tuple(value: object) -> tuple[Mapping[str, Any], ...]:
    return tuple(dict(item) for item in _value_tuple(value) if isinstance(item, Mapping))


def _mapping(value: object) -> Mapping[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}
