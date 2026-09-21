"""Host-owned context locators for the replacement Writing Agent.

The contract deliberately stores identifiers, never source bodies.  Selected
content enters a model turn only after an explicit read tool call.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from purra.json_values import thaw_json_mapping


WRITING_CONTEXT_SELECTION_STATE_KEY = "writingContextSelection"
MAX_CONTEXT_SELECTIONS = 32


class WritingContextSelectionError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class WritingContextSelection:
    associated_chapter_ids: tuple[str, ...] = ()
    associated_outline_ids: tuple[str, ...] = ()
    selected_spark_ids: tuple[str, ...] = ()
    selected_long_term_memory_ids: tuple[str, ...] = ()
    selected_foreshadowing_ids: tuple[str, ...] = ()
    writing_technique_input_id: str | None = None

    @classmethod
    def from_domain_payload(
        cls,
        payload: Mapping[str, Any] | object,
    ) -> "WritingContextSelection":
        values = thaw_json_mapping(payload)
        return cls(
            associated_chapter_ids=_identifiers(
                values.get("associated_chapter_ids")
                or values.get("associatedChapterIds"),
                "associatedChapterIds",
            ),
            associated_outline_ids=_identifiers(
                values.get("associated_outline_ids")
                or values.get("associatedOutlineIds"),
                "associatedOutlineIds",
            ),
            selected_spark_ids=_identifiers(
                values.get("selected_memory_ids")
                or values.get("selectedMemoryIds"),
                "selectedMemoryIds",
            ),
            selected_long_term_memory_ids=_identifiers(
                values.get("selected_long_term_memory_ids")
                or values.get("selectedLongTermMemoryIds"),
                "selectedLongTermMemoryIds",
            ),
            selected_foreshadowing_ids=_identifiers(
                values.get("selected_foreshadowing_ids")
                or values.get("selectedForeshadowingIds"),
                "selectedForeshadowingIds",
            ),
            writing_technique_input_id=_optional_identifier(
                values.get("writing_technique_input_id")
                or values.get("writingTechniqueInputId"),
                "writingTechniqueInputId",
            ),
        )

    @classmethod
    def from_mapping(cls, value: object) -> "WritingContextSelection":
        if not isinstance(value, dict):
            raise WritingContextSelectionError(
                "writing_context_selection_missing",
                "Writing context selection is missing",
            )
        allowed = {
            "associatedChapterIds",
            "associatedOutlineIds",
            "selectedSparkIds",
            "selectedLongTermMemoryIds",
            "selectedForeshadowingIds",
            "writingTechniqueInputId",
        }
        if not set(value).issubset(allowed):
            raise WritingContextSelectionError(
                "writing_context_selection_invalid",
                "Writing context selection contains unknown fields",
            )
        return cls(
            associated_chapter_ids=_identifiers(
                value.get("associatedChapterIds"), "associatedChapterIds"
            ),
            associated_outline_ids=_identifiers(
                value.get("associatedOutlineIds"), "associatedOutlineIds"
            ),
            selected_spark_ids=_identifiers(
                value.get("selectedSparkIds"), "selectedSparkIds"
            ),
            selected_long_term_memory_ids=_identifiers(
                value.get("selectedLongTermMemoryIds"),
                "selectedLongTermMemoryIds",
            ),
            selected_foreshadowing_ids=_identifiers(
                value.get("selectedForeshadowingIds"),
                "selectedForeshadowingIds",
            ),
            writing_technique_input_id=_optional_identifier(
                value.get("writingTechniqueInputId"),
                "writingTechniqueInputId",
            ),
        )

    def to_mapping(self) -> dict[str, object]:
        return {
            "associatedChapterIds": list(self.associated_chapter_ids),
            "associatedOutlineIds": list(self.associated_outline_ids),
            "selectedSparkIds": list(self.selected_spark_ids),
            "selectedLongTermMemoryIds": list(
                self.selected_long_term_memory_ids
            ),
            "selectedForeshadowingIds": list(self.selected_foreshadowing_ids),
            **(
                {"writingTechniqueInputId": self.writing_technique_input_id}
                if self.writing_technique_input_id
                else {}
            ),
        }

    def public_manifest(self) -> dict[str, object]:
        """Expose bounded selection counts, but neither locators nor bodies."""

        return {
            "schemaVersion": 1,
            "selectionCounts": {
                "associatedChapters": len(self.associated_chapter_ids),
                "associatedOutlines": len(self.associated_outline_ids),
                "sparks": len(self.selected_spark_ids),
                "longTermMemories": len(self.selected_long_term_memory_ids),
                "foreshadowing": len(self.selected_foreshadowing_ids),
                "writingTechniqueInput": int(
                    self.writing_technique_input_id is not None
                ),
            },
            "preferredContext": {
                "chapterIds": list(self.associated_chapter_ids),
                "outlineIds": list(self.associated_outline_ids),
            },
            "policy": {
                "sourceBodiesRequireTools": True,
                "memorySearchTool": "searchWritingMemories",
                "memoryReadTool": "readWritingMemories",
                "novelKnowledgeSearchTool": "searchNovelKnowledge",
                "novelKnowledgeReadTool": "readNovelKnowledge",
                "chapterDirectoryTool": "listWritingChapters",
                "chapterReadTool": "readWritingChapters",
                "outlineDirectoryTool": "listWritingOutlines",
                "outlineReadTool": "readWritingOutlines",
                "writingTechniqueCandidateTool": (
                    "listWritingTechniqueCandidates"
                ),
                "writingTechniqueTool": "readWritingTechniqueFile",
                "continuationDirectoryTool": "listContinuationSourceSections",
                "continuationSectionTool": "readContinuationSourceSection",
            },
        }


def _identifiers(value: object, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise WritingContextSelectionError(
            "writing_context_selection_invalid",
            f"{field} must be an array",
        )
    normalized = tuple(
        str(item).strip() for item in value if str(item).strip()
    )
    if len(normalized) > MAX_CONTEXT_SELECTIONS:
        raise WritingContextSelectionError(
            "writing_context_selection_too_large",
            f"{field} must contain at most {MAX_CONTEXT_SELECTIONS} identifiers",
        )
    return tuple(dict.fromkeys(normalized))


def _optional_identifier(value: object, field: str) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized:
        return None
    if len(normalized) > 128:
        raise WritingContextSelectionError(
            "writing_context_selection_invalid",
            f"{field} is too long",
        )
    return normalized


__all__ = [
    "MAX_CONTEXT_SELECTIONS",
    "WRITING_CONTEXT_SELECTION_STATE_KEY",
    "WritingContextSelection",
    "WritingContextSelectionError",
]
