"""Host-owned request payload for the PurrA-native Writing Agent."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from purra.contracts import DomainContext
from purra.json_values import thaw_json_mapping


WRITING_DOMAIN_NAMESPACE = "purrtypos.writing"


@dataclass(frozen=True, slots=True)
class WritingRequestContext:
    """Stable HTTP-to-Agent locators; authoritative bodies stay behind tools."""

    book_id: str | None = None
    chapter_id: str | None = None
    current_chapter_title: str | None = None
    associated_chapter_ids: tuple[str, ...] = ()
    associated_outline_ids: tuple[str, ...] = ()
    selected_memory_ids: tuple[Any, ...] = ()
    selected_long_term_memory_ids: tuple[str, ...] = ()
    selected_foreshadowing_ids: tuple[Any, ...] = ()
    context_window_label: str | None = None
    writing_technique_input_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "associated_chapter_ids", tuple(self.associated_chapter_ids)
        )
        object.__setattr__(
            self, "associated_outline_ids", tuple(self.associated_outline_ids)
        )
        object.__setattr__(
            self, "selected_memory_ids", tuple(self.selected_memory_ids)
        )
        object.__setattr__(
            self,
            "selected_long_term_memory_ids",
            tuple(self.selected_long_term_memory_ids),
        )
        object.__setattr__(
            self,
            "selected_foreshadowing_ids",
            tuple(self.selected_foreshadowing_ids),
        )

    def to_core_context(self) -> DomainContext:
        # Preserve the established wire-to-domain payload keys. Empty legacy
        # hydration slots are deliberately absent from replacement requests.
        return DomainContext(
            namespace=WRITING_DOMAIN_NAMESPACE,
            payload={
                "book_id": self.book_id,
                "chapter_id": self.chapter_id,
                "current_chapter_title": self.current_chapter_title,
                "associated_chapter_ids": self.associated_chapter_ids,
                "associated_outline_ids": self.associated_outline_ids,
                "selected_memory_ids": self.selected_memory_ids,
                "selected_long_term_memory_ids": (
                    self.selected_long_term_memory_ids
                ),
                "selected_foreshadowing_ids": self.selected_foreshadowing_ids,
                "context_window_label": self.context_window_label,
                "writing_technique_input_id": self.writing_technique_input_id,
            },
        )

    @classmethod
    def from_core_context(
        cls,
        context: DomainContext,
    ) -> "WritingRequestContext":
        if context.namespace != WRITING_DOMAIN_NAMESPACE:
            raise ValueError(
                f"unsupported writing domain namespace: {context.namespace}"
            )
        payload = thaw_json_mapping(context.payload)
        if (
            payload.get("writing_method_binding_snapshot")
            or payload.get("writing_method_overrides")
            or payload.get("writing_method_recommendation_requested")
        ):
            raise ValueError("旧版写作技法协议已退役，请重新发起写作请求")
        return cls(
            book_id=_optional_text(payload.get("book_id")),
            chapter_id=_optional_text(payload.get("chapter_id")),
            current_chapter_title=_optional_text(
                payload.get("current_chapter_title")
            ),
            associated_chapter_ids=_text_tuple(
                payload.get("associated_chapter_ids")
            ),
            associated_outline_ids=_text_tuple(
                payload.get("associated_outline_ids")
            ),
            selected_memory_ids=_value_tuple(
                payload.get("selected_memory_ids")
            ),
            selected_long_term_memory_ids=_text_tuple(
                payload.get("selected_long_term_memory_ids")
            ),
            selected_foreshadowing_ids=_value_tuple(
                payload.get("selected_foreshadowing_ids")
            ),
            context_window_label=_optional_text(
                payload.get("context_window_label")
            ),
            writing_technique_input_id=_optional_text(
                payload.get("writing_technique_input_id")
            ),
        )


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    return value if isinstance(value, str) else str(value)


def _value_tuple(value: object) -> tuple[Any, ...]:
    return tuple(value) if isinstance(value, (list, tuple)) else ()


def _text_tuple(value: object) -> tuple[str, ...]:
    return tuple(str(item) for item in _value_tuple(value))


__all__ = ["WRITING_DOMAIN_NAMESPACE", "WritingRequestContext"]
