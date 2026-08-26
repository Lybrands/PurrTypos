"""Execute screenplay business units while PurrA owns orchestration."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from purra.json_values import thaw_json_mapping
from purra.long_tasks import (
    DurableUnitExecutionContext,
    LongTaskSplitResult,
    LongTaskUsage,
    LongTaskUnitResult,
    LongTaskUnitSpec,
)
from purra.recovery import FailureCategory, FailureSignal
from application.screenplay_agent_context import ScreenplayAgentContextQuery
from application.screenplay_part_artifacts import ScreenplayPartArtifactQuery
from domains.screenplay_agent.contracts import (
    ReviewEpisodeInputRef,
    ReviewEpisodeResult,
)
from domains.screenplay_agent.recovery import classify_screenplay_run_failure
from application.screenplay_structured_call import ScreenplayStructuredCallService
from application.screenplay_checkpoint_planning import ScreenplayCheckpointPlanner
from application.screenplay_tool_calling import ScreenplayToolCallingService
from application.screenplay_part_contracts import (
    ScreenplayPartContract,
    resolve_screenplay_part_contract,
)
from domains.screenplay.source_scope import parse_source_scope
from domains.screenplay_agent.agent_context import ScreenplayAgentDomainContext
from domains.screenplay_agent.candidate_projection import (
    SCREENPLAY_CANDIDATE_VALIDATION_PROTOCOL,
    parse_candidate_validation_contract,
)
from exceptions import AppError
from infrastructure.persistence.sqlite_long_task_repository import (
    SqliteLongTaskRepository,
)


_DOCUMENT_KIND = {
    "sourceAnalysis": "source_analysis",
    "creativeBrief": "creative_brief",
    "structure": "episode_outline",
    "sceneList": "scene_list",
    "screenplayDraft": "scene_draft",
    "review": "review",
}
_ROLE_LABELS = {
    "sourceAnalysis": "原作分析",
    "creativeBrief": "创作简报",
    "structure": "分集结构",
    "sceneList": "场景表",
    "screenplayDraft": "剧本正文",
    "review": "审阅报告",
}
SCREENPLAY_AI_PART_KINDS = frozenset({
    "generate_draft_scene",
    "generate_episode_metadata",
    "generate_review_dimension",
    "generate_document_section",
    "compose_final_response",
})
_MAX_STRUCTURE_PHASES = 12
_MAX_STRUCTURE_EPISODES = 100
_MAX_STRUCTURE_CHARACTERS = 12
_MAX_CREATIVE_BRIEF_CHARACTERS = 12
_MAX_CREATIVE_BRIEF_LIST_ITEMS = 24
_SAFE_STRUCTURE_KEY = re.compile(r"[A-Za-z0-9._-]{1,128}")
_CREATIVE_BRIEF_POSITIONING_FIELDS = (
    "approach",
    "format",
    "audience",
    "tone",
)
_CREATIVE_BRIEF_PREMISE_FIELDS = (
    "premise",
    "centralConflict",
    "dramaticQuestion",
)
_CREATIVE_BRIEF_CHARACTER_FIELDS = (
    "key",
    "name",
    "function",
    "desire",
    "obstacle",
    "changeDirection",
)
_SCENE_LIST_FIELDS = frozenset({
    "id",
    "episodeNumber",
    "heading",
    "objective",
    "conflict",
    "turn",
    "synopsis",
})
_REVIEW_ISSUE_FIELDS = frozenset({
    "id",
    "severity",
    "description",
    "sceneIds",
})


class StructurePartSplit(RuntimeError):
    def __init__(
        self,
        strategy: str,
        entries: Sequence[Mapping[str, Any]],
        *,
        index_run_id: str,
        dependency_ids: Sequence[str] = (),
    ) -> None:
        self.strategy = str(strategy or "").strip()
        self.code = f"{self.strategy}_split_required"
        super().__init__(self.code)
        self.entries = tuple(dict(value) for value in entries)
        self.index_run_id = str(index_run_id or "").strip()
        self.dependency_ids = tuple(dict.fromkeys(
            str(value or "").strip()
            for value in dependency_ids
            if str(value or "").strip()
        ))


class ScreenplayTaskModelCalls:
    def __init__(
        self,
        db,
        *,
        composition=None,
        structured_call_service: ScreenplayStructuredCallService | None = None,
        tool_calling_service: ScreenplayToolCallingService | None = None,
    ) -> None:
        self._db = db
        self._context = ScreenplayAgentContextQuery(db)
        self._models = structured_call_service or (
            ScreenplayStructuredCallService(
                db,
                composition=composition,
            )
            if composition is not None
            else None
        )
        self._tool_calls = tool_calling_service or (
            ScreenplayToolCallingService(db, composition=composition)
            if composition is not None
            else None
        )
        if self._models is None and self._tool_calls is None:
            raise ValueError("screenplay task requires a model execution service")

    async def execute(
        self,
        *,
        task: Mapping[str, Any],
        unit: Mapping[str, Any],
        runtime,
        signal=None,
        bind_run=None,
    ) -> Mapping[str, Any]:
        kind = str(unit.get("kind") or "")
        if _requires_run(kind) and not str(
            task.get("rootRunId") or ""
        ).strip():
            raise RuntimeError(
                "screenplay AI Part requires its Run identity"
            )
        if kind == "collect_evidence":
            return await self._collect_evidence(task, unit)
        if kind == "generate_draft_scene":
            return await self._generate_draft_scene(
                task, unit, runtime, signal, bind_run
            )
        if kind == "generate_episode_metadata":
            return await self._generate_episode_metadata(
                task, unit, runtime, signal, bind_run
            )
        if kind == "generate_review_dimension":
            return await self._generate_review_dimension(
                task, unit, runtime, signal, bind_run
            )
        if kind == "generate_document_section":
            return await self._generate_document_section(
                task, unit, runtime, signal, bind_run
            )
        if kind in {
            "expand_structure_series_arc",
            "expand_structure_episode_plan",
            "expand_structure_character_arcs",
        }:
            return self._expand_structure_part(task, unit)
        if kind == "project_structure_hooks":
            return self._project_structure_hooks(task, unit)
        if kind == "validate_manifest_part":
            return self._validate_manifest_part(task, unit)
        if kind == "compose_final_response":
            return await self._compose_final_response(
                task, unit, runtime, signal, bind_run
            )
        raise RuntimeError(f"unsupported screenplay task unit: {kind}")

    async def _collect_evidence(
        self,
        task: Mapping[str, Any],
        unit: Mapping[str, Any],
    ) -> dict[str, Any]:
        project_id = str(task["projectId"])
        role = str(task["targetRole"])
        unit_input = dict(unit.get("input") or {})
        base_revision_id = str(unit_input.get("baseRevisionId") or "") or None
        episode_number = int(unit_input.get("episodeNumber") or 0)
        source_revision_refs = tuple(
            str(value) for value in task.get("sourceRevisionRefs") or ()
        )
        by_role = await self._context.revision_identities(
            project_id,
            source_revision_refs,
        )
        scene_list_revision_id = by_role.get("sceneList")
        structure_revision_id = by_role.get("structure")
        descriptor: dict[str, Any] = {
            "projectId": project_id,
            "targetRole": role,
            "sourceRevisionRefs": list(source_revision_refs),
            "baseRevisionId": base_revision_id,
            "acceptedRevisionIds": dict(by_role),
            "structureRevisionId": structure_revision_id,
            "structureEpisodeNumbers": list(
                await self._context.revision_episode_numbers(
                    structure_revision_id
                )
                if structure_revision_id
                else ()
            ),
            "currentDraftRevisionId": by_role.get("screenplayDraft"),
        }
        if episode_number:
            reviewed_draft_id = str(
                unit_input.get("reviewedDraftId") or ""
            ) or None
            scene_ids = tuple(unit_input.get("sceneIds") or ())
            if not scene_ids:
                raise ValueError("screenplay evidence requires Manifest scene ids")
            descriptor.update({
                "episodeNumber": episode_number,
                "sceneIds": list(scene_ids),
                "sceneListRevisionId": scene_list_revision_id,
                "reviewedDraftId": reviewed_draft_id,
                "evidenceKind": str(unit_input.get("evidenceKind") or "writing"),
            })
            if unit_input.get("evidenceKind") == "review_input":
                if reviewed_draft_id is None:
                    raise ValueError("review input requires an immutable Draft Revision")
                descriptor["reviewInputRef"] = _review_input_ref(
                    reviewed_draft_id=reviewed_draft_id,
                    episode_number=episode_number,
                    scene_ids=scene_ids,
                    scene_plan_revision_id=scene_list_revision_id,
                    content_digest=(
                        await self._context.revision_episode_digest(
                            reviewed_draft_id,
                            episode_number,
                        )
                    ),
                ).to_mapping()
        encoded = json.dumps(
            descriptor,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        return {
            "evidenceDescriptor": descriptor,
            "evidenceReceipt": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        }

    def _evidence_descriptor(
        self,
        task: Mapping[str, Any],
        unit: Mapping[str, Any],
    ) -> dict[str, Any]:
        checkpoint = _dependency_output(task, unit, "collect_evidence")
        descriptor = checkpoint.get("evidenceDescriptor")
        if not isinstance(descriptor, Mapping):
            raise RuntimeError("screenplay evidence Artifact is missing")
        if str(descriptor.get("projectId") or "") != str(task["projectId"]):
            raise RuntimeError("screenplay evidence project scope changed")
        return dict(descriptor)

    async def _generate_draft_scene(
        self, task, unit, runtime, signal, bind_run
    ):
        unit_input = dict(unit.get("input") or {})
        episode_number = int(unit_input.get("episodeNumber") or 0)
        scene_id = str(unit_input.get("sceneId") or "").strip()
        descriptor = self._evidence_descriptor(task, unit)
        scene_ids = tuple(str(value) for value in unit_input.get("sceneIds") or ())
        if not scene_id or scene_id not in scene_ids:
            raise ValueError("draft scene is absent from the accepted scene Manifest")
        dependency_part_keys = _dependency_part_keys(task, unit)
        payload = {
            "task": "create_screenplay_scene",
            "episodeNumber": episode_number,
            "sceneId": scene_id,
            "scenePosition": scene_ids.index(scene_id) + 1,
            "sceneCount": len(scene_ids),
            "instruction": unit_input.get("instruction"),
            "constraints": unit_input.get("constraints") or [],
            "preserve": unit_input.get("preserve") or [],
            "baseRevisionId": unit_input.get("baseRevisionId"),
            "evidenceDescriptor": descriptor,
            "dependencyPartKeys": list(dependency_part_keys),
        }
        if self._tool_calls is None:
            raise RuntimeError("draft scene requires the screenplay tool loop")
        contract = self._part_contract(task, unit)
        result = await self._tool_calls.run_candidate(
            runtime=runtime,
            session_id=int(task["sessionId"]),
            system_instruction=_scene_tool_instruction(episode_number, scene_id),
            user_payload=payload,
            domain_context=await self._domain_context(
                task,
                unit,
                expected_part_type="scene",
                expected_part_key=scene_id,
                runtime=runtime,
                tool_profile=contract.tool_profile,
                deliverable_revision_scope=_late_stage_revision_scope(
                    descriptor,
                    allowed_roles={"sceneList", "screenplayDraft"},
                    required_roles={"sceneList"},
                    base_revision_id=str(
                        unit_input.get("baseRevisionId") or ""
                    ).strip() or None,
                ),
            ),
            conversation_turn_id=str(task["turnId"]),
            bind_run=bind_run,
            output_token_cap=contract.output_token_cap,
            reasoning_mode=contract.reasoning_mode,
            host_candidate_template=_host_scene_candidate_template(scene_id),
            candidate_validation_contract={
                "protocol": SCREENPLAY_CANDIDATE_VALIDATION_PROTOCOL,
                "kind": contract.validation_kind,
                "expectedSceneId": scene_id,
            },
            signal=signal,
        )
        candidate = result.candidate
        return {
            **dict(candidate["payload"]),
            "episodeNumber": episode_number,
            "sceneListId": str(descriptor.get("sceneListRevisionId") or ""),
            "runId": result.run_id,
            "sourceRunIds": list(_part_source_run_ids(
                task,
                dependency_part_keys,
                result.run_id,
            )),
            **_candidate_artifact_identity(candidate),
        }

    async def _generate_episode_metadata(
        self, task, unit, runtime, signal, bind_run
    ):
        unit_input = dict(unit.get("input") or {})
        episode_number = int(unit_input.get("episodeNumber") or 0)
        scenes = _completed_part_outputs(
            task,
            kind="generate_draft_scene",
            episode_number=episode_number,
        )
        expected_ids = tuple(unit_input.get("sceneIds") or ())
        if tuple(str(scene.get("sceneId") or "") for scene in scenes) != expected_ids:
            raise RuntimeError("episode metadata requires every ordered scene Part")
        dependency_part_keys = _dependency_part_keys(task, unit)
        user_payload = {
            "task": "finalize_screenplay_episode_metadata",
            "episodeNumber": episode_number,
            "sceneIds": [str(scene["sceneId"]) for scene in scenes],
            "dependencyPartKeys": list(dependency_part_keys),
        }
        if self._tool_calls is None:
            raise RuntimeError("episode metadata requires the screenplay tool loop")
        contract = self._part_contract(task, unit)
        result = await self._tool_calls.run_candidate(
            runtime=runtime,
            session_id=int(task["sessionId"]),
            system_instruction=_episode_metadata_tool_instruction(episode_number),
            user_payload=user_payload,
            domain_context=await self._domain_context(
                task,
                unit,
                expected_part_type="episode_metadata",
                expected_part_key=str(episode_number),
                runtime=runtime,
                tool_profile=contract.tool_profile,
            ),
            conversation_turn_id=str(task["turnId"]),
            bind_run=bind_run,
            output_token_cap=contract.output_token_cap,
            reasoning_mode=contract.reasoning_mode,
            candidate_validation_contract={
                "protocol": SCREENPLAY_CANDIDATE_VALIDATION_PROTOCOL,
                "kind": contract.validation_kind,
                "episodeNumber": episode_number,
            },
            signal=signal,
        )
        return {
            **dict(result.candidate["payload"]),
            "runId": result.run_id,
            "sourceRunIds": list(_part_source_run_ids(
                task,
                dependency_part_keys,
                result.run_id,
            )),
            **_candidate_artifact_identity(result.candidate),
        }

    async def _generate_review_dimension(
        self, task, unit, runtime, signal, bind_run
    ):
        unit_input = dict(unit.get("input") or {})
        episode_number = int(unit_input.get("episodeNumber") or 0)
        dimension = str(unit_input.get("reviewDimension") or "")
        reviewed_draft_id = str(unit_input.get("reviewedDraftId") or "")
        descriptor = self._evidence_descriptor(task, unit)
        review_input_ref = descriptor.get("reviewInputRef")
        if not isinstance(review_input_ref, Mapping):
            raise RuntimeError("review dimension has no immutable input packet")
        reviewed_input = ReviewEpisodeInputRef.from_mapping(review_input_ref)
        if self._tool_calls is None:
            raise RuntimeError("review dimension requires the screenplay tool loop")
        contract = self._part_contract(task, unit)
        result = await self._tool_calls.run_candidate(
            runtime=runtime,
            session_id=int(task["sessionId"]),
            system_instruction=_review_dimension_tool_instruction(
                episode_number,
                dimension,
                tuple(unit_input.get("sceneIds") or ()),
            ),
            user_payload={
                "task": "review_screenplay_dimension",
                "episodeNumber": episode_number,
                "dimension": dimension,
                "reviewedDraftId": reviewed_draft_id,
                "instruction": unit_input.get("instruction"),
                "evidenceDescriptor": descriptor,
            },
            domain_context=await self._domain_context(
                task,
                unit,
                expected_part_type="review_dimension",
                expected_part_key=f"{episode_number}:{dimension}",
                runtime=runtime,
                tool_profile=contract.tool_profile,
                deliverable_revision_scope=_late_stage_revision_scope(
                    descriptor,
                    allowed_roles={"sceneList", "screenplayDraft"},
                    required_roles={"sceneList", "screenplayDraft"},
                    base_revision_id=reviewed_draft_id,
                ),
            ),
            conversation_turn_id=str(task["turnId"]),
            bind_run=bind_run,
            output_token_cap=contract.output_token_cap,
            reasoning_mode=contract.reasoning_mode,
            candidate_validation_contract={
                "protocol": SCREENPLAY_CANDIDATE_VALIDATION_PROTOCOL,
                "kind": contract.validation_kind,
                "episodeNumber": episode_number,
                "dimension": dimension,
                "allowedSceneIds": list(unit_input.get("sceneIds") or ()),
                "reviewedDraftId": reviewed_draft_id,
                "reviewedContentDigest": reviewed_input.content_digest,
            },
            signal=signal,
        )
        payload = dict(result.candidate["payload"])
        return {
            **payload,
            "contentText": str(result.candidate.get("contentText") or ""),
            "episodeNumber": episode_number,
            "reviewDimension": dimension,
            "runId": result.run_id,
            **_candidate_artifact_identity(result.candidate),
        }

    async def _generate_document_section(
        self, task, unit, runtime, signal, bind_run
    ):
        if self._tool_calls is None:
            raise RuntimeError("document section requires the screenplay tool loop")
        unit_input = dict(unit.get("input") or {})
        role = str(task["targetRole"])
        section_key = str(unit_input.get("sectionKey") or "")
        descriptor = (
            None
            if role == "sourceAnalysis"
            else self._evidence_descriptor(task, unit)
        )
        source_chapter_digest = bool(
            role == "sourceAnalysis"
            and unit_input.get("sourceChapterDigest") is True
        )
        source_digest_reduction = bool(
            role == "sourceAnalysis"
            and unit_input.get("sourceDigestReduction") is True
        )
        scene_list_episode_number = (
            int(section_key.removeprefix("episode-"))
            if role == "sceneList" and section_key.startswith("episode-")
            else 0
        )
        structure_episode_number = (
            int(unit_input.get("episodeNumber") or 0)
            if role == "structure"
            and unit_input.get("documentSectionKey") == "episode_plan"
            else 0
        )
        episode_plan_index = bool(
            role == "structure" and unit_input.get("episodePlanIndex") is True
        )
        series_arc_index = bool(
            role == "structure" and unit_input.get("seriesArcIndex") is True
        )
        character_arcs_index = bool(
            role == "structure" and unit_input.get("characterArcsIndex") is True
        )
        phase_identity = {
            "key": str(unit_input.get("phaseKey") or ""),
            "title": str(unit_input.get("phaseTitle") or ""),
            "objective": str(unit_input.get("phaseObjective") or ""),
        }
        character_identity = {
            "key": str(unit_input.get("characterKey") or ""),
            "name": str(unit_input.get("characterName") or ""),
        }
        episode_identity = {
            "number": structure_episode_number,
            "id": str(unit_input.get("episodeId") or ""),
            "title": str(unit_input.get("episodeTitle") or ""),
        }
        if structure_episode_number and (
            not episode_identity["id"] or not episode_identity["title"]
        ):
            raise ValueError("structure episode Part conflicts with its index")
        contract = self._part_contract(task, unit)
        dependency_part_keys = _dependency_part_keys(task, unit)
        if source_chapter_digest:
            chapter_id = str(unit_input.get("chapterId") or "").strip()
            system_instruction = _source_chapter_digest_tool_instruction(
                chapter_id=chapter_id,
                chapter_title=str(unit_input.get("chapterTitle") or ""),
                chapter_index=int(unit_input.get("chapterIndex") or 0),
            )
            validation_contract = {
                "protocol": SCREENPLAY_CANDIDATE_VALIDATION_PROTOCOL,
                "kind": contract.validation_kind,
                "chapterId": chapter_id,
            }
        elif source_digest_reduction:
            digest_id = str(unit_input.get("digestId") or "").strip()
            system_instruction = _source_digest_reduction_tool_instruction(
                digest_id
            )
            validation_contract = {
                "protocol": SCREENPLAY_CANDIDATE_VALIDATION_PROTOCOL,
                "kind": contract.validation_kind,
                "digestId": digest_id,
            }
        elif role == "sourceAnalysis":
            system_instruction = _source_analysis_section_tool_instruction(
                section_key
            )
            validation_contract = {
                "protocol": SCREENPLAY_CANDIDATE_VALIDATION_PROTOCOL,
                "kind": contract.validation_kind,
                "sectionKey": section_key,
            }
        elif scene_list_episode_number:
            system_instruction = _scene_list_fragment_tool_instruction(
                scene_list_episode_number
            )
            validation_contract = {
                "protocol": SCREENPLAY_CANDIDATE_VALIDATION_PROTOCOL,
                "kind": contract.validation_kind,
                "episodeNumber": scene_list_episode_number,
            }
        elif series_arc_index:
            system_instruction = _structure_series_arc_index_tool_instruction()
            validation_contract = {
                "protocol": SCREENPLAY_CANDIDATE_VALIDATION_PROTOCOL,
                "kind": contract.validation_kind,
            }
        elif phase_identity["key"]:
            system_instruction = _structure_series_arc_phase_tool_instruction(
                phase_identity
            )
            validation_contract = {
                "protocol": SCREENPLAY_CANDIDATE_VALIDATION_PROTOCOL,
                "kind": contract.validation_kind,
                "phaseKey": phase_identity["key"],
                "phaseTitle": phase_identity["title"],
                "phaseObjective": phase_identity["objective"],
            }
        elif episode_plan_index:
            system_instruction = _structure_episode_plan_index_tool_instruction()
            validation_contract = {
                "protocol": SCREENPLAY_CANDIDATE_VALIDATION_PROTOCOL,
                "kind": contract.validation_kind,
            }
        elif structure_episode_number:
            system_instruction = _structure_episode_plan_fragment_tool_instruction(
                episode_identity
            )
            validation_contract = {
                "protocol": SCREENPLAY_CANDIDATE_VALIDATION_PROTOCOL,
                "kind": contract.validation_kind,
                "episodeNumber": structure_episode_number,
                "episodeId": episode_identity["id"],
                "episodeTitle": episode_identity["title"],
            }
        elif character_arcs_index:
            system_instruction = _structure_character_arcs_index_tool_instruction()
            validation_contract = {
                "protocol": SCREENPLAY_CANDIDATE_VALIDATION_PROTOCOL,
                "kind": contract.validation_kind,
            }
        elif character_identity["key"]:
            system_instruction = _structure_character_arc_tool_instruction(
                character_identity
            )
            validation_contract = {
                "protocol": SCREENPLAY_CANDIDATE_VALIDATION_PROTOCOL,
                "kind": contract.validation_kind,
                "characterKey": character_identity["key"],
                "characterName": character_identity["name"],
            }
        else:
            system_instruction = _document_section_tool_instruction(role, section_key)
            validation_contract = {
                "protocol": SCREENPLAY_CANDIDATE_VALIDATION_PROTOCOL,
                "kind": contract.validation_kind,
                "sectionKey": section_key,
            }
        part_identity = None
        if source_chapter_digest:
            part_identity = {
                "chapterId": str(unit_input.get("chapterId") or ""),
                "chapterTitle": str(unit_input.get("chapterTitle") or ""),
                "chapterIndex": int(unit_input.get("chapterIndex") or 0),
            }
        elif source_digest_reduction:
            part_identity = {
                "digestId": str(unit_input.get("digestId") or ""),
                "reductionLevel": int(unit_input.get("reductionLevel") or 0),
                "reductionIndex": int(unit_input.get("reductionIndex") or 0),
            }
        elif structure_episode_number:
            part_identity = {
                "episodeNumber": structure_episode_number,
                "episodeId": episode_identity["id"],
                "episodeTitle": episode_identity["title"],
            }
        elif phase_identity["key"]:
            part_identity = {
                "phaseKey": phase_identity["key"],
                "phaseTitle": phase_identity["title"],
                "phaseObjective": phase_identity["objective"],
            }
        elif character_identity["key"]:
            part_identity = {
                "characterKey": character_identity["key"],
                "characterName": character_identity["name"],
            }
        result = await self._tool_calls.run_candidate(
            runtime=runtime,
            session_id=int(task["sessionId"]),
            system_instruction=system_instruction,
            user_payload={
                "task": "create_screenplay_document_section",
                "targetRole": role,
                "sectionKey": section_key,
                "episodeNumber": (
                    scene_list_episode_number
                    or structure_episode_number
                    or None
                ),
                "partIdentity": part_identity,
                "instruction": unit_input.get("instruction"),
                "constraints": unit_input.get("constraints") or [],
                "preserve": unit_input.get("preserve") or [],
                **(
                    {"evidenceDescriptor": descriptor}
                    if descriptor is not None
                    else {}
                ),
                "dependencyPartKeys": list(dependency_part_keys),
            },
            domain_context=await self._domain_context(
                task,
                unit,
                expected_part_type="document_section",
                expected_part_key=section_key,
                runtime=runtime,
                tool_profile=contract.tool_profile,
                deliverable_revision_scope=(
                    _creative_brief_revision_scope(descriptor)
                    if role == "creativeBrief"
                    else (
                        _late_stage_revision_scope(
                            descriptor,
                            allowed_roles={"structure"},
                            required_roles={"structure"},
                        )
                        if role == "sceneList"
                        else None
                    )
                ),
            ),
            conversation_turn_id=str(task["turnId"]),
            bind_run=bind_run,
            output_token_cap=contract.output_token_cap,
            reasoning_mode=contract.reasoning_mode,
            candidate_validation_contract=validation_contract,
            signal=signal,
        )
        return {
            **dict(result.candidate["payload"]),
            "contentText": str(result.candidate.get("contentText") or ""),
            "sectionKey": section_key,
            "runId": result.run_id,
            "sourceRunIds": list(_part_source_run_ids(
                task,
                dependency_part_keys,
                result.run_id,
            )),
            **_candidate_artifact_identity(result.candidate),
        }

    @staticmethod
    def _expand_structure_part(task, unit) -> Mapping[str, Any]:
        unit_input = dict(unit.get("input") or {})
        strategy = str(unit_input.get("splitStrategy") or "")
        config = {
            "structure_series_arc": ("phases", _MAX_STRUCTURE_PHASES),
            "structure_episode_plan": ("episodes", _MAX_STRUCTURE_EPISODES),
            "structure_character_arcs": ("characters", _MAX_STRUCTURE_CHARACTERS),
        }.get(strategy)
        if config is None:
            raise ValueError("unsupported structure split strategy")
        field, maximum = config
        index = _dependency_output(task, unit, "generate_document_section")
        content = index.get("contentJson")
        entries = content.get(field) if isinstance(content, Mapping) else None
        if not isinstance(entries, list) or not 1 <= len(entries) <= maximum:
            raise ValueError("structure split requires a persisted bounded index")
        normalized_entries = tuple(
            dict(value) for value in entries if isinstance(value, Mapping)
        )
        if len(normalized_entries) != len(entries):
            raise ValueError("structure split index entries are invalid")
        max_generated_units = int(task.get("maxGeneratedUnits") or 0)
        projected_units = len(tuple(task.get("units") or ())) + len(entries)
        if max_generated_units <= 0 or projected_units > max_generated_units:
            raise ValueError("screenplay_task_scope_too_large")
        dependency_ids = (
            tuple(
                str(candidate.get("id") or "")
                for candidate in task.get("units") or ()
                if isinstance(candidate, Mapping)
                and candidate.get("status") == "completed"
                and str((candidate.get("input") or {}).get(
                    "documentSectionKey"
                ) or "") == "episode_plan"
                and int((candidate.get("input") or {}).get(
                    "episodeNumber"
                ) or 0) > 0
            )
            if strategy == "structure_character_arcs"
            else ()
        )
        raise StructurePartSplit(
            strategy,
            normalized_entries,
            index_run_id=str(index.get("runId") or ""),
            dependency_ids=dependency_ids,
        )

    @staticmethod
    def _project_structure_hooks(task, unit) -> dict[str, Any]:
        dependency_ids = {
            str(value) for value in unit.get("dependsOn") or ()
        }
        episode_outputs = []
        dependency_outputs = []
        for candidate in task.get("units") or ():
            if (
                not isinstance(candidate, Mapping)
                or str(candidate.get("id") or "") not in dependency_ids
                or candidate.get("status") != "completed"
            ):
                continue
            output = candidate.get("output")
            if not isinstance(output, Mapping):
                raise ValueError("structure hook dependency has no durable output")
            dependency_outputs.append(output)
            candidate_input = candidate.get("input") or {}
            if (
                isinstance(candidate_input, Mapping)
                and str(candidate_input.get("documentSectionKey") or "")
                == "episode_plan"
            ):
                episode_outputs.append(output)
        episodes = [
            dict(episode)
            for output in episode_outputs
            for episode in (output.get("contentJson") or {}).get("episodes", ())
            if isinstance(episode, Mapping)
        ]
        episodes.sort(key=lambda item: int(item.get("number") or 0))
        if not episodes:
            raise ValueError("structure hooks require completed episode Parts")
        hooks = []
        for episode in episodes:
            episode_id = str(episode.get("id") or "").strip()
            hook = str(episode.get("hook") or "").strip()
            if not episode_id or not hook:
                raise ValueError("structure episode hook is incomplete")
            hooks.append({"episodeId": episode_id, "hook": hook})
        return {
            "sectionKey": "hooks",
            "title": "剧情钩子",
            "contentText": "\n".join(
                f"- {item['episodeId']}：{item['hook']}" for item in hooks
            ),
            "contentJson": {"hooks": hooks},
            "sourceRunIds": list(_source_run_ids(dependency_outputs)),
        }

    def _validate_manifest_part(self, task, unit) -> dict[str, Any]:
        validation_kind = str((unit.get("input") or {}).get("validationKind") or "")
        if validation_kind == "draft_episode":
            return _validate_draft_episode_parts(task, unit)
        if validation_kind == "review_episode":
            return _validate_review_episode_parts(task, unit)
        if validation_kind == "document":
            return _validate_document_parts(task, unit)
        raise RuntimeError("unsupported screenplay Manifest validation kind")

    async def _compose_final_response(
        self,
        task: Mapping[str, Any],
        unit: Mapping[str, Any],
        runtime,
        signal,
        bind_run,
    ) -> dict[str, Any]:
        validated = [
            dict(item.get("output") or {})
            for item in task.get("units") or ()
            if str(item.get("kind") or "") == "validate_manifest_part"
            and item.get("status") == "completed"
        ]
        if not validated:
            raise RuntimeError("screenplay final response has no validated candidates")
        unit_input = dict(unit.get("input") or {})
        role = str(task["targetRole"])
        payload = {
            "request": str(
                unit_input.get("userRequest")
                or unit_input.get("instruction")
                or ""
            ),
            "instruction": str(unit_input.get("instruction") or ""),
            "target": {
                "role": role,
                "label": _ROLE_LABELS.get(role, "剧本交付物"),
            },
            "constraints": list(unit_input.get("constraints") or ()),
            "preserve": list(unit_input.get("preserve") or ()),
            "candidates": [
                _public_candidate_fact(role, output)
                for output in validated
            ],
        }
        assert self._models is not None
        contract = self._part_contract(task, unit)
        result = await self._models.run_public_text(
            runtime=runtime,
            session_id=int(task["sessionId"]),
            system_instruction=_final_response_instruction(),
            user_payload=payload,
            binding_namespace="screenplay.agent.final_response",
            binding_aggregate_id=str(task["projectId"]),
            binding_command_id=f'{task["id"]}:{unit["id"]}',
            task_id=str(task["id"]),
            unit_id=str(unit["id"]),
            expected_part_key=str(task["targetRole"]),
            conversation_turn_id=str(task["turnId"]),
            output_token_cap=contract.output_token_cap,
            reasoning_mode=contract.reasoning_mode,
            bind_run=bind_run,
            signal=signal,
        )
        return {
            "finalResponse": result.text,
            "runId": result.run_id,
        }

    async def _domain_context(
        self,
        task: Mapping[str, Any],
        unit: Mapping[str, Any],
        *,
        expected_part_type: str,
        expected_part_key: str,
        runtime,
        tool_profile: str,
        unit_id: str | None = None,
        deliverable_revision_scope: Mapping[str, str] | None = None,
    ) -> ScreenplayAgentDomainContext:
        project = await self._db.fetch_one(
            "SELECT source_book_id, source_scope_json "
            "FROM screenplay_projects WHERE id = ?",
            [str(task["projectId"])],
        )
        if project is None:
            raise AppError("剧本项目不存在", 404)
        source_scope = parse_source_scope(project.get("source_scope_json"))
        if tool_profile == "source_chapter_digest":
            chapter_id = str(
                (unit.get("input") or {}).get("chapterId") or ""
            ).strip()
            if not chapter_id:
                raise ValueError("source chapter digest has no bound chapter")
            source_scope = parse_source_scope({
                "mode": "selected_chapters",
                "chapterIds": [chapter_id],
            })
        return ScreenplayAgentDomainContext(
            project_id=str(task["projectId"]),
            task_id=str(task["id"]),
            unit_id=str(unit_id or unit["id"]),
            target_role=str(task["targetRole"]),
            expected_part_type=expected_part_type,
            expected_part_key=expected_part_key,
            dependency_part_keys=_dependency_part_keys(task, unit),
            deliverable_revision_scope=deliverable_revision_scope,
            episode_number=(
                int((unit.get("input") or {}).get("episodeNumber") or 0)
                or None
            ),
            source_book_id=str(project.get("source_book_id") or "") or None,
            source_scope=source_scope,
            locale=str(getattr(runtime, "locale", "zh-CN") or "zh-CN"),
            tool_access=tool_profile,
        )

    @staticmethod
    def _part_contract(
        task: Mapping[str, Any],
        unit: Mapping[str, Any],
    ) -> ScreenplayPartContract:
        return resolve_screenplay_part_contract(
            str(task.get("targetRole") or ""),
            str(unit.get("kind") or ""),
            dict(unit.get("input") or {}),
        )


class ScreenplayTaskUnitExecutor:
    """Adapt screenplay generation to PurrA's durable unit contract."""

    def __init__(
        self,
        db,
        *,
        runtime,
        composition=None,
        tool_calling_service: ScreenplayToolCallingService | None = None,
    ) -> None:
        self._db = db
        self._runtime = runtime
        structured_calls = (
            ScreenplayStructuredCallService(db, composition=composition)
            if composition is not None
            else None
        )
        self.checkpoint_planner = (
            ScreenplayCheckpointPlanner(
                structured_calls,
                runtime=runtime,
            )
            if structured_calls is not None
            else None
        )
        self._delegate = ScreenplayTaskModelCalls(
            db,
            composition=composition,
            structured_call_service=structured_calls,
            tool_calling_service=tool_calling_service,
        )
        self._parts = ScreenplayPartArtifactQuery(db)
        self._long_tasks = SqliteLongTaskRepository(db)

    async def execute(
        self,
        context: DurableUnitExecutionContext,
        signal=None,
    ) -> LongTaskUnitResult:
        task = await self._task_view(context)
        unit = next(
            item for item in task["units"]
            if item["id"] == context.unit.id
        )
        if _requires_run(str(unit.get("kind") or "")):
            resolve_screenplay_part_contract(
                str(task.get("targetRole") or ""),
                str(unit.get("kind") or ""),
                dict(unit.get("input") or {}),
                persisted_key=str(unit.get("partContractKey") or ""),
            )
        bound_run_ids: list[str] = []

        async def bind_part_run(run_id: str) -> None:
            await context.bind_run(run_id)
            normalized = str(run_id or "").strip()
            if normalized and normalized not in bound_run_ids:
                bound_run_ids.append(normalized)

        try:
            output = dict(await self._delegate.execute(
                task=task,
                unit=unit,
                runtime=self._runtime,
                signal=signal,
                bind_run=bind_part_run,
            ))
        finally:
            settlement_error: Exception | None = None
            for run_id in bound_run_ids:
                try:
                    await _record_part_run_usage(
                        self._db,
                        self._long_tasks,
                        context.task.id,
                        run_id,
                    )
                except Exception as error:
                    settlement_error = settlement_error or error
            if settlement_error is not None:
                raise settlement_error
        semantic_key = str(context.unit.semantic_key or context.unit.id)
        ref = await self._parts.write_host_part(
            project_id=str(task["projectId"]),
            task_id=context.task.id,
            unit_id=context.unit.id,
            semantic_key=semantic_key,
            part_kind=str(unit["kind"]),
            output=output,
        )
        return _unit_result(ref, output)

    def classify_failure(self, error: Exception):
        if isinstance(error, StructurePartSplit):
            return FailureSignal(
                category=FailureCategory.MODEL_OUTPUT_INVALID,
                code=error.code,
                retryable=False,
                part_splittable=True,
            )
        return classify_screenplay_run_failure(error)

    def split_unit(
        self,
        context: DurableUnitExecutionContext,
        error: Exception,
    ) -> LongTaskSplitResult:
        if not isinstance(error, StructurePartSplit):
            return LongTaskSplitResult((), ())
        parent_metadata = thaw_json_mapping(context.unit.metadata)
        unit_input = dict(parent_metadata.get("input") or {})
        strategy = str(unit_input.get("splitStrategy") or "")
        if strategy != error.strategy:
            return LongTaskSplitResult((), ())
        recipe = thaw_json_mapping(context.task.metadata.get("recipe") or {})
        recipe_steps = tuple(
            item for item in recipe.get("steps") or ()
            if isinstance(item, Mapping)
        )
        known_recipe_ids = {
            str(item.get("id") or "")
            for item in recipe_steps
            if str(item.get("id") or "")
        }
        base_position = len(recipe_steps) + {
            "structure_series_arc": 0,
            "structure_episode_plan": _MAX_STRUCTURE_PHASES,
            "structure_character_arcs": (
                _MAX_STRUCTURE_PHASES + _MAX_STRUCTURE_EPISODES
            ),
        }[strategy]
        index_id = {
            "structure_series_arc": "section:structure:series_arc:index",
            "structure_episode_plan": "section:structure:episode_plan:index",
            "structure_character_arcs": "section:structure:character_arcs:index",
        }[strategy]
        inherited_dependencies = tuple(dict.fromkeys((
            *(("document:evidence",) if "document:evidence" in known_recipe_ids else ()),
            *((index_id,) if index_id in known_recipe_ids else ()),
            *error.dependency_ids,
        )))
        children = []
        for offset, entry in enumerate(error.entries):
            identity = _structure_split_child_identity(strategy, entry)
            child_id = str(identity["childId"])
            child_input = {
                key: value
                for key, value in unit_input.items()
                if key != "splitStrategy"
            }
            child_input.update(dict(identity["input"]))
            if strategy == "structure_series_arc":
                child_input["phasePosition"] = offset + 1
            elif strategy == "structure_character_arcs":
                child_input["characterPosition"] = offset + 1
            children.append(LongTaskUnitSpec(
                id=child_id,
                semantic_key=child_id,
                position=base_position + offset,
                dependencies=inherited_dependencies,
                parent_unit_id=context.unit.id,
                max_attempts=4,
                metadata={
                    "input": child_input,
                    "displayTitle": str(identity["displayTitle"]),
                    "partKind": "document_section",
                    "semanticKey": child_id,
                    "effectClass": "idempotent_write",
                    "completionEvidence": "artifact_ref",
                    "checkpointPolicy": "reuse_completed",
                    "retryPolicy": "bounded_attempts",
                    "partContractKey": str(identity["partContractKey"]),
                    "unitKind": "generate_document_section",
                    "executor": "screenplay",
                    "plannerStepId": str(
                        parent_metadata.get("plannerStepId") or context.unit.id
                    ),
                },
            ))
        child_ids = tuple(child.id for child in children)
        return LongTaskSplitResult(tuple(children), child_ids)

    async def _task_view(
        self,
        context: DurableUnitExecutionContext,
    ) -> dict[str, Any]:
        metadata = thaw_json_mapping(context.task.metadata)
        outputs = await self._parts.list_task_outputs(context.task.id)
        records = await self._long_tasks.list_units(context.task.id)
        units = []
        for record in records:
            unit_metadata = thaw_json_mapping(record.metadata)
            unit_id = str(record.id)
            units.append({
                "id": unit_id,
                "kind": str(unit_metadata.get("unitKind") or ""),
                "input": dict(unit_metadata.get("input") or {}),
                "dependsOn": list(record.dependencies),
                "status": record.status.value,
                "required": bool(record.required),
                "parentUnitId": record.parent_unit_id,
                "partContractKey": str(
                    unit_metadata.get("partContractKey") or ""
                ),
                "output": outputs.get(unit_id, {}),
            })
        return {
            "id": context.task.id,
            "projectId": str(metadata["projectId"]),
            "sessionId": int(metadata["sessionId"]),
            "turnId": str(metadata["turnId"]),
            "rootRunId": context.run_id,
            "targetRole": str(metadata["targetRole"]),
            "sourceRevisionRefs": list(metadata.get("sourceRevisionRefs") or ()),
            "maxGeneratedUnits": int(metadata.get("maxGeneratedUnits") or 0),
            "units": units,
        }


def _unit_result(ref, output: Mapping[str, Any]) -> LongTaskUnitResult:
    revision_id = str(output.get("revisionId") or "").strip()
    final_response = str(output.get("finalResponse") or "").strip()
    return LongTaskUnitResult(
        output_ref=ref.output_ref,
        run_id=ref.run_id,
        artifact_digest=ref.content_digest,
        validation_receipt=ref.validation_receipt,
        metadata={
            **({"revisionId": revision_id} if revision_id else {}),
            **({"finalResponse": final_response} if final_response else {}),
        },
    )


async def _record_part_run_usage(
    db,
    long_tasks: SqliteLongTaskRepository,
    task_id: str,
    run_id: str,
):
    row = await db.fetch_one(
        "SELECT status, model_attempt_count, unreported_usage_attempts, "
        "input_tokens, output_tokens, reasoning_tokens "
        "FROM ai_agent_runs WHERE id = ?",
        [str(run_id or "").strip()],
    )
    if row is None:
        raise RuntimeError("screenplay_part_run_usage_missing")
    if str(row.get("status") or "") not in {"done", "failed", "canceled"}:
        raise RuntimeError("screenplay_part_run_usage_not_terminal")
    usage = LongTaskUsage(
        invocation_count=int(row.get("model_attempt_count") or 0),
        unreported_usage_attempts=int(
            row.get("unreported_usage_attempts") or 0
        ),
        input_tokens=int(row.get("input_tokens") or 0),
        output_tokens=int(row.get("output_tokens") or 0),
        reasoning_tokens=int(row.get("reasoning_tokens") or 0),
    )
    for _attempt in range(16):
        task = await long_tasks.load(task_id)
        if task is None:
            raise RuntimeError("screenplay_long_task_missing")
        try:
            updated = await long_tasks.record_usage(
                task.id,
                run_id=run_id,
                usage=usage,
                expected_revision=task.revision,
            )
            break
        except ValueError as error:
            if str(error) != "long task revision conflict":
                raise
    else:
        raise RuntimeError("screenplay_long_task_usage_revision_conflict")
    if updated.status.terminal:
        units = await long_tasks.list_units(updated.id)
        if any(
            unit.error_code == "runtime_budget_exceeded"
            for unit in units
        ):
            raise RuntimeError("runtime_budget_exceeded")
        raise RuntimeError("screenplay_long_task_no_longer_active")
    return updated


def _requires_run(kind: str) -> bool:
    return str(kind or "").strip() in SCREENPLAY_AI_PART_KINDS


def _structure_split_child_identity(
    strategy: str,
    entry: Mapping[str, Any],
) -> dict[str, Any]:
    if strategy == "structure_series_arc":
        key = str(entry.get("key") or "").strip()
        title = str(entry.get("title") or "").strip()
        objective = str(entry.get("objective") or "").strip()
        if (
            _SAFE_STRUCTURE_KEY.fullmatch(key) is None
            or not title
            or not objective
        ):
            raise ValueError("structure series arc split identity is invalid")
        child_id = f"section:structure:series_arc:phase:{key}"
        return {
            "childId": child_id,
            "input": {
                "sectionKey": f"series_arc:phase:{key}",
                "documentSectionKey": "series_arc",
                "phaseKey": key,
                "phaseTitle": title,
                "phaseObjective": objective,
            },
            "displayTitle": f"设计{title}",
            "partContractKey": "structure.series_arc_phase",
        }
    if strategy == "structure_episode_plan":
        number = int(entry.get("number") or 0)
        episode_id = str(entry.get("id") or "").strip()
        title = str(entry.get("title") or "").strip()
        if number <= 0 or not episode_id or not title:
            raise ValueError("structure episode split identity is invalid")
        child_id = f"section:structure:episode_plan:episode-{number}"
        return {
            "childId": child_id,
            "input": {
                "sectionKey": f"episode_plan:episode-{number}",
                "documentSectionKey": "episode_plan",
                "episodeNumber": number,
                "episodeId": episode_id,
                "episodeTitle": title,
            },
            "displayTitle": f"规划第 {number} 集结构",
            "partContractKey": "structure.episode_plan_fragment",
        }
    if strategy == "structure_character_arcs":
        key = str(entry.get("key") or "").strip()
        name = str(entry.get("name") or "").strip()
        if _SAFE_STRUCTURE_KEY.fullmatch(key) is None or not name:
            raise ValueError("structure character split identity is invalid")
        child_id = f"section:structure:character_arcs:character:{key}"
        return {
            "childId": child_id,
            "input": {
                "sectionKey": f"character_arcs:character:{key}",
                "documentSectionKey": "character_arcs",
                "characterKey": key,
                "characterName": name,
            },
            "displayTitle": f"设计{name}人物弧",
            "partContractKey": "structure.character_arc_fragment",
        }
    raise ValueError("unsupported structure split strategy")


def _dependency_output(
    task: Mapping[str, Any],
    unit: Mapping[str, Any],
    expected_kind: str,
) -> Mapping[str, Any]:
    """Resolve a completed checkpoint for the same recipe target.

    Generate directly depends on evidence. Validation directly depends on the
    generated candidate, so its evidence checkpoint is an ancestor rather
    than a direct dependency. Match the stable target identity instead of
    relying on list position.
    """

    units = tuple(
        item
        for item in task.get("units") or ()
        if isinstance(item, Mapping)
    )
    dependencies = {
        str(value)
        for value in unit.get("dependsOn") or ()
        if str(value).strip()
    }
    direct = tuple(
        item
        for item in units
        if str(item.get("id") or "") in dependencies
        and str(item.get("kind") or "") == expected_kind
        and item.get("status") == "completed"
    )
    candidates = direct or tuple(
        item
        for item in units
        if str(item.get("kind") or "") == expected_kind
        and item.get("status") == "completed"
        and _same_recipe_target(item, unit)
    )
    if len(candidates) != 1:
        raise RuntimeError(
            f"screenplay checkpoint {expected_kind!r} is missing or ambiguous"
        )
    output = candidates[0].get("output")
    if not isinstance(output, Mapping):
        raise RuntimeError(
            f"screenplay checkpoint {expected_kind!r} has no durable output"
        )
    return output


def _same_recipe_target(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
) -> bool:
    left_input = left.get("input")
    right_input = right.get("input")
    if not isinstance(left_input, Mapping) or not isinstance(right_input, Mapping):
        return False
    left_episode = int(left_input.get("episodeNumber") or 0)
    right_episode = int(right_input.get("episodeNumber") or 0)
    if left_episode or right_episode:
        return left_episode > 0 and left_episode == right_episode
    return str(left_input.get("targetRole") or "") == str(
        right_input.get("targetRole") or ""
    )


def _dependency_part_keys(
    task: Mapping[str, Any],
    unit: Mapping[str, Any],
) -> tuple[str, ...]:
    dependencies = tuple(
        str(value)
        for value in unit.get("dependsOn") or ()
        if str(value).strip()
    )
    units = {
        str(candidate.get("id") or ""): candidate
        for candidate in task.get("units") or ()
        if isinstance(candidate, Mapping)
    }
    return tuple(
        dependency
        for dependency in dependencies
        if (
            (candidate := units.get(dependency)) is not None
            and candidate.get("status") == "completed"
            and isinstance(candidate.get("output"), Mapping)
            and str(candidate.get("kind") or "") not in {
                "collect_evidence",
                "expand_structure_series_arc",
                "expand_structure_episode_plan",
                "expand_structure_character_arcs",
            }
        )
    )


def _creative_brief_revision_scope(
    descriptor: Mapping[str, Any] | None,
) -> dict[str, str]:
    if not isinstance(descriptor, Mapping):
        raise ValueError("creative brief requires an evidence descriptor")
    raw = descriptor.get("acceptedRevisionIds")
    if not isinstance(raw, Mapping):
        raise ValueError("creative brief evidence Revision scope is invalid")
    unexpected = set(raw).difference({"sourceAnalysis", "creativeBrief"})
    if unexpected:
        raise ValueError("creative brief evidence scope is too broad")
    result = {
        str(role): str(revision_id).strip()
        for role, revision_id in raw.items()
        if str(revision_id).strip()
    }
    if len(result) != len(raw):
        raise ValueError("creative brief evidence Revision scope is invalid")
    base_revision_id = str(descriptor.get("baseRevisionId") or "").strip()
    if base_revision_id and result.get("creativeBrief") != base_revision_id:
        raise ValueError("creative brief baseline Revision is not locked")
    return result


def _late_stage_revision_scope(
    descriptor: Mapping[str, Any] | None,
    *,
    allowed_roles: set[str],
    required_roles: set[str],
    base_revision_id: str | None = None,
) -> dict[str, str]:
    if not isinstance(descriptor, Mapping):
        raise ValueError("screenplay Part requires an evidence descriptor")
    raw = descriptor.get("acceptedRevisionIds")
    if not isinstance(raw, Mapping) or set(raw).difference(allowed_roles):
        raise ValueError("screenplay Part evidence Revision scope is invalid")
    result = {
        str(role): str(revision_id).strip()
        for role, revision_id in raw.items()
        if str(revision_id).strip()
    }
    if len(result) != len(raw) or not required_roles.issubset(result):
        raise ValueError("screenplay Part evidence Revision scope is incomplete")
    if base_revision_id:
        baseline = str(base_revision_id).strip()
        if result.get("screenplayDraft") != baseline:
            raise ValueError("screenplay Draft baseline Revision is not locked")
    return result


def _dependency_outputs(
    task: Mapping[str, Any],
    dependency_part_keys: Sequence[str],
) -> tuple[Mapping[str, Any], ...]:
    units = {
        str(candidate.get("id") or ""): candidate
        for candidate in task.get("units") or ()
        if isinstance(candidate, Mapping)
    }
    outputs = []
    for key in dependency_part_keys:
        output = (units.get(str(key)) or {}).get("output")
        if not isinstance(output, Mapping):
            raise RuntimeError("completed screenplay dependency has no output")
        outputs.append(output)
    return tuple(outputs)


def _part_source_run_ids(
    task: Mapping[str, Any],
    dependency_part_keys: Sequence[str],
    current_run_id: str,
) -> tuple[str, ...]:
    result: list[str] = []
    for output in _dependency_outputs(task, dependency_part_keys):
        values = output.get("sourceRunIds")
        for value in (
            *(values if isinstance(values, (list, tuple)) else ()),
            output.get("runId"),
        ):
            run_id = str(value or "").strip()
            if run_id and run_id not in result:
                result.append(run_id)
    normalized_current = str(current_run_id or "").strip()
    if normalized_current and normalized_current not in result:
        result.append(normalized_current)
    return tuple(result)


def _completed_part_outputs(
    task: Mapping[str, Any],
    *,
    kind: str,
    episode_number: int | None = None,
) -> list[dict[str, Any]]:
    outputs: list[dict[str, Any]] = []
    for unit in task.get("units") or ():
        if not isinstance(unit, Mapping):
            continue
        unit_input = unit.get("input")
        if (
            str(unit.get("kind") or "") != kind
            or unit.get("status") != "completed"
            or not isinstance(unit_input, Mapping)
            or (
                episode_number is not None
                and int(unit_input.get("episodeNumber") or 0) != episode_number
            )
        ):
            continue
        output = unit.get("output")
        if not isinstance(output, Mapping):
            raise RuntimeError(f"completed screenplay Part {unit.get('id')} has no output")
        enriched = dict(output)
        for key in (
            "episodeNumber",
            "sceneId",
            "reviewDimension",
            "sectionKey",
        ):
            if key not in enriched and key in unit_input:
                enriched[key] = unit_input[key]
        outputs.append(enriched)
    return outputs


def _scene_tool_instruction(episode_number: int, scene_id: str) -> str:
    return f"""你是专业剧本编剧，只创作第 {episode_number} 集中的场景 {scene_id}。
必须调用可用的读取工具，按 evidenceDescriptor 中的精确 revisionId 和 episodeNumber 获取场景计划、旧稿、审阅信息或原作依据。不得把 evidenceDescriptor 当成正文，也不得声称未调用工具就已读到材料。
如果 dependencyPartKeys 非空，必须调用 readScreenplayTaskDependencies 读取这些当前任务依赖；不得根据 Part key 猜测其内容。
取得工具结果后完成创作，不得扩大到其他场景或重新规划任务。
最终回复只输出当前场景的完整可拍摄剧本文本。不得输出 JSON、Markdown 代码块、过程说明、整集或其他场景。场景身份和写入由宿主负责。"""


def _host_scene_candidate_template(
    scene_id: str,
) -> dict[str, Any]:
    return {"sceneId": scene_id}


def _candidate_artifact_identity(
    candidate: Mapping[str, Any],
) -> dict[str, str]:
    artifact_id = str(candidate.get("artifactId") or "").strip()
    return {"artifactId": artifact_id} if artifact_id else {}


def _episode_metadata_tool_instruction(episode_number: int) -> str:
    return f"""你只负责整理第 {episode_number} 集的短元数据，不生成或复述剧本正文。
必须调用 readScreenplayTaskDependencies，读取 dependencyPartKeys 指向的已完成场景 Part，再形成以下对象：
{{"episodeNumber":{episode_number},"title":"简洁集标题","continuitySummary":"供下一集续写的连续性摘要"}}
最终必须调用 writeScreenplayCandidatePart 写入该对象；不得只在回复中打印 JSON，不得附加其他字段。"""


def _review_dimension_tool_instruction(
    episode_number: int,
    dimension: str,
    scene_ids: Sequence[str],
) -> str:
    issue_limit = len(tuple(dict.fromkeys(scene_ids)))
    return f"""你是剧本审阅 Agent，只审阅第 {episode_number} 集的 {dimension} 维度。
必须调用 getScreenplayEpisodeContext，使用 evidenceDescriptor 中的 episodeNumber、reviewedDraftId 和 sceneListRevisionId 读取指定不可变版本。只能依据工具返回的材料审阅，不得把系统错误写成审阅意见。
构造以下候选对象：
{{"episodeNumber":{episode_number},"reviewDimension":"{dimension}","title":"第 {episode_number} 集 {dimension} 审阅","contentText":"当前维度的 Markdown 审阅意见","contentJson":{{"verdict":"ready|revise|major_rework","issues":[{{"id":"维度内唯一 ID","severity":"critical|major|minor","description":"具体问题与修改方向","sceneIds":["场景ID"]}}]}}}}
问题只能引用这些场景 ID：{list(scene_ids)}。本维度最多提交 {issue_limit} 个问题，不得超过当前集场景数；没有问题时 issues=[] 且 verdict=ready。最终必须调用 writeScreenplayCandidatePart 写入候选对象，不得只在回复中打印 JSON。"""


def _validate_review_dimension_candidate(
    candidate: Mapping[str, Any],
    *,
    episode_number: int,
    dimension: str,
    allowed_scene_ids: Sequence[str],
    reviewed_draft_id: str,
    reviewed_content_digest: str,
) -> dict[str, Any]:
    raw = candidate.get("payload")
    if not isinstance(raw, Mapping) or (
        set(raw) != {
            "episodeNumber",
            "reviewDimension",
            "title",
            "contentJson",
        }
        or isinstance(raw.get("episodeNumber"), bool)
        or not isinstance(raw.get("episodeNumber"), int)
        or int(raw.get("episodeNumber") or 0) != episode_number
        or str(raw.get("reviewDimension") or "") != dimension
    ):
        raise ValueError("review dimension candidate identity does not match")
    raw_content = raw.get("contentJson")
    if (
        not isinstance(raw.get("title"), str)
        or not str(raw["title"]).strip()
        or not isinstance(raw_content, Mapping)
        or set(raw_content) != {"verdict", "issues"}
        or not isinstance(raw_content.get("issues"), list)
        or any(
            not isinstance(issue, Mapping)
            or set(issue) != _REVIEW_ISSUE_FIELDS
            or not isinstance(issue.get("id"), str)
            or not isinstance(issue.get("severity"), str)
            or not isinstance(issue.get("description"), str)
            or not isinstance(issue.get("sceneIds"), list)
            or any(
                not isinstance(scene_id, str) or not scene_id.strip()
                for scene_id in issue.get("sceneIds") or ()
            )
            for issue in raw_content["issues"]
        )
    ):
        raise ValueError("review dimension candidate fields are invalid")
    normalized = _validate_deliverable_candidate(
        "review",
        candidate,
        structure_id=None,
        structure_episode_numbers=(),
        reviewed_draft_id=reviewed_draft_id,
    )
    payload = dict(normalized["payload"])
    content = dict(payload["contentJson"])
    allowed = set(allowed_scene_ids)
    if len(content["issues"]) > len(allowed):
        raise ValueError("review dimension issue count exceeds scene count")
    issues = []
    for issue in content["issues"]:
        scene_ids = tuple(str(value) for value in issue["sceneIds"])
        if not set(scene_ids).issubset(allowed):
            raise ValueError("review dimension references another episode")
        issue_prefix = f"episode-{episode_number}:{dimension}:"
        issue_id = str(issue["id"])
        issues.append({
            **dict(issue),
            "id": (
                issue_id
                if issue_id.startswith(issue_prefix)
                else f"{issue_prefix}{issue_id}"
            ),
            "sceneIds": list(scene_ids),
            "dimension": dimension,
        })
    content.update({
        "issues": issues,
        "issueCount": len(issues),
        "criticalIssueCount": sum(
            issue["severity"] == "critical" for issue in issues
        ),
        "reviewedEpisode": episode_number,
        "reviewDimension": dimension,
        "reviewedDraftId": reviewed_draft_id,
        "reviewedContentDigest": reviewed_content_digest,
        "inputContractVersion": 2,
    })
    payload["contentJson"] = content
    payload["episodeNumber"] = episode_number
    payload["reviewDimension"] = dimension
    return {**normalized, "payload": payload}


def _creative_brief_section_example(section_key: str) -> dict[str, Any]:
    examples = {
        "positioning": {"fields": {
            "approach": "人物驱动的创作方法",
            "format": "竖屏短剧",
            "audience": "目标观众",
            "tone": "整体调性",
        }},
        "premise": {"fields": {
            "premise": "一句完整的核心前提",
            "centralConflict": "核心冲突",
            "dramaticQuestion": "核心戏剧问题",
        }},
        "characters": {"coreCharacters": [{
            "key": "lead",
            "name": "人物姓名",
            "function": "叙事功能",
            "desire": "核心欲望",
            "obstacle": "主要阻力",
            "changeDirection": "变化方向",
        }]},
        "world": {
            "worldRules": ["对剧情有约束力的世界规则"],
            "visualIdentity": "可执行的视觉识别",
        },
        "adaptation_rules": {"adaptationRules": [{
            "rule": "改编规则",
            "reason": "采用该规则的理由",
        }]},
    }
    try:
        return examples[section_key]
    except KeyError as error:
        raise ValueError("screenplay_part_contract_unknown") from error


def _creative_brief_section_tool_instruction(section_key: str) -> str:
    content_json = json.dumps(
        _creative_brief_section_example(section_key),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    character_limit = (
        f"coreCharacters 必须包含 1 至 {_MAX_CREATIVE_BRIEF_CHARACTERS} 项；"
        if section_key == "characters"
        else ""
    )
    list_limit = (
        f"worldRules 必须包含 1 至 {_MAX_CREATIVE_BRIEF_LIST_ITEMS} 项；"
        if section_key == "world"
        else (
            f"adaptationRules 必须包含 1 至 {_MAX_CREATIVE_BRIEF_LIST_ITEMS} 项；"
            if section_key == "adaptation_rules"
            else ""
        )
    )
    return f"""你只生成 creativeBrief 文档中的 {section_key} 章节。
如果 evidenceDescriptor.acceptedRevisionIds 非空，必须调用 readScreenplayDeliverable，并严格使用其中绑定给 sourceAnalysis 或 creativeBrief 的 role 与 revisionId；不得读取其他交付物、旧版本或当前 Operation 的候选 Part。若该映射为空，只能依据当前用户指令和已有对话上下文，不得虚构已读取材料。
如果 dependencyPartKeys 非空，必须调用 readScreenplayTaskDependencies 读取这些直接依赖；不得根据 Part key 猜测内容。
构造以下候选对象：
{{"sectionKey":"{section_key}","title":"章节标题","contentText":"当前章节的紧凑 Markdown 正文","contentJson":{content_json}}}
contentJson 的顶层与嵌套字段必须和示例完全一致。{character_limit}{list_limit}不得输出其他章节、完整简报、分集、场景或对白。最终必须调用 writeScreenplayCandidatePart 写入候选对象；不得打印工具参数、Run/Task/Artifact ID 或私有推理。"""


def _document_section_tool_instruction(role: str, section_key: str) -> str:
    if role == "sourceAnalysis":
        return _source_analysis_section_tool_instruction(section_key)
    if role == "creativeBrief":
        return _creative_brief_section_tool_instruction(section_key)
    schemas = {
        ("structure", "series_arc"): '{"seriesArc":{"phases":[]}}',
        ("structure", "character_arcs"): '{"characterArcs":[]}',
        ("structure", "hooks"): '{"hooks":[]}',
    }
    content_json = schemas.get((role, section_key))
    if content_json is None:
        raise ValueError("screenplay_part_contract_unknown")
    return f"""你只生成 {role} 文档中的 {section_key} 章节。
必须调用可用的读取工具，按 evidenceDescriptor 中的精确 revisionId 获取完成本章节所需的项目交付物或原作依据。不得把描述符当作正文。
如果 dependencyPartKeys 非空，必须调用 readScreenplayTaskDependencies 读取这些当前任务依赖；不得根据 Part key 猜测内容。
构造以下候选对象：
{{"sectionKey":"{section_key}","title":"章节标题","contentText":"当前章节的 Markdown 正文","contentJson":{content_json}}}
contentJson 必须是可与同一文档其他章节确定性合并的顶层片段；不得输出其他章节或完整文档。最终必须调用 writeScreenplayCandidatePart 写入候选对象，不得只在回复中打印 JSON。"""


def _source_digest_schema(identity: str) -> str:
    return (
        '{"chapterId":' + json.dumps(identity, ensure_ascii=False)
        + ',"summary":"紧凑事件边界",'
        '"characters":[{"key":"来源人物键","state":"状态变化"}],'
        '"events":[{"key":"事件键","summary":"事件","consequence":"后果"}],'
        '"worldFacts":[{"key":"事实键","summary":"规则或设定"}],'
        '"themes":["主题信号"],'
        '"plotThreads":[{"key":"线索键","state":"opened|advanced|resolved"}],'
        '"adaptationRisks":["改编风险"]}'
    )


def _source_chapter_digest_tool_instruction(
    *,
    chapter_id: str,
    chapter_title: str,
    chapter_index: int,
) -> str:
    section_key = f"source_digest:chapter:{chapter_id}"
    return f"""你只分析宿主绑定的一个授权叶子章节：第 {chapter_index} 章《{chapter_title}》。
必须调用 readSourceChapters，chapterIds 只能且必须是 [{json.dumps(chapter_id, ensure_ascii=False)}]；不得读取其他章节、全书目录、人物库或既有分析文档。
构造以下候选对象：
{{"sectionKey":{json.dumps(section_key, ensure_ascii=False)},"title":"当前章节事实摘要","contentText":"只包含本章事件边界的紧凑摘要","contentJson":{_source_digest_schema(chapter_id)}}}
contentJson 只能包含示例中的八个字段；数组应紧凑、键唯一，不得复制长原文，不得生成分集、场景、对白或改编成稿。最终必须调用 writeScreenplayCandidatePart；不得输出工具参数、Run/Task/Artifact ID 或私有推理。"""


def _source_digest_reduction_tool_instruction(digest_id: str) -> str:
    section_key = digest_id.replace("source-analysis:", "source_digest:", 1)
    return f"""你只归并当前单元的直接原作摘要依赖，不读取原文，也不创建新的故事事实。
必须调用 readScreenplayTaskDependencies，一次读取 dependencyPartKeys 中全部 1 至 12 个直接依赖；不得根据 Part key 猜测内容。
构造以下候选对象：
{{"sectionKey":{json.dumps(section_key, ensure_ascii=False)},"title":"原作事实归并摘要","contentText":"直接依赖的紧凑无损归并摘要","contentJson":{_source_digest_schema(digest_id)}}}
contentJson 只能包含示例中的八个字段。合并同键事实并保留冲突或状态变化，不得新增人物、事件、设定、主题或改编结论，不得输出逐章复述。最终必须调用 writeScreenplayCandidatePart；不得输出 Schema 之外的内部标识或私有推理。"""


def _source_analysis_section_tool_instruction(section_key: str) -> str:
    configurations = {
        "characters": (
            '{"characters":[]}',
            "只分析原作人物及其状态、关系和叙事功能；不得重设计剧本人物或复述完整故事。",
        ),
        "story": (
            '{"story":{"beats":[],"openThreads":[]}}',
            "只梳理有证据的故事节拍与未闭合线索；不得逐字复述原文或设计分集。",
        ),
        "world": (
            '{"world":{"rules":[],"locations":[],"factions":[]}}',
            "只整理有证据的规则、地点和阵营；不得补写无证据的新设定。",
        ),
        "themes": (
            '{"themes":[]}',
            "只提炼主题信号；不得输出人物档案、逐章摘要或完整故事。",
        ),
        "adaptation_risks": (
            '{"adaptationRisks":[]}',
            "只识别改编风险；不得直接写改编成稿、分集或场景。",
        ),
    }
    config = configurations.get(section_key)
    if config is None:
        raise ValueError("screenplay_part_contract_unknown")
    content_json, boundary = config
    return f"""你只生成原作分析文档中的 {section_key} 章节。
必须调用 readScreenplayTaskDependencies，一次读取 dependencyPartKeys 中全部直接摘要依赖；不得调用原作正文工具，也不得根据 Part key 猜测内容。
构造以下候选对象：
{{"sectionKey":"{section_key}","title":"章节标题","contentText":"当前章节的 Markdown 正文","contentJson":{content_json}}}
contentJson 顶层及嵌套字段必须与示例完全一致。{boundary}
最终必须调用 writeScreenplayCandidatePart；不得输出其他分析章节、工具参数、Run/Task/Artifact ID 或私有推理。"""


def _structure_episode_plan_index_tool_instruction() -> str:
    return """你只确定分集结构的索引，不写完整分集内容。
必须调用可用的读取工具，按 evidenceDescriptor 中的精确 revisionId 读取已接受创作简报、原作分析，并按需读取原作结构或正文。不得把原作章节数直接等同于剧集数；应依据项目格式、叙事容量、主线阶段和用户约束确定集数。
如果 dependencyPartKeys 非空，必须调用 readScreenplayTaskDependencies 读取这些当前任务依赖。
构造以下候选对象：
{"sectionKey":"episode_plan:index","title":"分集索引","contentText":"简短的分集索引 Markdown","contentJson":{"episodes":[{"number":1,"id":"ep01","title":"集标题","summary":"本集叙事边界与核心推进","sourceChapterIds":["实际读取过的 chapterId"]}]}}
集号必须从 1 连续递增，最多 100 集；id、title、summary 必须唯一且简洁。这里只提交用于后续逐集生成的轻量索引，不得展开场景或长篇正文。最终必须调用 writeScreenplayCandidatePart 写入候选对象。"""


def _structure_series_arc_index_tool_instruction() -> str:
    return """你只确定全剧主线的阶段索引，不写分集、场景或完整阶段正文。
必须调用可用的读取工具，按 evidenceDescriptor 中的精确 revisionId 获取已接受材料；如 dependencyPartKeys 非空，还必须调用 readScreenplayTaskDependencies 读取直接依赖。
构造以下候选对象：
{"sectionKey":"series_arc:index","title":"全剧阶段索引","contentText":"简短的阶段索引 Markdown","contentJson":{"phases":[{"key":"稳定的英文或数字键","title":"阶段标题","objective":"该阶段的叙事目标"}]}}
阶段必须按叙事顺序排列，数量 1 到 12；key、title 必须唯一。不得包含 episodes、scenes、sceneText 或对白。最终必须调用 writeScreenplayCandidatePart 写入候选对象。"""


def _structure_series_arc_phase_tool_instruction(
    entry: Mapping[str, Any],
) -> str:
    key = str(entry.get("key") or "")
    title = str(entry.get("title") or "")
    objective = str(entry.get("objective") or "")
    return f"""你只生成全剧主线中的阶段 {title}。
阶段索引已经固定身份：key={key}、title={title}、objective={objective}。不得改变这些值，也不得输出其他阶段。
必须调用 readScreenplayTaskDependencies 读取 dependencyPartKeys 中的阶段索引；需要已接受创作简报或原作分析时，按 evidenceDescriptor 中的精确 revisionId 调用交付物读取工具。
构造以下候选对象：
{{"sectionKey":"series_arc:phase:{key}","title":{json.dumps(title, ensure_ascii=False)},"contentText":"当前阶段的 Markdown 正文","contentJson":{{"seriesArc":{{"phases":[{{"key":{json.dumps(key, ensure_ascii=False)},"title":{json.dumps(title, ensure_ascii=False)},"objective":{json.dumps(objective, ensure_ascii=False)},"centralConflict":"阶段核心冲突","turningPoint":"阶段关键转折","exitState":"阶段结束状态"}}]}}}}}}
contentJson 只能包含当前阶段；不得包含 episodes、scenes、sceneText 或对白。最终必须调用 writeScreenplayCandidatePart 写入候选对象。"""


def _structure_episode_plan_fragment_tool_instruction(
    entry: Mapping[str, Any],
) -> str:
    number = int(entry.get("number") or 0)
    episode_id = str(entry.get("id") or "")
    title = str(entry.get("title") or "")
    episode_id_json = json.dumps(episode_id, ensure_ascii=False)
    title_json = json.dumps(title, ensure_ascii=False)
    return f"""你只生成分集结构中的第 {number} 集。
分集索引已经固定本集身份：number={number}、id={episode_id}、title={title}。不得改变集数、ID、标题，也不得输出其他集。
必须调用 readScreenplayTaskDependencies，读取 dependencyPartKeys 中的分集索引和其他直接依赖；需要原作依据时再按 evidenceDescriptor 调用对应读取工具。不得根据 Part key 猜测内容。
构造以下候选对象：
{{"sectionKey":"episode_plan:episode-{number}","title":"第 {number} 集分集结构","contentText":"当前集分集结构的 Markdown 正文","contentJson":{{"episodes":[{{"number":{number},"id":{episode_id_json},"title":{title_json},"summary":"本集完整梗概","objective":"本集目标","conflict":"核心冲突","turn":"关键转折","hook":"集末钩子"}}]}}}}
contentJson.episodes 必须且只能包含当前一集。最终必须调用 writeScreenplayCandidatePart 写入候选对象。"""


def _structure_character_arcs_index_tool_instruction() -> str:
    return """你只确定需要人物弧设计的核心人物索引，不写完整人物弧。
必须调用 readScreenplayTaskDependencies 分批读取 dependencyPartKeys 中已完成的分集结构；需要项目或原作身份依据时调用相应读取工具。
构造以下候选对象：
{"sectionKey":"character_arcs:index","title":"核心人物索引","contentText":"简短的人物索引 Markdown","contentJson":{"characters":[{"key":"稳定的英文或数字键","name":"人物姓名"}]}}
只选择 1 到 12 名确实需要跨集变化的核心人物；key、name 必须唯一。不得在索引中写人物弧正文、分集或场景。最终必须调用 writeScreenplayCandidatePart 写入候选对象。"""


def _structure_character_arc_tool_instruction(
    entry: Mapping[str, Any],
) -> str:
    key = str(entry.get("key") or "")
    name = str(entry.get("name") or "")
    return f"""你只生成 {name} 的人物弧。
人物索引已经固定身份：key={key}、name={name}。不得改变人物身份，也不得输出其他人物。
必须调用 readScreenplayTaskDependencies 分批读取 dependencyPartKeys 中的人物索引和分集结构，不得根据 Part key 猜测内容。
构造以下候选对象：
{{"sectionKey":"character_arcs:character:{key}","title":{json.dumps(name + '人物弧', ensure_ascii=False)},"contentText":"当前人物弧的 Markdown 正文","contentJson":{{"characterArcs":[{{"key":{json.dumps(key, ensure_ascii=False)},"startState":"起始状态","desire":"核心欲望","turningEpisodes":["ep01"],"endState":"结束状态"}}]}}}}
turningEpisodes 只能引用已读取分集的 id。contentJson 只能包含当前人物。最终必须调用 writeScreenplayCandidatePart 写入候选对象。"""


def _scene_list_fragment_tool_instruction(episode_number: int) -> str:
    return f"""你只规划已采纳结构中的第 {episode_number} 集场景。
必须调用 readScreenplayDeliverable，使用 role=structure、evidenceDescriptor 中的精确 structureRevisionId 和 episodeNumber={episode_number}，只读取当前集结构。
如果 dependencyPartKeys 非空，必须调用 readScreenplayTaskDependencies 读取这些当前任务依赖。
构造以下候选对象：
{{"sectionKey":"episode-{episode_number}","title":"第 {episode_number} 集场景表","contentText":"当前集场景表的 Markdown 文档","contentJson":{{"scenes":[{{"id":"全局唯一场景 ID","episodeNumber":{episode_number},"heading":"内外景·地点·时间","objective":"目标","conflict":"冲突","turn":"转折","synopsis":"场景梗概"}}]}}}}
只提交当前集，场景顺序必须可直接用于后续剧本创作。最终必须调用 writeScreenplayCandidatePart 写入候选对象，不得只在回复中打印 JSON。"""


def _validate_document_section_candidate(
    candidate: Mapping[str, Any],
    section_key: str,
) -> dict[str, Any]:
    value = dict(candidate.get("payload") or {})
    title = str(value.get("title") or "").strip()
    text = str(candidate.get("contentText") or "").strip()
    content = value.get("contentJson")
    if (
        not section_key
        or str(value.get("sectionKey") or "") != section_key
        or not title
        or not text
        or not isinstance(content, Mapping)
    ):
        raise ValueError("document section candidate is incomplete")
    return {
        **dict(candidate),
        "payload": {
            "sectionKey": section_key,
            "title": title,
            "contentJson": dict(content),
        },
        "contentText": text,
    }


def _creative_brief_required_strings(
    value: object,
    fields: Sequence[str],
    *,
    label: str,
) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != set(fields):
        raise ValueError(f"creative brief {label} fields are invalid")
    if any(
        not isinstance(value.get(field), str)
        or not value[field].strip()
        for field in fields
    ):
        raise ValueError(f"creative brief {label} is incomplete")
    return {field: value[field].strip() for field in fields}


def _creative_brief_characters(value: object) -> list[dict[str, str]]:
    if (
        not isinstance(value, list)
        or not 1 <= len(value) <= _MAX_CREATIVE_BRIEF_CHARACTERS
    ):
        raise ValueError("creative brief coreCharacters count is invalid")
    characters = [
        _creative_brief_required_strings(
            item,
            _CREATIVE_BRIEF_CHARACTER_FIELDS,
            label="core character",
        )
        for item in value
    ]
    keys = [item["key"] for item in characters]
    if (
        any(_SAFE_STRUCTURE_KEY.fullmatch(key) is None for key in keys)
        or len(keys) != len(set(keys))
    ):
        raise ValueError("creative brief core character keys are invalid")
    return characters


def _creative_brief_world_rules(value: object) -> list[str]:
    if (
        not isinstance(value, list)
        or not 1 <= len(value) <= _MAX_CREATIVE_BRIEF_LIST_ITEMS
        or any(not isinstance(item, str) or not item.strip() for item in value)
    ):
        raise ValueError("creative brief worldRules are invalid")
    rules = [item.strip() for item in value]
    if len(rules) != len(set(rules)):
        raise ValueError("creative brief worldRules must be unique")
    return rules


def _creative_brief_adaptation_rules(value: object) -> list[dict[str, str]]:
    if (
        not isinstance(value, list)
        or not 1 <= len(value) <= _MAX_CREATIVE_BRIEF_LIST_ITEMS
    ):
        raise ValueError("creative brief adaptationRules count is invalid")
    rules = [
        _creative_brief_required_strings(
            item,
            ("rule", "reason"),
            label="adaptation rule",
        )
        for item in value
    ]
    values = [item["rule"] for item in rules]
    if len(values) != len(set(values)):
        raise ValueError("creative brief adaptationRules must be unique")
    return rules


def _validate_creative_brief_section_candidate(
    candidate: Mapping[str, Any],
    section_key: str,
) -> dict[str, Any]:
    normalized = _validate_document_section_candidate(candidate, section_key)
    content = dict(normalized["payload"]["contentJson"])
    if section_key == "positioning":
        if set(content) != {"fields"}:
            raise ValueError("creative brief positioning fields are invalid")
        content["fields"] = _creative_brief_required_strings(
            content.get("fields"),
            _CREATIVE_BRIEF_POSITIONING_FIELDS,
            label="positioning",
        )
    elif section_key == "premise":
        if set(content) != {"fields"}:
            raise ValueError("creative brief premise fields are invalid")
        content["fields"] = _creative_brief_required_strings(
            content.get("fields"),
            _CREATIVE_BRIEF_PREMISE_FIELDS,
            label="premise",
        )
    elif section_key == "characters":
        if set(content) != {"coreCharacters"}:
            raise ValueError("creative brief character fields are invalid")
        content["coreCharacters"] = _creative_brief_characters(
            content.get("coreCharacters")
        )
    elif section_key == "world":
        if set(content) != {"worldRules", "visualIdentity"}:
            raise ValueError("creative brief world fields are invalid")
        content["worldRules"] = _creative_brief_world_rules(
            content.get("worldRules")
        )
        content["visualIdentity"] = _creative_brief_required_strings(
            {"visualIdentity": content.get("visualIdentity")},
            ("visualIdentity",),
            label="visual identity",
        )["visualIdentity"]
    elif section_key == "adaptation_rules":
        if set(content) != {"adaptationRules"}:
            raise ValueError("creative brief adaptation rule fields are invalid")
        content["adaptationRules"] = _creative_brief_adaptation_rules(
            content.get("adaptationRules")
        )
    else:
        raise ValueError("creative brief section is unsupported")
    normalized["payload"]["contentJson"] = content
    return normalized


def _validate_source_digest_candidate(
    candidate: Mapping[str, Any],
    *,
    section_key: str,
    digest_id: str,
) -> dict[str, Any]:
    normalized = _validate_document_section_candidate(candidate, section_key)
    content = dict(normalized["payload"]["contentJson"])
    required = {
        "chapterId",
        "summary",
        "characters",
        "events",
        "worldFacts",
        "themes",
        "plotThreads",
        "adaptationRisks",
    }
    if set(content) != required or str(content.get("chapterId") or "") != digest_id:
        raise ValueError("source chapter digest fields are invalid")
    summary = str(content.get("summary") or "").strip()
    if not summary:
        raise ValueError("source chapter digest summary is required")
    content["summary"] = summary
    content["characters"] = _validate_source_digest_objects(
        content.get("characters"),
        ("key", "state"),
    )
    content["events"] = _validate_source_digest_objects(
        content.get("events"),
        ("key", "summary", "consequence"),
    )
    content["worldFacts"] = _validate_source_digest_objects(
        content.get("worldFacts"),
        ("key", "summary"),
    )
    threads = _validate_source_digest_objects(
        content.get("plotThreads"),
        ("key", "state"),
    )
    if any(
        str(item["state"]) not in {"opened", "advanced", "resolved"}
        for item in threads
    ):
        raise ValueError("source chapter digest plot thread state is invalid")
    content["plotThreads"] = threads
    content["themes"] = _validate_source_digest_strings(content.get("themes"))
    content["adaptationRisks"] = _validate_source_digest_strings(
        content.get("adaptationRisks")
    )
    normalized["payload"]["contentJson"] = content
    return normalized


def _validate_source_digest_objects(value, fields):
    if not isinstance(value, list) or len(value) > 24:
        raise ValueError("source chapter digest array is invalid")
    result = []
    for item in value:
        if not isinstance(item, Mapping) or set(item) != set(fields):
            raise ValueError("source chapter digest item fields are invalid")
        normalized = {
            field: str(item.get(field) or "").strip()
            for field in fields
        }
        if any(not field_value for field_value in normalized.values()):
            raise ValueError("source chapter digest item is incomplete")
        result.append(normalized)
    keys = [item[fields[0]] for item in result]
    if len(keys) != len(set(keys)):
        raise ValueError("source chapter digest item keys must be unique")
    return result


def _validate_source_digest_strings(value):
    if (
        not isinstance(value, list)
        or len(value) > 24
        or any(not isinstance(item, str) or not item.strip() for item in value)
        or len(value) != len(set(item.strip() for item in value))
    ):
        raise ValueError("source chapter digest string array is invalid")
    return [item.strip() for item in value]


def _validate_source_analysis_section_candidate(
    candidate: Mapping[str, Any],
    section_key: str,
) -> dict[str, Any]:
    normalized = _validate_document_section_candidate(candidate, section_key)
    content = dict(normalized["payload"]["contentJson"])
    if section_key == "characters":
        valid = set(content) == {"characters"} and isinstance(
            content.get("characters"), list
        )
    elif section_key == "story":
        story = content.get("story")
        valid = (
            set(content) == {"story"}
            and isinstance(story, Mapping)
            and set(story) == {"beats", "openThreads"}
            and isinstance(story.get("beats"), list)
            and isinstance(story.get("openThreads"), list)
        )
    elif section_key == "world":
        world = content.get("world")
        valid = (
            set(content) == {"world"}
            and isinstance(world, Mapping)
            and set(world) == {"rules", "locations", "factions"}
            and all(
                isinstance(world.get(key), list)
                for key in ("rules", "locations", "factions")
            )
        )
    elif section_key == "themes":
        valid = set(content) == {"themes"} and isinstance(
            content.get("themes"), list
        )
    elif section_key == "adaptation_risks":
        valid = set(content) == {"adaptationRisks"} and isinstance(
            content.get("adaptationRisks"), list
        )
    else:
        valid = False
    if not valid:
        raise ValueError("source analysis section fields are invalid")
    normalized["payload"]["contentJson"] = content
    return normalized


def _reject_structure_detail(
    value: object,
    *,
    forbidden: frozenset[str] = frozenset({
        "episodes",
        "scenes",
        "sceneText",
        "dialogue",
    }),
) -> None:
    if isinstance(value, Mapping):
        if forbidden.intersection(str(key) for key in value):
            raise ValueError(
                "structure candidate cannot contain episode or scene detail"
            )
        for nested in value.values():
            _reject_structure_detail(nested, forbidden=forbidden)
    elif isinstance(value, list):
        for nested in value:
            _reject_structure_detail(nested, forbidden=forbidden)


def _structure_candidate_shell(
    candidate: Mapping[str, Any],
    *,
    section_key: str,
) -> tuple[str, str, Mapping[str, Any]]:
    value = dict(candidate.get("payload") or {})
    title = str(value.get("title") or "").strip()
    text = str(candidate.get("contentText") or "").strip()
    content = value.get("contentJson")
    if (
        str(value.get("sectionKey") or "") != section_key
        or not title
        or not text
        or not isinstance(content, Mapping)
    ):
        raise ValueError("structure candidate is incomplete")
    return title, text, content


def _validate_structure_series_arc_index_candidate(
    candidate: Mapping[str, Any],
) -> dict[str, Any]:
    title, text, content = _structure_candidate_shell(
        candidate,
        section_key="series_arc:index",
    )
    phases = content.get("phases")
    if not isinstance(phases, list) or not 1 <= len(phases) <= 12:
        raise ValueError("structure series arc index is incomplete")
    _reject_structure_detail(content)
    normalized = []
    for raw in phases:
        if not isinstance(raw, Mapping) or set(raw) != {
            "key",
            "title",
            "objective",
        }:
            raise ValueError("structure series arc index entry is invalid")
        key = str(raw.get("key") or "").strip()
        phase_title = str(raw.get("title") or "").strip()
        objective = str(raw.get("objective") or "").strip()
        if (
            _SAFE_STRUCTURE_KEY.fullmatch(key) is None
            or not phase_title
            or not objective
        ):
            raise ValueError("structure series arc identity is incomplete")
        normalized.append({
            "key": key,
            "title": phase_title,
            "objective": objective,
        })
    keys = [item["key"] for item in normalized]
    titles = [item["title"] for item in normalized]
    if len(keys) != len(set(keys)) or len(titles) != len(set(titles)):
        raise ValueError("structure series arc identities must be unique")
    return {
        **dict(candidate),
        "payload": {
            "sectionKey": "series_arc:index",
            "title": title,
            "contentJson": {"phases": normalized},
        },
        "contentText": text,
    }


def _validate_structure_series_arc_phase_candidate(
    candidate: Mapping[str, Any],
    *,
    phase_key: str,
    phase_title: str,
    phase_objective: str,
) -> dict[str, Any]:
    section_key = f"series_arc:phase:{phase_key}"
    title, text, content = _structure_candidate_shell(
        candidate,
        section_key=section_key,
    )
    _reject_structure_detail(content)
    series_arc = content.get("seriesArc")
    phases = series_arc.get("phases") if isinstance(series_arc, Mapping) else None
    if (
        set(content) != {"seriesArc"}
        or not isinstance(series_arc, Mapping)
        or set(series_arc) != {"phases"}
        or not isinstance(phases, list)
        or len(phases) != 1
        or not isinstance(phases[0], Mapping)
    ):
        raise ValueError("structure series arc phase is incomplete")
    phase = dict(phases[0])
    required = (
        "key",
        "title",
        "objective",
        "centralConflict",
        "turningPoint",
        "exitState",
    )
    if set(phase) != set(required) or (
        str(phase.get("key") or "").strip() != phase_key
        or str(phase.get("title") or "").strip() != phase_title
        or str(phase.get("objective") or "").strip() != phase_objective
    ):
        raise ValueError("structure series arc phase conflicts with its index")
    if any(not str(phase.get(key) or "").strip() for key in required):
        raise ValueError("structure series arc phase fields are incomplete")
    normalized = {key: str(phase[key]).strip() for key in required}
    return {
        **dict(candidate),
        "payload": {
            "sectionKey": section_key,
            "title": title,
            "contentJson": {"seriesArc": {"phases": [normalized]}},
        },
        "contentText": text,
    }


def _validate_structure_character_arcs_index_candidate(
    candidate: Mapping[str, Any],
) -> dict[str, Any]:
    title, text, content = _structure_candidate_shell(
        candidate,
        section_key="character_arcs:index",
    )
    characters = content.get("characters")
    if (
        set(content) != {"characters"}
        or not isinstance(characters, list)
        or not 1 <= len(characters) <= 12
    ):
        raise ValueError("structure character index is incomplete")
    normalized = []
    for raw in characters:
        if not isinstance(raw, Mapping) or set(raw) != {"key", "name"}:
            raise ValueError("structure character index entry is invalid")
        key = str(raw.get("key") or "").strip()
        name = str(raw.get("name") or "").strip()
        if _SAFE_STRUCTURE_KEY.fullmatch(key) is None or not name:
            raise ValueError("structure character identity is incomplete")
        normalized.append({"key": key, "name": name})
    keys = [item["key"] for item in normalized]
    names = [item["name"] for item in normalized]
    if len(keys) != len(set(keys)) or len(names) != len(set(names)):
        raise ValueError("structure character identities must be unique")
    return {
        **dict(candidate),
        "payload": {
            "sectionKey": "character_arcs:index",
            "title": title,
            "contentJson": {"characters": normalized},
        },
        "contentText": text,
    }


def _validate_structure_character_arc_candidate(
    candidate: Mapping[str, Any],
    *,
    character_key: str,
    character_name: str,
) -> dict[str, Any]:
    section_key = f"character_arcs:character:{character_key}"
    title, text, content = _structure_candidate_shell(
        candidate,
        section_key=section_key,
    )
    arcs = content.get("characterArcs")
    if (
        set(content) != {"characterArcs"}
        or not isinstance(arcs, list)
        or len(arcs) != 1
        or not isinstance(arcs[0], Mapping)
    ):
        raise ValueError("structure character arc is incomplete")
    arc = dict(arcs[0])
    required = (
        "key",
        "startState",
        "desire",
        "turningEpisodes",
        "endState",
    )
    if (
        set(arc) != set(required)
        or str(arc.get("key") or "").strip() != character_key
    ):
        raise ValueError("structure character arc conflicts with its index")
    turning = arc.get("turningEpisodes")
    if (
        not isinstance(turning, list)
        or not turning
        or any(
            not isinstance(value, str) or not value.strip()
            for value in turning
        )
    ):
        raise ValueError("structure character turning episodes are incomplete")
    normalized_turning = [str(value).strip() for value in turning]
    if len(normalized_turning) != len(set(normalized_turning)):
        raise ValueError("structure character turning episodes must be unique")
    if any(
        not str(arc.get(key) or "").strip()
        for key in ("startState", "desire", "endState")
    ):
        raise ValueError("structure character arc fields are incomplete")
    return {
        **dict(candidate),
        "payload": {
            "sectionKey": section_key,
            "title": title or f"{character_name}人物弧",
            "contentJson": {"characterArcs": [{
                "key": character_key,
                "startState": str(arc["startState"]).strip(),
                "desire": str(arc["desire"]).strip(),
                "turningEpisodes": normalized_turning,
                "endState": str(arc["endState"]).strip(),
            }]},
        },
        "contentText": text,
    }


def _validate_structure_episode_plan_index_candidate(
    candidate: Mapping[str, Any],
) -> dict[str, Any]:
    value = dict(candidate.get("payload") or {})
    title = str(value.get("title") or "").strip()
    text = str(candidate.get("contentText") or "").strip()
    content = value.get("contentJson")
    episodes = content.get("episodes") if isinstance(content, Mapping) else None
    if (
        str(value.get("sectionKey") or "") != "episode_plan:index"
        or not title
        or not text
        or not isinstance(episodes, list)
        or not 1 <= len(episodes) <= 100
    ):
        raise ValueError("structure episode index is incomplete")
    _reject_structure_detail(content, forbidden=frozenset({
        "scenes",
        "sceneText",
        "dialogue",
    }))
    normalized = []
    for raw in episodes:
        if not isinstance(raw, Mapping):
            raise ValueError("structure episode index entry is invalid")
        number = int(raw.get("number") or 0)
        episode_id = str(raw.get("id") or "").strip()
        episode_title = str(raw.get("title") or "").strip()
        summary = str(raw.get("summary") or "").strip()
        if not episode_id or not episode_title or not summary:
            raise ValueError("structure episode index identity is incomplete")
        source_ids = raw.get("sourceChapterIds")
        if source_ids is not None and not isinstance(source_ids, list):
            raise ValueError("structure episode source ids are invalid")
        if set(raw) - {
            "number", "id", "title", "summary", "sourceChapterIds",
        }:
            raise ValueError("structure episode index contains detail fields")
        normalized.append({
            "number": number,
            "id": episode_id,
            "title": episode_title,
            "summary": summary,
            **(
                {"sourceChapterIds": list(dict.fromkeys(
                    str(item).strip()
                    for item in source_ids
                    if str(item).strip()
                ))}
                if isinstance(source_ids, list)
                else {}
            ),
        })
    numbers = [item["number"] for item in normalized]
    ids = [item["id"] for item in normalized]
    titles = [item["title"] for item in normalized]
    if (
        numbers != list(range(1, len(normalized) + 1))
        or len(ids) != len(set(ids))
        or len(titles) != len(set(titles))
    ):
        raise ValueError("structure episode index must be ordered and unique")
    return {
        **dict(candidate),
        "payload": {
            "sectionKey": "episode_plan:index",
            "title": title,
            "contentJson": {"episodes": normalized},
        },
        "contentText": text,
    }


def _validate_structure_episode_plan_fragment_candidate(
    candidate: Mapping[str, Any],
    *,
    episode_number: int,
    episode_id: str,
    episode_title: str,
) -> dict[str, Any]:
    value = dict(candidate.get("payload") or {})
    title = str(value.get("title") or "").strip()
    text = str(candidate.get("contentText") or "").strip()
    content = value.get("contentJson")
    episodes = content.get("episodes") if isinstance(content, Mapping) else None
    expected_key = f"episode_plan:episode-{episode_number}"
    if (
        str(value.get("sectionKey") or "") != expected_key
        or not title
        or not text
        or not isinstance(episodes, list)
        or len(episodes) != 1
        or not isinstance(episodes[0], Mapping)
    ):
        raise ValueError("structure episode fragment is incomplete")
    episode = dict(episodes[0])
    if (
        int(episode.get("number") or 0) != episode_number
        or str(episode.get("id") or "").strip() != episode_id
        or str(episode.get("title") or "").strip() != episode_title
    ):
        raise ValueError("structure episode fragment conflicts with its index")
    if any(
        not str(episode.get(key) or "").strip()
        for key in ("summary", "objective", "conflict", "turn", "hook")
    ):
        raise ValueError("structure episode planning fields are required")
    _reject_structure_detail(
        episode,
        forbidden=frozenset({"scenes", "sceneText", "dialogue", "script"}),
    )
    return {
        **dict(candidate),
        "payload": {
            "sectionKey": expected_key,
            "title": title,
            "contentJson": {"episodes": [episode]},
        },
        "contentText": text,
    }


def _review_input_ref(
    *,
    reviewed_draft_id: str,
    episode_number: int,
    scene_ids: Sequence[str],
    scene_plan_revision_id: str | None,
    content_digest: str,
) -> ReviewEpisodeInputRef:
    return ReviewEpisodeInputRef(
        reviewed_revision_id=reviewed_draft_id,
        episode_number=episode_number,
        scene_part_refs=tuple(
            f"{reviewed_draft_id}#scene:{scene_id}"
            for scene_id in scene_ids
        ),
        scene_plan_revision_id=str(scene_plan_revision_id or ""),
        content_digest=content_digest,
    )


def _validate_draft_episode_parts(
    task: Mapping[str, Any],
    unit: Mapping[str, Any],
) -> dict[str, Any]:
    unit_input = dict(unit.get("input") or {})
    number = int(unit_input.get("episodeNumber") or 0)
    expected_ids = tuple(str(value) for value in unit_input.get("sceneIds") or ())
    scenes = _completed_part_outputs(
        task,
        kind="generate_draft_scene",
        episode_number=number,
    )
    metadata = _completed_part_outputs(
        task,
        kind="generate_episode_metadata",
        episode_number=number,
    )
    if tuple(str(scene.get("sceneId") or "") for scene in scenes) != expected_ids:
        raise ValueError("draft validation requires every ordered scene Part")
    if len(metadata) != 1:
        raise ValueError("draft validation requires one episode metadata Part")
    evidence_output = _dependency_output(task, unit, "collect_evidence")
    descriptor = evidence_output.get("evidenceDescriptor")
    if not isinstance(descriptor, Mapping):
        raise ValueError("draft validation requires an evidence descriptor")
    evidence_scene_list_id = str(
        descriptor.get("sceneListRevisionId") or ""
    )
    scene_list_ids = {
        str(scene.get("sceneListId") or evidence_scene_list_id)
        for scene in scenes
    }
    if len(scene_list_ids) != 1 or "" in scene_list_ids:
        raise ValueError("draft scene Parts do not share one accepted scene list")
    meta = metadata[0]
    scene_texts = [{
        "sceneId": str(scene["sceneId"]),
        "contentText": str(scene["sceneText"]),
    } for scene in scenes]
    result = {
        "sceneListId": next(iter(scene_list_ids)),
        "episodeDraft": {
            "episodeNumber": number,
            "title": str(meta["title"]),
            "sceneIds": list(expected_ids),
            "sceneTexts": scene_texts,
            "sceneExecutions": [{"sceneId": value, "status": "completed"} for value in expected_ids],
            "contentText": "\n\n".join(item["contentText"] for item in scene_texts),
            "continuitySummary": str(meta["continuitySummary"]),
        },
        "sourceRunIds": _source_run_ids((*scenes, meta)),
        "runId": str(meta.get("runId") or "") or None,
    }
    return _with_validation_receipt(result)


def _validate_review_episode_parts(
    task: Mapping[str, Any],
    unit: Mapping[str, Any],
) -> dict[str, Any]:
    unit_input = dict(unit.get("input") or {})
    number = int(unit_input.get("episodeNumber") or 0)
    expected_dimensions = (
        "continuity", "character_arc", "structure_rhythm", "dialogue", "format",
    )
    parts = _completed_part_outputs(
        task,
        kind="generate_review_dimension",
        episode_number=number,
    )
    if tuple(str(part.get("reviewDimension") or "") for part in parts) != expected_dimensions:
        raise ValueError("review validation requires all five dimension Parts")
    evidence_output = _dependency_output(task, unit, "collect_evidence")
    descriptor = evidence_output.get("evidenceDescriptor")
    if not isinstance(descriptor, Mapping):
        raise ValueError("review validation requires an evidence descriptor")
    review_ref = descriptor.get("reviewInputRef")
    if not isinstance(review_ref, Mapping):
        raise ValueError("review validation requires an immutable input ref")
    reviewed_draft_id = str(
        review_ref.get("reviewedRevisionId") or ""
    )
    reviewed_digest = str(
        review_ref.get("contentDigest") or ""
    )
    allowed_scene_ids = tuple(unit_input.get("sceneIds") or ())
    content_values = []
    for part in parts:
        dimension = str(part.get("reviewDimension") or "")
        content = dict(part.get("contentJson") or {})
        if (
            content.get("reviewDimension") == dimension
            and content.get("reviewedContentDigest") == reviewed_digest
        ):
            content_values.append(content)
            continue
        normalized = _validate_review_dimension_candidate(
            {
                "artifactId": part.get("artifactId"),
                "payload": {
                    "episodeNumber": number,
                    "reviewDimension": dimension,
                    "title": part.get("title"),
                    "contentJson": content,
                },
                "contentText": part.get("contentText"),
            },
            episode_number=number,
            dimension=dimension,
            allowed_scene_ids=allowed_scene_ids,
            reviewed_draft_id=reviewed_draft_id,
            reviewed_content_digest=reviewed_digest,
        )
        content_values.append(dict(normalized["payload"]["contentJson"]))
    digests = {str(value.get("reviewedContentDigest") or "") for value in content_values}
    draft_ids = {str(value.get("reviewedDraftId") or "") for value in content_values}
    if len(digests) != 1 or "" in digests or len(draft_ids) != 1 or "" in draft_ids:
        raise ValueError("review dimension Parts do not share one immutable input")
    issues = [dict(issue) for value in content_values for issue in value["issues"]]
    part_receipts = tuple(
        str(
            (part.get("validationReceipt") or {}).get("contentDigest")
            or part.get("artifactDigest")
            or part.get("artifactId")
            or ""
        )
        for part in parts
    )
    episode_result = ReviewEpisodeResult(
        episode_number=number,
        reviewed_revision_id=next(iter(draft_ids)),
        reviewed_content_digest=next(iter(digests)),
        issues=tuple(issues),
        verdict=_aggregate_review_verdict(
            value["verdict"] for value in content_values
        ),
        part_receipts=part_receipts,
    )
    result = {
        "title": f"第 {number} 集审阅",
        "contentText": "\n\n".join(str(part.get("contentText") or "") for part in parts),
        "contentJson": episode_result.to_mapping(),
        "sourceRunIds": _source_run_ids(parts),
        "runId": str(parts[-1].get("runId") or "") or None,
    }
    return _with_validation_receipt(result)


def _validate_document_parts(
    task: Mapping[str, Any],
    unit: Mapping[str, Any],
) -> dict[str, Any]:
    role = str(task["targetRole"])
    grouped_sections = _ordered_document_sections(task)
    sections = [
        section
        for _key, values in grouped_sections
        for section in values
    ]
    if not sections:
        raise ValueError("document validation requires section Parts")
    merged: dict[str, Any] = {}
    for section in sections:
        merged = _merge_document_json(merged, dict(section.get("contentJson") or {}))
    evidence_output = _dependency_output(task, unit, "collect_evidence")
    descriptor = evidence_output.get("evidenceDescriptor")
    if not isinstance(descriptor, Mapping):
        raise ValueError("document validation requires an evidence descriptor")
    structure_numbers = tuple(
        int(value) for value in descriptor.get("structureEpisodeNumbers") or ()
    )
    structure_revision_id = (
        str(descriptor.get("structureRevisionId") or "") or None
    )
    reviewed_draft_id = (
        str(descriptor.get("currentDraftRevisionId") or "") or None
    )
    value = _validate_deliverable(
        role,
        {
            "title": _ROLE_LABELS.get(role, "剧本交付物"),
            "contentText": "\n\n".join(str(section["contentText"]) for section in sections),
            "contentJson": merged,
        },
        structure_id=structure_revision_id,
        structure_episode_numbers=structure_numbers,
        reviewed_draft_id=reviewed_draft_id,
    )
    return _with_validation_receipt({
        **value,
        "sections": [
            {
                "key": key,
                "title": (
                    "分集结构"
                    if key == "episode_plan" and len(values) > 1
                    else str(values[0].get("title") or "")
                ),
            }
            for key, values in grouped_sections
        ],
        "sourceRunIds": _source_run_ids(sections),
        "runId": str(sections[-1].get("runId") or "") or None,
    })


def _ordered_document_sections(
    task: Mapping[str, Any],
) -> list[tuple[str, list[dict[str, Any]]]]:
    section_order = []
    outputs: dict[str, list[dict[str, Any]]] = {}
    expected_output_ids = []
    completed_output_ids = []
    for unit in task.get("units") or ():
        if not isinstance(unit, Mapping):
            continue
        kind = str(unit.get("kind") or "")
        unit_input = unit.get("input")
        if not isinstance(unit_input, Mapping) or kind not in {
            "generate_document_section",
            "expand_structure_series_arc",
            "expand_structure_episode_plan",
            "expand_structure_character_arcs",
            "project_structure_hooks",
        }:
            continue
        if any(
            unit_input.get(flag) is True
            for flag in ("sourceChapterDigest", "sourceDigestReduction")
        ):
            continue
        section_key = str(
            unit_input.get("documentSectionKey")
            or unit_input.get("sectionKey")
            or ""
        )
        if section_key and section_key not in section_order:
            section_order.append(section_key)
        if kind.startswith("expand_structure_") or any(
            unit_input.get(flag) is True
            for flag in (
                "seriesArcIndex",
                "episodePlanIndex",
                "characterArcsIndex",
            )
        ):
            continue
        unit_id = str(unit.get("id") or "")
        if unit.get("required", True):
            expected_output_ids.append(unit_id)
        if unit.get("status") != "completed":
            continue
        output = unit.get("output")
        if not isinstance(output, Mapping):
            raise RuntimeError(f"completed screenplay Part {unit_id} has no output")
        completed_output_ids.append(unit_id)
        normalized_output = dict(output)
        if "episodeNumber" not in normalized_output and unit_input.get(
            "episodeNumber"
        ) is not None:
            normalized_output["episodeNumber"] = unit_input["episodeNumber"]
        for position_key in ("phasePosition", "characterPosition"):
            if unit_input.get(position_key) is not None:
                normalized_output[position_key] = unit_input[position_key]
        outputs.setdefault(section_key, []).append(normalized_output)
    if completed_output_ids != expected_output_ids:
        raise ValueError("document section Parts are incomplete or out of order")
    for key, values in outputs.items():
        if key == "episode_plan":
            values.sort(key=lambda value: int(value.get("episodeNumber") or 0))
        elif key == "series_arc":
            values.sort(key=lambda value: int(value.get("phasePosition") or 0))
        elif key == "character_arcs":
            values.sort(key=lambda value: int(
                value.get("characterPosition") or 0
            ))
        elif len(values) != 1:
            raise ValueError(f"document section {key!r} is ambiguous")
    return [
        (key, outputs[key])
        for key in section_order
        if key in outputs
    ]


def _merge_document_json(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
) -> dict[str, Any]:
    result = dict(left)
    for key, value in right.items():
        if key not in result:
            result[key] = value
        elif isinstance(result[key], Mapping) and isinstance(value, Mapping):
            result[key] = _merge_document_json(result[key], value)
        elif isinstance(result[key], list) and isinstance(value, list):
            result[key] = [*result[key], *value]
        elif result[key] != value:
            raise ValueError(f"document sections conflict at {key}")
    return result


def _with_validation_receipt(value: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(value)
    result["validationReceipt"] = _canonical_digest(result)
    return result


def _canonical_digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")).hexdigest()


def _final_response_instruction() -> str:
    return """你负责为已经完成校验、但尚未向用户公布的剧本候选稿撰写最终答复。
使用 2 至 5 句自然语言，直接回应用户原始请求，准确总结完成了哪些候选内容以及值得注意的覆盖范围；如果合适，再说明这些内容仍可继续编辑。
只能依据输入中的公开事实，不得声称候选稿已经采纳，不得编造版本号、链接或未提供的结果。
不得复述剧本正文，不得输出工具过程、内部协议、推理过程，也不得套用固定模板。"""


def _public_candidate_fact(
    role: str,
    output: Mapping[str, Any],
) -> dict[str, Any]:
    if role == "screenplayDraft":
        draft = output.get("episodeDraft")
        if not isinstance(draft, Mapping):
            raise ValueError("validated screenplay episode fact is missing")
        fact: dict[str, Any] = {
            "episodeNumber": int(draft.get("episodeNumber") or 0),
            "title": str(draft.get("title") or "").strip(),
            "sceneCount": len(tuple(draft.get("sceneIds") or ())),
        }
    elif role == "review":
        content = output.get("contentJson")
        if not isinstance(content, Mapping):
            raise ValueError("validated screenplay review fact is missing")
        fact = {
            "title": str(output.get("title") or "").strip(),
            "episodeNumber": int(content.get("episodeNumber") or 0),
            "verdict": str(content.get("verdict") or ""),
            "issueCount": len(tuple(content.get("issues") or ())),
        }
    else:
        sections = output.get("sections")
        fact = {
            "title": str(output.get("title") or "").strip(),
            "sections": [
                {
                    "key": str(item.get("key") or ""),
                    "title": str(item.get("title") or ""),
                }
                for item in sections or ()
                if isinstance(item, Mapping)
            ],
        }
    if not fact.get("title"):
        raise ValueError("validated screenplay candidate title is missing")
    if role == "screenplayDraft" and int(fact["episodeNumber"]) <= 0:
        raise ValueError("validated screenplay episode number is missing")
    return fact


def _validate_scene_candidate(
    candidate: Mapping[str, Any],
    expected_scene_id: str,
) -> dict[str, Any]:
    value = dict(candidate.get("payload") or {})
    scene_id = str(value.get("sceneId") or "").strip()
    scene_text = str(value.get("sceneText") or "").strip()
    if scene_id != expected_scene_id or not scene_text:
        raise ValueError("scene candidate does not match the requested scene")
    scene = {
        "sceneId": scene_id,
        "sceneText": scene_text,
    }
    if str(candidate.get("contentText") or "").strip() != scene_text:
        raise ValueError("scene candidate contentText does not match sceneText")
    return {**dict(candidate), "payload": scene, "contentText": scene_text}


def _validate_episode_metadata_candidate(
    candidate: Mapping[str, Any],
    episode_number: int,
) -> dict[str, Any]:
    raw = candidate.get("payload")
    if (
        not isinstance(raw, Mapping)
        or set(raw) != {"episodeNumber", "title", "continuitySummary"}
        or isinstance(raw.get("episodeNumber"), bool)
        or not isinstance(raw.get("episodeNumber"), int)
        or raw.get("episodeNumber") != episode_number
        or not isinstance(raw.get("title"), str)
        or not isinstance(raw.get("continuitySummary"), str)
    ):
        raise ValueError("episode metadata number does not match")
    title = raw["title"].strip()
    continuity = raw["continuitySummary"].strip()
    if not title or not continuity:
        raise ValueError("episode metadata title and continuity are required")
    metadata = {
        "episodeNumber": episode_number,
        "title": title,
        "continuitySummary": continuity,
    }
    return {**dict(candidate), "payload": metadata, "contentText": ""}


def _validate_deliverable(
    role: str,
    value: dict[str, Any],
    *,
    structure_id: str | None,
    structure_episode_numbers: Sequence[int],
    reviewed_draft_id: str | None,
) -> dict[str, Any]:
    title = str(value.get("title") or "").strip()
    text = str(value.get("contentText") or "").strip()
    content = value.get("contentJson")
    if not title or not text or not isinstance(content, Mapping):
        raise ValueError("deliverable title, contentText and contentJson are required")
    normalized = dict(content)
    normalized.update({"schemaVersion": 1, "documentKind": _DOCUMENT_KIND[role]})
    if role == "sourceAnalysis":
        _validate_source_analysis_document(normalized)
    elif role == "creativeBrief":
        _validate_creative_brief_document(normalized)
    elif role == "structure":
        _validate_structure(normalized)
    elif role == "sceneList":
        _validate_scene_list(normalized, structure_episode_numbers)
        normalized["structureId"] = structure_id
    elif role == "review":
        if any(
            key in normalized
            for key in (
                "error",
                "errorCode",
                "executionError",
                "failedEpisodes",
                "failure",
                "failureCode",
            )
        ):
            raise ValueError("review content cannot contain execution metadata")
        if normalized.get("verdict") not in {"ready", "revise", "major_rework"}:
            raise ValueError("review verdict is invalid")
        issues = _validate_review_issues(normalized.get("issues"))
        normalized["issues"] = issues
        normalized["issueCount"] = len(issues)
        normalized["criticalIssueCount"] = sum(
            issue["severity"] == "critical" for issue in issues
        )
        normalized["reviewedDraftId"] = reviewed_draft_id
    return {
        "title": title,
        "contentText": text,
        "contentJson": normalized,
    }


def _validate_source_analysis_document(content: Mapping[str, Any]) -> None:
    story = content.get("story")
    world = content.get("world")
    if (
        set(content) != {
            "schemaVersion",
            "documentKind",
            "characters",
            "story",
            "world",
            "themes",
            "adaptationRisks",
        }
        or content.get("schemaVersion") != 1
        or content.get("documentKind") != "source_analysis"
        or not isinstance(content.get("characters"), list)
        or not isinstance(content.get("themes"), list)
        or not isinstance(content.get("adaptationRisks"), list)
        or not isinstance(story, Mapping)
        or set(story) != {"beats", "openThreads"}
        or not isinstance(story.get("beats"), list)
        or not isinstance(story.get("openThreads"), list)
        or not isinstance(world, Mapping)
        or set(world) != {"rules", "locations", "factions"}
        or any(
            not isinstance(world.get(key), list)
            for key in ("rules", "locations", "factions")
        )
    ):
        raise ValueError("source analysis document fields are invalid")


def _validate_creative_brief_document(content: dict[str, Any]) -> None:
    expected = {
        "schemaVersion",
        "documentKind",
        "fields",
        "coreCharacters",
        "worldRules",
        "visualIdentity",
        "adaptationRules",
    }
    if (
        set(content) != expected
        or content.get("schemaVersion") != 1
        or content.get("documentKind") != "creative_brief"
    ):
        raise ValueError("creative brief document fields are invalid")
    content["fields"] = _creative_brief_required_strings(
        content.get("fields"),
        (*_CREATIVE_BRIEF_POSITIONING_FIELDS, *_CREATIVE_BRIEF_PREMISE_FIELDS),
        label="document",
    )
    content["coreCharacters"] = _creative_brief_characters(
        content.get("coreCharacters")
    )
    content["worldRules"] = _creative_brief_world_rules(
        content.get("worldRules")
    )
    content["visualIdentity"] = _creative_brief_required_strings(
        {"visualIdentity": content.get("visualIdentity")},
        ("visualIdentity",),
        label="visual identity",
    )["visualIdentity"]
    content["adaptationRules"] = _creative_brief_adaptation_rules(
        content.get("adaptationRules")
    )


def _validate_deliverable_candidate(
    role: str,
    candidate: Mapping[str, Any],
    *,
    structure_id: str | None,
    structure_episode_numbers: Sequence[int],
    reviewed_draft_id: str | None,
) -> dict[str, Any]:
    value = dict(candidate.get("payload") or {})
    value["contentText"] = str(candidate.get("contentText") or "")
    normalized = _validate_deliverable(
        role,
        value,
        structure_id=structure_id,
        structure_episode_numbers=structure_episode_numbers,
        reviewed_draft_id=reviewed_draft_id,
    )
    return {
        **dict(candidate),
        "payload": {
            "title": normalized["title"],
            "contentJson": normalized["contentJson"],
        },
        "contentText": normalized["contentText"],
    }


def _validate_scene_list_fragment_candidate(
    candidate: Mapping[str, Any],
    episode_number: int,
) -> dict[str, Any]:
    raw = candidate.get("payload")
    if (
        not isinstance(raw, Mapping)
        or set(raw) != {"sectionKey", "title", "contentJson"}
        or raw.get("sectionKey") != f"episode-{episode_number}"
        or not isinstance(raw.get("title"), str)
    ):
        raise ValueError("scene list fragment fields are invalid")
    value = dict(raw)
    title = raw["title"].strip()
    text = str(candidate.get("contentText") or "").strip()
    content = value.get("contentJson")
    if not title or not text or not isinstance(content, Mapping):
        raise ValueError("scene list fragment is incomplete")
    if set(content) != {"scenes"}:
        raise ValueError("scene list fragment fields are invalid")
    scenes = content.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        raise ValueError("scene list fragment requires scenes")
    normalized_scenes = [dict(scene) for scene in scenes if isinstance(scene, Mapping)]
    if len(normalized_scenes) != len(scenes) or any(
        int(scene.get("episodeNumber") or 0) != episode_number
        for scene in normalized_scenes
    ):
        raise ValueError("scene list fragment contains another episode")
    _validate_scene_list({"scenes": normalized_scenes}, (episode_number,))
    return {
        **dict(candidate),
        "payload": {
            "sectionKey": f"episode-{episode_number}",
            "title": title,
            "contentJson": {"scenes": normalized_scenes},
        },
        "contentText": text,
    }


def _aggregate_review_verdict(values) -> str:
    ranks = {"ready": 0, "revise": 1, "major_rework": 2}
    normalized = tuple(str(value) for value in values)
    if not normalized or any(value not in ranks for value in normalized):
        raise ValueError("review fragment verdict is invalid")
    return max(normalized, key=ranks.__getitem__)


def _validate_structure(content: Mapping[str, Any]) -> None:
    series_arc = content.get("seriesArc")
    phases = series_arc.get("phases") if isinstance(series_arc, Mapping) else None
    if not isinstance(phases, list) or not 1 <= len(phases) <= 12:
        raise ValueError("structure series arc phases are required")
    _reject_structure_detail(phases)
    phase_keys = []
    for phase in phases:
        if not isinstance(phase, Mapping) or any(
            not str(phase.get(key) or "").strip()
            for key in (
                "key",
                "title",
                "objective",
                "centralConflict",
                "turningPoint",
                "exitState",
            )
        ):
            raise ValueError("structure series arc phase fields are required")
        phase_keys.append(str(phase["key"]).strip())
    if len(phase_keys) != len(set(phase_keys)):
        raise ValueError("structure series arc phase keys must be unique")

    episodes = content.get("episodes")
    if not isinstance(episodes, list) or not episodes:
        raise ValueError("structure episodes are required")
    numbers = [int(item.get("number") or 0) for item in episodes if isinstance(item, Mapping)]
    ids = [str(item.get("id") or "").strip() for item in episodes if isinstance(item, Mapping)]
    if len(numbers) != len(episodes) or any(number <= 0 for number in numbers):
        raise ValueError("structure episode numbers are invalid")
    titles = [
        str(item.get("title") or "").strip()
        for item in episodes
        if isinstance(item, Mapping)
    ]
    if (
        numbers != list(range(1, len(episodes) + 1))
        or len(ids) != len(set(ids))
        or len(titles) != len(set(titles))
        or any(not item for item in ids)
    ):
        raise ValueError("structure episode identity is invalid")
    if any(
        any(not str(item.get(key) or "").strip() for key in (
            "title",
            "summary",
            "objective",
            "conflict",
            "turn",
            "hook",
        ))
        for item in episodes
        if isinstance(item, Mapping)
    ):
        raise ValueError("structure episode planning fields are required")
    for episode in episodes:
        _reject_structure_detail(
            episode,
            forbidden=frozenset({"scenes", "sceneText", "dialogue", "script"}),
        )

    character_arcs = content.get("characterArcs")
    if (
        not isinstance(character_arcs, list)
        or not 1 <= len(character_arcs) <= 12
    ):
        raise ValueError("structure character arcs are required")
    character_keys = []
    episode_ids = set(ids)
    for arc in character_arcs:
        if not isinstance(arc, Mapping) or any(
            not str(arc.get(key) or "").strip()
            for key in ("key", "startState", "desire", "endState")
        ):
            raise ValueError("structure character arc fields are required")
        turning = arc.get("turningEpisodes")
        if (
            not isinstance(turning, list)
            or not turning
            or not set(str(value) for value in turning).issubset(episode_ids)
        ):
            raise ValueError("structure character arc episode refs are invalid")
        character_keys.append(str(arc["key"]).strip())
    if len(character_keys) != len(set(character_keys)):
        raise ValueError("structure character arc keys must be unique")

    hooks = content.get("hooks")
    if not isinstance(hooks, list) or len(hooks) != len(episodes):
        raise ValueError("structure hooks must cover every episode")
    hook_ids = []
    for hook in hooks:
        if (
            not isinstance(hook, Mapping)
            or not str(hook.get("episodeId") or "").strip()
            or not str(hook.get("hook") or "").strip()
        ):
            raise ValueError("structure hook is invalid")
        hook_ids.append(str(hook["episodeId"]).strip())
    if hook_ids != ids:
        raise ValueError("structure hooks must match ordered episodes")


def _validate_scene_list(
    content: Mapping[str, Any],
    structure_episode_numbers: Sequence[int],
) -> None:
    scenes = content.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        raise ValueError("scene list scenes are required")
    if any(
        not isinstance(item, Mapping)
        or set(item) != _SCENE_LIST_FIELDS
        or isinstance(item.get("episodeNumber"), bool)
        or not isinstance(item.get("episodeNumber"), int)
        or any(
            not isinstance(item.get(field), str)
            for field in _SCENE_LIST_FIELDS.difference({"episodeNumber"})
        )
        for item in scenes
    ):
        raise ValueError("scene planning fields are invalid")
    ids = [str(item.get("id") or "").strip() for item in scenes if isinstance(item, Mapping)]
    numbers = [int(item.get("episodeNumber") or 0) for item in scenes if isinstance(item, Mapping)]
    if len(ids) != len(scenes) or len(ids) != len(set(ids)) or any(not item for item in ids):
        raise ValueError("scene ids are invalid")
    if any(number <= 0 for number in numbers):
        raise ValueError("scene episode numbers are invalid")
    allowed = set(structure_episode_numbers)
    if allowed and (set(numbers) != allowed):
        raise ValueError("scene list must cover exactly the accepted structure episodes")
    if any(
        not str(item.get("heading") or "").strip()
        or not str(item.get("objective") or "").strip()
        or not str(item.get("conflict") or "").strip()
        or not str(item.get("turn") or "").strip()
        or not str(item.get("synopsis") or "").strip()
        for item in scenes
        if isinstance(item, Mapping)
    ):
        raise ValueError("scene planning fields are required")


def _validate_review_issues(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError("review issues must be an array")
    issues = [dict(item) for item in value if isinstance(item, Mapping)]
    ids = [str(issue.get("id") or "").strip() for issue in issues]
    if len(issues) != len(value) or any(not issue_id for issue_id in ids):
        raise ValueError("review issue identity is invalid")
    if len(ids) != len(set(ids)):
        raise ValueError("review issue ids must be unique")
    for issue in issues:
        severity = str(issue.get("severity") or "").strip()
        description = str(issue.get("description") or "").strip()
        scene_ids = issue.get("sceneIds")
        if (
            severity not in {"critical", "major", "minor"}
            or not description
            or not isinstance(scene_ids, list)
        ):
            raise ValueError("review issue fields are invalid")
        issue.update({
            "id": str(issue["id"]).strip(),
            "severity": severity,
            "description": description,
            "sceneIds": [str(scene_id) for scene_id in scene_ids],
        })
    return issues


def _source_run_ids(outputs: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    result = []
    for output in outputs:
        values = output.get("sourceRunIds")
        candidates = (
            output.get("runId"),
            *(values if isinstance(values, (list, tuple)) else ()),
        )
        for value in candidates:
            run_id = str(value or "").strip()
            if run_id and run_id not in result:
                result.append(run_id)
    return tuple(result)


def normalize_screenplay_candidate(
    contract: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> dict[str, Any]:
    """Apply one persisted, versioned task Candidate contract deterministically."""

    value = parse_candidate_validation_contract(contract)
    kind = str(value.get("kind") or "").strip()
    normalized_candidate = dict(candidate)
    if kind == "generic":
        return normalized_candidate
    if kind == "scene":
        scene_id = str(value.get("expectedSceneId") or "").strip()
        return _validate_scene_candidate(normalized_candidate, scene_id)
    if kind == "episode_metadata":
        episode_number = int(value.get("episodeNumber") or 0)
        return _validate_episode_metadata_candidate(
            normalized_candidate,
            episode_number,
        )
    if kind == "review_dimension":
        episode_number = int(value.get("episodeNumber") or 0)
        dimension = str(value.get("dimension") or "").strip()
        raw_scene_ids = value.get("allowedSceneIds")
        allowed_scene_ids = (
            tuple(str(item or "").strip() for item in raw_scene_ids)
            if isinstance(raw_scene_ids, list)
            else ()
        )
        reviewed_draft_id = str(value.get("reviewedDraftId") or "").strip()
        reviewed_digest = str(
            value.get("reviewedContentDigest") or ""
        ).strip()
        return _validate_review_dimension_candidate(
            normalized_candidate,
            episode_number=episode_number,
            dimension=dimension,
            allowed_scene_ids=allowed_scene_ids,
            reviewed_draft_id=reviewed_draft_id,
            reviewed_content_digest=reviewed_digest,
        )
    if kind == "document_section":
        section_key = str(value.get("sectionKey") or "").strip()
        return _validate_document_section_candidate(
            normalized_candidate,
            section_key,
        )
    if kind == "creative_brief_section":
        section_key = str(value.get("sectionKey") or "").strip()
        return _validate_creative_brief_section_candidate(
            normalized_candidate,
            section_key,
        )
    if kind == "source_chapter_digest":
        chapter_id = str(value.get("chapterId") or "").strip()
        return _validate_source_digest_candidate(
            normalized_candidate,
            section_key=f"source_digest:chapter:{chapter_id}",
            digest_id=chapter_id,
        )
    if kind == "source_digest_reduction":
        digest_id = str(value.get("digestId") or "").strip()
        return _validate_source_digest_candidate(
            normalized_candidate,
            section_key=digest_id.replace(
                "source-analysis:",
                "source_digest:",
                1,
            ),
            digest_id=digest_id,
        )
    if kind == "source_analysis_section":
        return _validate_source_analysis_section_candidate(
            normalized_candidate,
            str(value.get("sectionKey") or "").strip(),
        )
    if kind == "scene_list_fragment":
        episode_number = int(value.get("episodeNumber") or 0)
        return _validate_scene_list_fragment_candidate(
            normalized_candidate,
            episode_number,
        )
    if kind == "structure_episode_plan_index":
        return _validate_structure_episode_plan_index_candidate(
            normalized_candidate,
        )
    if kind == "structure_series_arc_index":
        return _validate_structure_series_arc_index_candidate(
            normalized_candidate,
        )
    if kind == "structure_series_arc_phase":
        return _validate_structure_series_arc_phase_candidate(
            normalized_candidate,
            phase_key=str(value.get("phaseKey") or ""),
            phase_title=str(value.get("phaseTitle") or ""),
            phase_objective=str(value.get("phaseObjective") or ""),
        )
    if kind == "structure_episode_plan_fragment":
        return _validate_structure_episode_plan_fragment_candidate(
            normalized_candidate,
            episode_number=int(value.get("episodeNumber") or 0),
            episode_id=str(value.get("episodeId") or ""),
            episode_title=str(value.get("episodeTitle") or ""),
        )
    if kind == "structure_character_arcs_index":
        return _validate_structure_character_arcs_index_candidate(
            normalized_candidate,
        )
    if kind == "structure_character_arc_fragment":
        return _validate_structure_character_arc_candidate(
            normalized_candidate,
            character_key=str(value.get("characterKey") or ""),
            character_name=str(value.get("characterName") or ""),
        )
    raise ValueError("candidate validation kind is unsupported")


__all__ = [
    "ScreenplayTaskModelCalls",
    "ScreenplayTaskUnitExecutor",
    "normalize_screenplay_candidate",
]
