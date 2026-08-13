"""Pure compiler from screenplay intent facts to a semantic Part DAG."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Mapping, Sequence

from domains.screenplay_agent.contracts import (
    ScreenplayIntent,
    ScreenplayPlanBinding,
    ScreenplayPlanPhase,
)
from domains.screenplay_agent.manifest import (
    ScreenplayArtifactManifest,
    ScreenplayPartKind,
    ScreenplayPartSpec,
)
from purra.contracts import ExecutionRecipe, ExecutionRecipeStep
from purra.json_values import thaw_json_mapping


REVIEW_DIMENSIONS = (
    "continuity",
    "character_arc",
    "structure_rhythm",
    "dialogue",
    "format",
)

DOCUMENT_SECTIONS = {
    "sourceAnalysis": (
        "characters", "story", "world", "themes", "adaptation_risks",
    ),
    "creativeBrief": (
        "positioning", "premise", "characters", "world", "adaptation_rules",
    ),
    "structure": (
        "series_arc", "episode_plan", "character_arcs", "hooks",
    ),
    "sceneList": ("episode_plan",),
}


@dataclass(frozen=True, slots=True)
class CompiledScreenplayManifest:
    target_role: str
    manifest: ScreenplayArtifactManifest
    recipe: ExecutionRecipe


def compile_screenplay_manifest(
    *,
    intent: ScreenplayIntent,
    target_role: str,
    source_revision_refs: Sequence[str] = (),
    episode_scene_ids: Mapping[int, Sequence[str]] | None = None,
    reviewed_draft_id: str | None = None,
    base_revision_id: str | None = None,
    document_sections: Sequence[str] = (),
    original_request: str | None = None,
    plan_bindings: Sequence[ScreenplayPlanBinding] = (),
) -> CompiledScreenplayManifest:
    scenes = {
        int(number): tuple(str(value).strip() for value in values)
        for number, values in (episode_scene_ids or {}).items()
    }
    common = {
        "instruction": intent.instruction,
        "constraints": list(intent.constraints),
        "preserve": list(intent.preserve),
        "baseRevisionId": base_revision_id,
    }
    if target_role == "screenplayDraft":
        parts, strategy, parallelism = _draft_parts(scenes, common)
    elif target_role == "review":
        parts, strategy, parallelism = _review_parts(
            scenes,
            common,
            reviewed_draft_id=reviewed_draft_id,
        )
    else:
        parts, strategy, parallelism = _document_parts(
            target_role,
            tuple(document_sections) or DOCUMENT_SECTIONS.get(target_role, ()),
            common,
        )
    parts = _append_terminal_parts(
        parts,
        target_role=target_role,
        common=common,
        original_request=original_request,
    )
    bindings = tuple(plan_bindings) or intent.plan_bindings
    part_step_ids, binding_digest = _bind_parts_to_plan(
        parts,
        bindings,
        target_role=target_role,
    )
    canonical = {
        "artifactKind": target_role,
        "sourceRevisionRefs": sorted(set(source_revision_refs)),
        "assemblyStrategy": strategy,
        "parts": [_part_mapping(part) for part in parts],
    }
    digest = "sha256:" + hashlib.sha256(
        json.dumps(
            canonical,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    manifest = ScreenplayArtifactManifest(
        id=f"spmanifest_{digest.removeprefix('sha256:')[:24]}",
        artifact_kind=target_role,
        source_revision_refs=tuple(source_revision_refs),
        assembly_strategy=strategy,
        parts=parts,
        digest=digest,
    )
    return CompiledScreenplayManifest(
        target_role=target_role,
        manifest=manifest,
        recipe=ExecutionRecipe(
            kind=f"screenplay.{target_role}",
            steps=tuple(
                _recipe_step(part, plan_step_id=part_step_ids[part.id])
                for part in parts
            ),
            max_parallelism=parallelism,
            metadata={
                "targetRole": target_role,
                "recipeVersion": 4,
                "manifestId": manifest.id,
                "manifestDigest": manifest.digest,
                "assemblyStrategy": strategy,
                "planBindingDigest": binding_digest,
            },
        ),
    )


def _draft_parts(scenes, common):
    if not scenes or any(not values for values in scenes.values()):
        raise ValueError("screenplay Draft Manifest requires stable scene ids")
    parts: list[ScreenplayPartSpec] = []
    previous_validation: str | None = None
    for episode_number in sorted(scenes):
        evidence_id = f"evidence:{episode_number}"
        evidence_dependencies = (
            (previous_validation,) if previous_validation else ()
        )
        parts.append(_part(
            evidence_id,
            ScreenplayPartKind.EVIDENCE,
            len(parts),
            evidence_dependencies,
            metadata={
                "episodeNumber": episode_number,
                "sceneIds": list(scenes[episode_number]),
                **common,
            },
        ))
        previous_scene = evidence_id
        for scene_id in scenes[episode_number]:
            part_id = f"draft:{episode_number}:{scene_id}"
            parts.append(_part(
                part_id,
                ScreenplayPartKind.DRAFT_SCENE,
                len(parts),
                (previous_scene,),
                metadata={
                    "episodeNumber": episode_number,
                    "sceneId": scene_id,
                    "sceneIds": list(scenes[episode_number]),
                    **common,
                },
            ))
            previous_scene = part_id
        metadata_id = f"episode:{episode_number}:metadata"
        parts.append(_part(
            metadata_id,
            ScreenplayPartKind.EPISODE_METADATA,
            len(parts),
            (previous_scene,),
            metadata={
                "episodeNumber": episode_number,
                "sceneIds": list(scenes[episode_number]),
                **common,
            },
        ))
        previous_validation = f"episode:{episode_number}:validation"
        parts.append(_part(
            previous_validation,
            ScreenplayPartKind.VALIDATION,
            len(parts),
            (metadata_id,),
            metadata={
                "validationKind": "draft_episode",
                "episodeNumber": episode_number,
                "sceneIds": list(scenes[episode_number]),
                **common,
            },
        ))
    return parts, "draft_by_episode_scene", 1


def _review_parts(scenes, common, *, reviewed_draft_id):
    if not reviewed_draft_id or not scenes:
        raise ValueError("Review Manifest requires an immutable Draft Revision")
    parts: list[ScreenplayPartSpec] = []
    for episode_number in sorted(scenes):
        evidence_id = f"review-input:{episode_number}"
        parts.append(_part(
            evidence_id,
            ScreenplayPartKind.EVIDENCE,
            len(parts),
            input_ref=f"revision://{reviewed_draft_id}/episode/{episode_number}",
            metadata={
                "evidenceKind": "review_input",
                "episodeNumber": episode_number,
                "sceneIds": list(scenes[episode_number]),
                "reviewedDraftId": reviewed_draft_id,
                **common,
            },
        ))
        dimension_ids = []
        for dimension in REVIEW_DIMENSIONS:
            part_id = f"review:{episode_number}:{dimension}"
            dimension_ids.append(part_id)
            parts.append(_part(
                part_id,
                ScreenplayPartKind.REVIEW_DIMENSION,
                len(parts),
                (evidence_id,),
                metadata={
                    "episodeNumber": episode_number,
                    "sceneIds": list(scenes[episode_number]),
                    "reviewDimension": dimension,
                    "reviewedDraftId": reviewed_draft_id,
                    **common,
                },
            ))
        parts.append(_part(
            f"review:{episode_number}:validation",
            ScreenplayPartKind.VALIDATION,
            len(parts),
            tuple(dimension_ids),
            metadata={
                "validationKind": "review_episode",
                "episodeNumber": episode_number,
                "sceneIds": list(scenes[episode_number]),
                "reviewedDraftId": reviewed_draft_id,
                **common,
            },
        ))
    return parts, "review_by_episode_dimension", 5


def _document_parts(target_role, sections, common):
    normalized = tuple(dict.fromkeys(str(value).strip() for value in sections))
    if not normalized or any(not value for value in normalized):
        raise ValueError("document Manifest requires explicit section keys")
    parts = [_part(
        "document:evidence",
        ScreenplayPartKind.EVIDENCE,
        0,
        metadata={"targetRole": target_role, **common},
    )]
    section_ids = []
    for section in normalized:
        part_id = f"section:{target_role}:{section}"
        section_ids.append(part_id)
        parts.append(_part(
            part_id,
            ScreenplayPartKind.DOCUMENT_SECTION,
            len(parts),
            ("document:evidence",),
            metadata={"targetRole": target_role, "sectionKey": section, **common},
        ))
    parts.append(_part(
        "document:validation",
        ScreenplayPartKind.VALIDATION,
        len(parts),
        tuple(section_ids),
        metadata={"validationKind": "document", "targetRole": target_role, **common},
    ))
    return parts, "document_by_section", min(4, len(section_ids))


def _append_terminal_parts(parts, *, target_role, common, original_request):
    terminal_dependencies = tuple(
        part.id for part in parts if part.kind is ScreenplayPartKind.VALIDATION
    )
    final_id = "compose-final-response"
    parts.append(_part(
        final_id,
        ScreenplayPartKind.FINAL_RESPONSE,
        len(parts),
        terminal_dependencies,
        metadata={
            "targetRole": target_role,
            "userRequest": str(original_request or common["instruction"]),
            **common,
        },
    ))
    return tuple(parts)


def _part(part_id, kind, position, dependencies=(), *, input_ref=None, metadata):
    return ScreenplayPartSpec(
        id=part_id,
        semantic_key=part_id,
        kind=kind,
        position=position,
        dependencies=tuple(dependencies),
        input_ref=input_ref,
        metadata=metadata,
    )


def _part_mapping(part):
    return {
        "id": part.id,
        "semanticKey": part.semantic_key,
        "kind": part.kind.value,
        "position": part.position,
        "dependencies": list(part.dependencies),
        "inputRef": part.input_ref,
        "required": part.required,
        "metadata": thaw_json_mapping(part.metadata),
    }


def _recipe_step(part, *, plan_step_id):
    execution_kind = {
        ScreenplayPartKind.EVIDENCE: "collect_evidence",
        ScreenplayPartKind.DRAFT_SCENE: "generate_draft_scene",
        ScreenplayPartKind.EPISODE_METADATA: "generate_episode_metadata",
        ScreenplayPartKind.REVIEW_DIMENSION: "generate_review_dimension",
        ScreenplayPartKind.DOCUMENT_SECTION: "generate_document_section",
        ScreenplayPartKind.VALIDATION: "validate_manifest_part",
        ScreenplayPartKind.FINAL_RESPONSE: "compose_final_response",
    }[part.kind]
    metadata = thaw_json_mapping(part.metadata)
    return ExecutionRecipeStep(
        id=part.id,
        kind=execution_kind,
        executor="screenplay",
        depends_on=part.dependencies,
        input_ref=part.input_ref,
        plan_step_id=plan_step_id,
        max_attempts=(4 if part.kind in {
            ScreenplayPartKind.DRAFT_SCENE,
            ScreenplayPartKind.REVIEW_DIMENSION,
            ScreenplayPartKind.DOCUMENT_SECTION,
        } else 2),
        metadata={
            "input": metadata,
            "partKind": part.kind.value,
            "semanticKey": part.semantic_key,
            "effectClass": (
                "idempotent_write"
                if part.kind in {
                    ScreenplayPartKind.DRAFT_SCENE,
                    ScreenplayPartKind.REVIEW_DIMENSION,
                    ScreenplayPartKind.DOCUMENT_SECTION,
                }
                else "read_only"
            ),
            "completionEvidence": "artifact_ref",
            "checkpointPolicy": "reuse_completed",
            "retryPolicy": "bounded_attempts",
        },
    )


def _bind_parts_to_plan(parts, bindings, *, target_role):
    if not bindings or any(
        not isinstance(binding, ScreenplayPlanBinding)
        for binding in bindings
    ):
        raise ValueError("screenplay Manifest requires plan bindings")
    by_phase = {
        phase: tuple(
            binding.step_id for binding in bindings if binding.phase is phase
        )
        for phase in ScreenplayPlanPhase
    }
    parts_by_phase = {
        phase: tuple(
            part for part in parts
            if _part_phase(part, target_role=target_role) is phase
        )
        for phase in ScreenplayPlanPhase
    }
    mapped: dict[str, str] = {}
    for phase in ScreenplayPlanPhase:
        step_ids = by_phase[phase]
        phase_parts = parts_by_phase[phase]
        if bool(step_ids) != bool(phase_parts) or len(step_ids) > len(phase_parts):
            raise ValueError(
                f"screenplay plan phase {phase.value} cannot cover Manifest Parts"
            )
        for index, part in enumerate(phase_parts):
            mapped[part.id] = step_ids[index * len(step_ids) // len(phase_parts)]
    digest_source = [binding.to_mapping() for binding in bindings]
    digest = "sha256:" + hashlib.sha256(json.dumps(
        digest_source,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")).hexdigest()
    return mapped, digest


def _part_phase(part, *, target_role):
    if part.kind is ScreenplayPartKind.EVIDENCE:
        return ScreenplayPlanPhase.EVIDENCE
    if part.kind is ScreenplayPartKind.REVIEW_DIMENSION:
        return ScreenplayPlanPhase.REVIEW
    if part.kind is ScreenplayPartKind.FINAL_RESPONSE:
        return ScreenplayPlanPhase.DELIVERY
    if part.kind is ScreenplayPartKind.VALIDATION and target_role == "review":
        return ScreenplayPlanPhase.REVIEW
    return ScreenplayPlanPhase.CREATION


__all__ = [
    "CompiledScreenplayManifest",
    "DOCUMENT_SECTIONS",
    "REVIEW_DIMENSIONS",
    "compile_screenplay_manifest",
]
