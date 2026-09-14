"""Single capability manifest for Screenplay replacement Parts."""

from __future__ import annotations

from collections.abc import Mapping

from agents.screenplay.contracts import (
    SCREENPLAY_PART_REGISTRY,
    ScreenplayPartCompletion,
    ScreenplayPartKind,
)


WRITE_SCREENPLAY_CANDIDATE_PART = "writeScreenplayCandidatePartV1"

_READ_TOOLS_BY_PROFILE: Mapping[str, tuple[str, ...]] = {
    "source_read_candidate": (
        "inspectScreenplayProjectV1",
        "listScreenplaySourceItemsV1",
        "readScreenplaySourceItemV1",
    ),
    "draft_scene_read": (
        "readScreenplayPartDependenciesV1",
        "readScreenplaySceneScopeV1",
    ),
    "episode_metadata_read": ("readScreenplayPartDependenciesV1",),
    "revision_read_candidate": (
        "readScreenplayBoundRevisionV1",
        "readScreenplayPartDependenciesV1",
    ),
    "bounded_material_candidate": (
        "inspectScreenplayProjectV1",
        "listScreenplaySourceItemsV1",
        "readScreenplaySourceItemV1",
        "readScreenplayBoundRevisionV1",
        "readScreenplayPartDependenciesV1",
    ),
    "dependency_read_candidate": ("readScreenplayPartDependenciesV1",),
    "host_only": (),
}


def screenplay_part_tool_names(kind: ScreenplayPartKind | str) -> tuple[str, ...]:
    definition = SCREENPLAY_PART_REGISTRY[ScreenplayPartKind(kind)]
    reads = _READ_TOOLS_BY_PROFILE[definition.tool_profile]
    return (
        (*reads, WRITE_SCREENPLAY_CANDIDATE_PART)
        if definition.completion is ScreenplayPartCompletion.CANDIDATE_TOOL
        else reads
    )


def screenplay_part_tool_guidance(kind: ScreenplayPartKind | str) -> str:
    definition = SCREENPLAY_PART_REGISTRY[ScreenplayPartKind(kind)]
    names = screenplay_part_tool_names(definition.kind)
    if definition.completion is ScreenplayPartCompletion.HOST_ONLY:
        return "This Part is host-only and has no model tools."
    completion = {
        ScreenplayPartCompletion.CANDIDATE_TOOL: (
            f"Submit exactly one candidate with {WRITE_SCREENPLAY_CANDIDATE_PART}."
        ),
        ScreenplayPartCompletion.HOST_CAPTURE: (
            "Return the requested structured output; the host captures it."
        ),
    }[definition.completion]
    return "Allowed tools: " + ", ".join(names) + ". " + completion


def validate_screenplay_capability_manifest() -> None:
    profiles = {item.tool_profile for item in SCREENPLAY_PART_REGISTRY.values()}
    missing = profiles - set(_READ_TOOLS_BY_PROFILE)
    extra = set(_READ_TOOLS_BY_PROFILE) - profiles
    if missing or extra:
        raise ValueError(
            "Screenplay replacement tool profiles disagree with Part registry"
        )
    for kind, definition in SCREENPLAY_PART_REGISTRY.items():
        names = screenplay_part_tool_names(kind)
        has_writer = WRITE_SCREENPLAY_CANDIDATE_PART in names
        if has_writer != (
            definition.completion is ScreenplayPartCompletion.CANDIDATE_TOOL
        ):
            raise ValueError("Screenplay candidate writer capability is inconsistent")


validate_screenplay_capability_manifest()


__all__ = [
    "WRITE_SCREENPLAY_CANDIDATE_PART",
    "screenplay_part_tool_guidance",
    "screenplay_part_tool_names",
    "validate_screenplay_capability_manifest",
]
