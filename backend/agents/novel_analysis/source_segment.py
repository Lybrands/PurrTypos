"""Immutable source ranges shared by novel-analysis request compilation."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AnalysisSegment:
    id: str
    section_id: str
    section_digest: str
    section_ordinal: int
    start_character: int
    end_character: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.id, str)
            or not self.id.strip()
            or not isinstance(self.section_id, str)
            or not self.section_id.strip()
            or not isinstance(self.section_digest, str)
            or not self.section_digest.strip()
            or type(self.section_ordinal) is not int
            or type(self.start_character) is not int
            or type(self.end_character) is not int
            or self.section_ordinal < 0
            or self.start_character < 0
            or self.end_character <= self.start_character
        ):
            raise ValueError("novel analysis source segment is invalid")
        object.__setattr__(self, "id", self.id.strip())
        object.__setattr__(self, "section_id", self.section_id.strip())
        object.__setattr__(self, "section_digest", self.section_digest.strip())

    def to_mapping(self) -> dict[str, object]:
        return {
            "id": self.id,
            "sectionId": self.section_id,
            "sectionDigest": self.section_digest,
            "sectionOrdinal": self.section_ordinal,
            "startCharacter": self.start_character,
            "endCharacter": self.end_character,
        }


__all__ = ["AnalysisSegment"]
