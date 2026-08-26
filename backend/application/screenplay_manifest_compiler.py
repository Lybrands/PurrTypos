"""Pure compiler from screenplay intent facts to a semantic Part DAG."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
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
from purra.contracts import ExecutionRecipe, ExecutionRecipeStep, TaskStep
from purra.json_values import thaw_json_mapping
from application.screenplay_part_contracts import (
    resolve_screenplay_part_contract,
)


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

DOCUMENT_SECTION_TITLES = {
    "sourceAnalysis": {
        "characters": "分析人物",
        "story": "梳理故事",
        "world": "分析世界观",
        "themes": "提炼主题",
        "adaptation_risks": "评估改编风险",
    },
    "creativeBrief": {
        "positioning": "明确项目定位",
        "premise": "提炼核心命题",
        "characters": "设计核心人物",
        "world": "建立剧本世界",
        "adaptation_rules": "制定改编规则",
    },
    "structure": {
        "series_arc": "设计全剧主线",
        "episode_plan": "规划分集结构",
        "character_arcs": "设计人物弧",
        "hooks": "设计剧情钩子",
    },
    "sceneList": {"episode_plan": "规划场景"},
}

REVIEW_DIMENSION_TITLES = {
    "continuity": "检查连续性",
    "character_arc": "审阅人物弧",
    "structure_rhythm": "审阅结构节奏",
    "dialogue": "审阅对白",
    "format": "检查剧本格式",
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
    source_chapters: Sequence[Mapping[str, object]] = (),
    original_request: str | None = None,
    plan_bindings: Sequence[ScreenplayPlanBinding] = (),
    plan_steps: Sequence[TaskStep],
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
    elif target_role == "structure":
        parts, strategy, parallelism = _structure_parts(common)
    elif target_role == "sourceAnalysis":
        parts, strategy, parallelism = _source_analysis_parts(
            source_chapters,
            common,
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
    bindings = _order_bindings_by_root_plan(
        tuple(plan_bindings) or intent.plan_bindings,
        plan_steps,
    )
    part_step_ids, binding_digest = _bind_parts_to_plan(
        parts,
        bindings,
        target_role=target_role,
    )
    parts = _apply_public_step_barriers(
        parts,
        part_step_ids,
        bindings,
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
                _recipe_step(
                    part,
                    target_role=target_role,
                    plan_step_id=part_step_ids[part.id],
                )
                for part in parts
            ),
            max_parallelism=parallelism,
            metadata={
                "targetRole": target_role,
                "recipeVersion": 6,
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
    structure_episode_expansion_id = "section:structure:episode_plan"
    for section in normalized:
        part_id = f"section:{target_role}:{section}"
        if target_role == "structure" and section == "episode_plan":
            index_id = f"{part_id}:index"
            index_dependencies = ["document:evidence"]
            series_arc_id = "section:structure:series_arc"
            if "series_arc" in normalized:
                index_dependencies.append(series_arc_id)
            parts.append(_part(
                index_id,
                ScreenplayPartKind.DOCUMENT_SECTION,
                len(parts),
                tuple(index_dependencies),
                metadata={
                    "targetRole": target_role,
                    "sectionKey": "episode_plan:index",
                    "documentSectionKey": section,
                    "episodePlanIndex": True,
                    **common,
                },
            ))
            section_ids.append(part_id)
            parts.append(_part(
                part_id,
                ScreenplayPartKind.EXPANSION,
                len(parts),
                (index_id,),
                metadata={
                    "targetRole": target_role,
                    "sectionKey": section,
                    "splitStrategy": "structure_episode_plan",
                    **common,
                },
            ))
            continue
        section_ids.append(part_id)
        dependencies = ["document:evidence"]
        if (
            target_role == "structure"
            and section in {"character_arcs", "hooks"}
            and "episode_plan" in normalized
        ):
            dependencies.append(structure_episode_expansion_id)
        metadata = {"targetRole": target_role, "sectionKey": section, **common}
        if target_role == "sceneList":
            try:
                episode_number = int(section.removeprefix("episode-"))
            except ValueError as error:
                raise ValueError(
                    "sceneList Manifest section must identify one episode"
                ) from error
            if (
                not section.startswith("episode-")
                or episode_number < 1
                or section != f"episode-{episode_number}"
            ):
                raise ValueError(
                    "sceneList Manifest section must identify one episode"
                )
            metadata["episodeNumber"] = episode_number
        parts.append(_part(
            part_id,
            ScreenplayPartKind.DOCUMENT_SECTION,
            len(parts),
            tuple(dependencies),
            metadata=metadata,
        ))
    parts.append(_part(
        "document:validation",
        ScreenplayPartKind.VALIDATION,
        len(parts),
        tuple(section_ids),
        metadata={"validationKind": "document", "targetRole": target_role, **common},
    ))
    return parts, "document_by_section", min(4, len(section_ids))


def _source_analysis_parts(source_chapters, common):
    chapters = tuple(_source_chapter_identity(value) for value in source_chapters)
    if not chapters:
        raise ValueError("sourceAnalysis Manifest requires authorized leaf chapters")
    chapter_ids = tuple(chapter["id"] for chapter in chapters)
    if len(chapter_ids) != len(set(chapter_ids)):
        raise ValueError("sourceAnalysis chapter ids must be unique")
    if tuple(chapter["index"] for chapter in chapters) != tuple(sorted(
        chapter["index"] for chapter in chapters
    )):
        raise ValueError("sourceAnalysis chapters must preserve source order")

    parts = [_part(
        "document:evidence",
        ScreenplayPartKind.EVIDENCE,
        0,
        metadata={"targetRole": "sourceAnalysis", **common},
    )]
    frontier = []
    for chapter in chapters:
        part_id = f'source-analysis:chapter:{chapter["id"]}'
        frontier.append(part_id)
        parts.append(_part(
            part_id,
            ScreenplayPartKind.DOCUMENT_SECTION,
            len(parts),
            ("document:evidence",),
            metadata={
                "targetRole": "sourceAnalysis",
                "sectionKey": f'source_digest:chapter:{chapter["id"]}',
                "sourceChapterDigest": True,
                "chapterId": chapter["id"],
                "chapterTitle": chapter["title"],
                "chapterIndex": chapter["index"],
                **common,
            },
        ))

    level = 1
    while len(frontier) > 12:
        next_frontier = []
        for offset in range(0, len(frontier), 12):
            reduction_index = offset // 12 + 1
            part_id = f"source-analysis:reduce:{level}:{reduction_index}"
            next_frontier.append(part_id)
            parts.append(_part(
                part_id,
                ScreenplayPartKind.DOCUMENT_SECTION,
                len(parts),
                tuple(frontier[offset:offset + 12]),
                metadata={
                    "targetRole": "sourceAnalysis",
                    "sectionKey": f"source_digest:reduce:{level}:{reduction_index}",
                    "sourceDigestReduction": True,
                    "digestId": part_id,
                    "reductionLevel": level,
                    "reductionIndex": reduction_index,
                    **common,
                },
            ))
        frontier = next_frontier
        level += 1

    section_ids = []
    for section in DOCUMENT_SECTIONS["sourceAnalysis"]:
        part_id = f"section:sourceAnalysis:{section}"
        section_ids.append(part_id)
        parts.append(_part(
            part_id,
            ScreenplayPartKind.DOCUMENT_SECTION,
            len(parts),
            tuple(frontier),
            metadata={
                "targetRole": "sourceAnalysis",
                "sectionKey": section,
                **common,
            },
        ))
    parts.append(_part(
        "document:validation",
        ScreenplayPartKind.VALIDATION,
        len(parts),
        tuple(section_ids),
        metadata={
            "validationKind": "document",
            "targetRole": "sourceAnalysis",
            **common,
        },
    ))
    return parts, "source_analysis_by_chapter_digest", 12


def _source_chapter_identity(value):
    if not isinstance(value, Mapping):
        raise ValueError("sourceAnalysis chapter identity must be an object")
    chapter_id = str(value.get("id") or "").strip()
    title = str(value.get("title") or "").strip()
    index = int(value.get("index") or 0)
    if not chapter_id or len(chapter_id) > 120 or not title or index <= 0:
        raise ValueError("sourceAnalysis chapter identity is invalid")
    return {"id": chapter_id, "title": title, "index": index}


def _structure_parts(common):
    specs = (
        (
            "document:evidence",
            ScreenplayPartKind.EVIDENCE,
            (),
            {"targetRole": "structure", **common},
        ),
        (
            "section:structure:series_arc:index",
            ScreenplayPartKind.DOCUMENT_SECTION,
            ("document:evidence",),
            {
                "targetRole": "structure",
                "sectionKey": "series_arc:index",
                "documentSectionKey": "series_arc",
                "seriesArcIndex": True,
                **common,
            },
        ),
        (
            "section:structure:series_arc",
            ScreenplayPartKind.EXPANSION,
            ("section:structure:series_arc:index",),
            {
                "targetRole": "structure",
                "sectionKey": "series_arc",
                "splitStrategy": "structure_series_arc",
                **common,
            },
        ),
        (
            "section:structure:episode_plan:index",
            ScreenplayPartKind.DOCUMENT_SECTION,
            ("section:structure:series_arc",),
            {
                "targetRole": "structure",
                "sectionKey": "episode_plan:index",
                "documentSectionKey": "episode_plan",
                "episodePlanIndex": True,
                **common,
            },
        ),
        (
            "section:structure:episode_plan",
            ScreenplayPartKind.EXPANSION,
            ("section:structure:episode_plan:index",),
            {
                "targetRole": "structure",
                "sectionKey": "episode_plan",
                "splitStrategy": "structure_episode_plan",
                **common,
            },
        ),
        (
            "section:structure:character_arcs:index",
            ScreenplayPartKind.DOCUMENT_SECTION,
            ("section:structure:episode_plan",),
            {
                "targetRole": "structure",
                "sectionKey": "character_arcs:index",
                "documentSectionKey": "character_arcs",
                "characterArcsIndex": True,
                **common,
            },
        ),
        (
            "section:structure:character_arcs",
            ScreenplayPartKind.EXPANSION,
            ("section:structure:character_arcs:index",),
            {
                "targetRole": "structure",
                "sectionKey": "character_arcs",
                "splitStrategy": "structure_character_arcs",
                **common,
            },
        ),
        (
            "section:structure:hooks",
            ScreenplayPartKind.HOST_PROJECTION,
            (
                "section:structure:episode_plan",
                "section:structure:character_arcs",
            ),
            {
                "targetRole": "structure",
                "sectionKey": "hooks",
                **common,
            },
        ),
        (
            "document:validation",
            ScreenplayPartKind.VALIDATION,
            (
                "section:structure:series_arc",
                "section:structure:episode_plan",
                "section:structure:character_arcs",
                "section:structure:hooks",
            ),
            {
                "validationKind": "document",
                "targetRole": "structure",
                **common,
            },
        ),
    )
    return [
        _part(part_id, kind, position, dependencies, metadata=metadata)
        for position, (part_id, kind, dependencies, metadata) in enumerate(specs)
    ], "structure_by_bounded_parts", 12


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


def _recipe_step(part, *, target_role, plan_step_id):
    expansion_kind = {
        "structure_series_arc": "expand_structure_series_arc",
        "structure_episode_plan": "expand_structure_episode_plan",
        "structure_character_arcs": "expand_structure_character_arcs",
    }.get(str(thaw_json_mapping(part.metadata).get("splitStrategy") or ""))
    execution_kind = {
        ScreenplayPartKind.EVIDENCE: "collect_evidence",
        ScreenplayPartKind.DRAFT_SCENE: "generate_draft_scene",
        ScreenplayPartKind.EPISODE_METADATA: "generate_episode_metadata",
        ScreenplayPartKind.REVIEW_DIMENSION: "generate_review_dimension",
        ScreenplayPartKind.DOCUMENT_SECTION: "generate_document_section",
        ScreenplayPartKind.EXPANSION: expansion_kind,
        ScreenplayPartKind.HOST_PROJECTION: "project_structure_hooks",
        ScreenplayPartKind.VALIDATION: "validate_manifest_part",
        ScreenplayPartKind.FINAL_RESPONSE: "compose_final_response",
    }[part.kind]
    if not execution_kind:
        raise ValueError("screenplay expansion strategy is unsupported")
    metadata = thaw_json_mapping(part.metadata)
    contract = (
        resolve_screenplay_part_contract(
            target_role,
            execution_kind,
            metadata,
        )
        if execution_kind in {
            "generate_draft_scene",
            "generate_episode_metadata",
            "generate_review_dimension",
            "generate_document_section",
            "compose_final_response",
        }
        else None
    )
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
            "displayTitle": _display_title(part, metadata),
            "partKind": part.kind.value,
            "semanticKey": part.semantic_key,
            "effectClass": (
                "idempotent_write"
                if part.kind in {
                    ScreenplayPartKind.DRAFT_SCENE,
                    ScreenplayPartKind.REVIEW_DIMENSION,
                    ScreenplayPartKind.DOCUMENT_SECTION,
                    ScreenplayPartKind.EXPANSION,
                    ScreenplayPartKind.HOST_PROJECTION,
                }
                else "read_only"
            ),
            "completionEvidence": (
                "expanded_parts"
                if part.kind is ScreenplayPartKind.EXPANSION
                else "artifact_ref"
            ),
            "checkpointPolicy": "reuse_completed",
            "retryPolicy": "bounded_attempts",
            **(
                {"partContractKey": contract.key}
                if contract is not None
                else {}
            ),
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


def _order_bindings_by_root_plan(bindings, plan_steps):
    steps = tuple(plan_steps)
    if not steps or any(not isinstance(step, TaskStep) for step in steps):
        raise ValueError("screenplay Manifest requires validated Root plan steps")
    by_id = {binding.step_id: binding for binding in bindings}
    step_ids = tuple(step.id for step in steps)
    if len(by_id) != len(bindings) or set(by_id) != set(step_ids):
        raise ValueError("screenplay bindings must match validated Root plan steps")
    return tuple(by_id[step_id] for step_id in step_ids)


def _apply_public_step_barriers(parts, part_step_ids, bindings):
    """Make private recipe progress obey the model-authored Root step order."""

    ordered_step_ids = tuple(dict.fromkeys(
        binding.step_id for binding in bindings
    ))
    parts_by_id = {part.id: part for part in parts}
    groups = {
        step_id: tuple(
            part for part in parts
            if part_step_ids[part.id] == step_id
        )
        for step_id in ordered_step_ids
    }
    rewritten: dict[str, ScreenplayPartSpec] = {}
    previous_terminals: tuple[str, ...] = ()
    for step_id in ordered_step_ids:
        group = groups[step_id]
        group_ids = {part.id for part in group}
        depended_on_within_group = {
            dependency
            for part in group
            for dependency in part.dependencies
            if dependency in group_ids
        }
        for part in group:
            internal = tuple(
                dependency
                for dependency in part.dependencies
                if dependency in group_ids
            )
            dependencies = (
                tuple(dict.fromkeys((*internal, *previous_terminals)))
                if not internal
                else internal
            )
            rewritten[part.id] = replace(part, dependencies=dependencies)
        previous_terminals = tuple(
            part.id for part in group
            if part.id not in depended_on_within_group
        )
    if set(rewritten) != set(parts_by_id):
        raise ValueError("screenplay public plan barriers left Parts unbound")
    return tuple(
        rewritten[part.id]
        for step_id in ordered_step_ids
        for part in groups[step_id]
    )


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


def _display_title(part, metadata):
    episode_number = metadata.get("episodeNumber")
    target_role = str(metadata.get("targetRole") or "")
    if part.kind is ScreenplayPartKind.EVIDENCE:
        if target_role == "sourceAnalysis":
            return "准备原作范围"
        if episode_number is not None:
            noun = (
                "剧本"
                if metadata.get("evidenceKind") == "review_input"
                else "素材"
            )
            return f"读取第 {episode_number} 集{noun}"
        return "读取项目内容"
    if part.kind is ScreenplayPartKind.DRAFT_SCENE:
        return f"生成第 {episode_number} 集场景"
    if part.kind is ScreenplayPartKind.EPISODE_METADATA:
        return f"整理第 {episode_number} 集信息"
    if part.kind is ScreenplayPartKind.REVIEW_DIMENSION:
        dimension = str(metadata.get("reviewDimension") or "")
        return REVIEW_DIMENSION_TITLES.get(dimension, "审阅剧本")
    if part.kind is ScreenplayPartKind.DOCUMENT_SECTION:
        section = str(metadata.get("sectionKey") or "")
        if metadata.get("sourceChapterDigest") is True:
            return f'分析第 {int(metadata.get("chapterIndex") or 0)} 章'
        if metadata.get("sourceDigestReduction") is True:
            return "归并原作摘要"
        if metadata.get("seriesArcIndex") is True:
            return "确定全剧阶段"
        if metadata.get("episodePlanIndex") is True:
            return "确定分集索引"
        if metadata.get("characterArcsIndex") is True:
            return "确定核心人物"
        return DOCUMENT_SECTION_TITLES.get(target_role, {}).get(
            section,
            "生成交付内容",
        )
    if part.kind is ScreenplayPartKind.EXPANSION:
        return {
            "structure_series_arc": "展开全剧阶段",
            "structure_episode_plan": "展开分集任务",
            "structure_character_arcs": "展开人物弧任务",
        }.get(str(metadata.get("splitStrategy") or ""), "展开结构任务")
    if part.kind is ScreenplayPartKind.HOST_PROJECTION:
        return "整理剧情钩子"
    if part.kind is ScreenplayPartKind.VALIDATION:
        if target_role == "sourceAnalysis":
            return "检查分析结果"
        if episode_number is not None:
            return f"检查第 {episode_number} 集结果"
        return "检查生成结果"
    return "生成回复"


__all__ = [
    "CompiledScreenplayManifest",
    "DOCUMENT_SECTIONS",
    "REVIEW_DIMENSIONS",
    "compile_screenplay_manifest",
]
