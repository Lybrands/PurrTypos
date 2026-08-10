"""Stable screenplay Artifact manifest contracts owned by the product domain."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from graphlib import CycleError, TopologicalSorter
from typing import Any, Mapping

from purra.json_values import freeze_json_mapping
from purra.normalization import non_negative_int, optional_text, required_text


class ScreenplayPartKind(StrEnum):
    EVIDENCE = "evidence"
    DRAFT_SCENE = "draft_scene"
    EPISODE_METADATA = "episode_metadata"
    REVIEW_DIMENSION = "review_dimension"
    DOCUMENT_SECTION = "document_section"
    VALIDATION = "validation"
    FINAL_RESPONSE = "final_response"


@dataclass(frozen=True, slots=True)
class ScreenplayPartSpec:
    id: str
    semantic_key: str
    kind: ScreenplayPartKind
    position: int
    dependencies: tuple[str, ...] = ()
    input_ref: str | None = None
    required: bool = True
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("id", "semantic_key"):
            object.__setattr__(
                self,
                name,
                required_text(getattr(self, name), f"screenplay Part {name}"),
            )
        object.__setattr__(self, "kind", ScreenplayPartKind(self.kind))
        object.__setattr__(
            self,
            "position",
            non_negative_int(self.position, "screenplay Part position"),
        )
        dependencies = tuple(dict.fromkeys(
            required_text(value, "screenplay Part dependency")
            for value in self.dependencies
        ))
        if self.id in dependencies:
            raise ValueError("screenplay Part cannot depend on itself")
        object.__setattr__(self, "dependencies", dependencies)
        object.__setattr__(self, "input_ref", optional_text(self.input_ref))
        object.__setattr__(self, "required", bool(self.required))
        object.__setattr__(self, "metadata", freeze_json_mapping(self.metadata))


@dataclass(frozen=True, slots=True)
class ScreenplayArtifactManifest:
    id: str
    artifact_kind: str
    source_revision_refs: tuple[str, ...]
    assembly_strategy: str
    parts: tuple[ScreenplayPartSpec, ...]
    digest: str

    def __post_init__(self) -> None:
        for name in ("id", "artifact_kind", "assembly_strategy", "digest"):
            object.__setattr__(
                self,
                name,
                required_text(getattr(self, name), f"screenplay Manifest {name}"),
            )
        refs = tuple(sorted(set(
            required_text(value, "screenplay source Revision ref")
            for value in self.source_revision_refs
        )))
        object.__setattr__(self, "source_revision_refs", refs)
        parts = tuple(self.parts)
        if not parts:
            raise ValueError("screenplay Manifest requires Parts")
        ids = tuple(part.id for part in parts)
        keys = tuple(part.semantic_key for part in parts)
        positions = tuple(part.position for part in parts)
        if len(ids) != len(set(ids)):
            raise ValueError("screenplay Part ids must be unique")
        if len(keys) != len(set(keys)):
            raise ValueError("screenplay Part semantic keys must be unique")
        if len(positions) != len(set(positions)):
            raise ValueError("screenplay Part positions must be unique")
        unknown = {
            dependency
            for part in parts
            for dependency in part.dependencies
            if dependency not in set(ids)
        }
        if unknown:
            raise ValueError("screenplay Part dependency is unknown")
        try:
            tuple(TopologicalSorter({
                part.id: part.dependencies for part in parts
            }).static_order())
        except CycleError as error:
            raise ValueError("screenplay Part dependencies contain a cycle") from error
        object.__setattr__(self, "parts", parts)


__all__ = [
    "ScreenplayArtifactManifest",
    "ScreenplayPartKind",
    "ScreenplayPartSpec",
]
