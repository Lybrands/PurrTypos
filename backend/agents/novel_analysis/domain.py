"""Immutable request scope for the replacement Novel Analysis Agent."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from agents.novel_analysis.source_segment import AnalysisSegment
from purra.contracts import AgentRunRequest
from purra.json_values import thaw_json_mapping


NOVEL_ANALYSIS_REPLACEMENT_DOMAIN_NAMESPACE = "purrtypos.novel_analysis"
NOVEL_ANALYSIS_PUBLISHABLE_FACT_KINDS = frozenset({
    "background",
    "character_summary",
    "relationship_summary",
    "story_summary",
    "setting",
    "location",
    "faction",
    "item",
    "character_identity",
    "character_state",
    "relationship",
    "world_rule",
    "event",
    "timeline",
    "unresolved_plot",
    "foreshadowing",
    "character_knowledge",
})


@dataclass(frozen=True, slots=True)
class NovelAnalysisRequestScope:
    source_revision_id: str
    command_id: str
    segments: tuple[AnalysisSegment, ...]

    def __post_init__(self) -> None:
        revision_id = str(self.source_revision_id or "").strip()
        command_id = str(self.command_id or "").strip()
        segments = tuple(self.segments)
        if not revision_id or not command_id or not segments:
            raise ValueError(
                "replacement analysis requires sourceRevisionId, commandId, and segments"
            )
        if len({item.id for item in segments}) != len(segments):
            raise ValueError("replacement analysis segment ids must be unique")
        object.__setattr__(self, "source_revision_id", revision_id)
        object.__setattr__(self, "command_id", command_id)
        object.__setattr__(self, "segments", segments)

    @property
    def section_ids(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(item.section_id for item in self.segments))

    def to_mapping(self) -> dict[str, object]:
        return {
            "sourceRevisionId": self.source_revision_id,
            "commandId": self.command_id,
            "segments": [item.to_mapping() for item in self.segments],
        }

    @classmethod
    def from_mapping(cls, value) -> "NovelAnalysisRequestScope":
        if not isinstance(value, dict):
            raise ValueError("replacement analysis source scope is unavailable")
        return cls._from_payload(value)

    @classmethod
    def from_request(cls, request: AgentRunRequest) -> "NovelAnalysisRequestScope":
        if (
            request.domain_context.namespace
            != NOVEL_ANALYSIS_REPLACEMENT_DOMAIN_NAMESPACE
        ):
            raise ValueError("unsupported Novel Analysis replacement namespace")
        return cls._from_payload(
            thaw_json_mapping(request.domain_context.payload)
        )

    @classmethod
    def _from_payload(cls, payload) -> "NovelAnalysisRequestScope":
        base = {"sourceRevisionId", "commandId", "segments"}
        if frozenset(payload) not in {
            frozenset(base),
            frozenset((*base, "unit")),
        }:
            raise ValueError(
                "novel analysis domain payload must use the canonical shape"
            )
        raw_segments = payload.get("segments")
        if not isinstance(raw_segments, list):
            raise ValueError("replacement analysis segments must be a list")
        segments = []
        for raw in raw_segments:
            if not isinstance(raw, Mapping) or set(raw) != {
                "id",
                "sectionId",
                "sectionDigest",
                "sectionOrdinal",
                "startCharacter",
                "endCharacter",
            }:
                raise ValueError("replacement analysis segment shape is invalid")
            segments.append(AnalysisSegment(
                id=raw["id"],
                section_id=raw["sectionId"],
                section_digest=raw["sectionDigest"],
                section_ordinal=raw["sectionOrdinal"],
                start_character=raw["startCharacter"],
                end_character=raw["endCharacter"],
            ))
        return cls(
            source_revision_id=payload.get("sourceRevisionId"),
            command_id=payload.get("commandId"),
            segments=tuple(segments),
        )


__all__ = [
    "NOVEL_ANALYSIS_PUBLISHABLE_FACT_KINDS",
    "NOVEL_ANALYSIS_REPLACEMENT_DOMAIN_NAMESPACE",
    "NovelAnalysisRequestScope",
]
