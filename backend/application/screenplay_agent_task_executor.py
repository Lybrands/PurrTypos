"""Execute screenplay business units while PurrA owns orchestration."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from purra.contracts import ReasoningMode, RunLineage
from purra.json_values import thaw_json_mapping
from purra.long_tasks import DurableUnitExecutionContext, LongTaskUnitResult
from application.screenplay_agent_context import ScreenplayAgentContextQuery
from application.screenplay_part_artifacts import ScreenplayPartArtifactQuery
from domains.screenplay_agent.contracts import (
    ReviewEpisodeInputRef,
    ReviewEpisodeResult,
)
from domains.screenplay_agent.recovery import classify_screenplay_run_failure
from application.screenplay_structured_call import ScreenplayStructuredCallService
from application.screenplay_tool_calling import ScreenplayToolCallingService
from domains.screenplay.source_scope import parse_source_scope
from domains.screenplay_agent.agent_context import ScreenplayAgentDomainContext
from domains.screenplay_agent.candidate_projection import (
    SCREENPLAY_CANDIDATE_VALIDATION_PROTOCOL,
    parse_candidate_validation_contract,
)
from exceptions import AppError


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


class ScreenplayTaskModelCalls:
    def __init__(
        self,
        db,
        *,
        composition=None,
        tool_calling_service: ScreenplayToolCallingService | None = None,
    ) -> None:
        self._db = db
        self._context = ScreenplayAgentContextQuery(db)
        self._models = (
            ScreenplayStructuredCallService(
                db,
                composition=composition,
            )
            if composition is not None
            else None
        )
        self._tool_calls = tool_calling_service
        if self._models is None and self._tool_calls is None:
            raise ValueError("screenplay task requires a model execution service")

    async def execute(
        self,
        *,
        task: Mapping[str, Any],
        unit: Mapping[str, Any],
        runtime,
        signal=None,
    ) -> Mapping[str, Any]:
        kind = str(unit.get("kind") or "")
        if _requires_child_run(kind) and not str(
            task.get("rootRunId") or ""
        ).strip():
            raise RuntimeError(
                "screenplay AI Part requires its Root Run identity"
            )
        if kind == "collect_evidence":
            return await self._collect_evidence(task, unit)
        if kind == "generate_draft_scene":
            return await self._generate_draft_scene(task, unit, runtime, signal)
        if kind == "generate_episode_metadata":
            return await self._generate_episode_metadata(task, unit, runtime, signal)
        if kind == "generate_review_dimension":
            return await self._generate_review_dimension(task, unit, runtime, signal)
        if kind == "generate_document_section":
            return await self._generate_document_section(task, unit, runtime, signal)
        if kind == "validate_manifest_part":
            return self._validate_manifest_part(task, unit)
        if kind == "compose_final_response":
            return await self._compose_final_response(
                task, unit, runtime, signal
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
        accepted = await self._context.revisions(
            source_revision_refs,
            text_limit=0,
        )
        by_role = {str(item["role"]): item for item in accepted}
        scene_list_revision_id = str(
            (by_role.get("sceneList") or {}).get("revisionId") or ""
        ) or None
        descriptor: dict[str, Any] = {
            "projectId": project_id,
            "targetRole": role,
            "sourceRevisionRefs": list(source_revision_refs),
            "baseRevisionId": base_revision_id,
            "structureRevisionId": str(
                (by_role.get("structure") or {}).get("revisionId") or ""
            ) or None,
            "structureEpisodeNumbers": [
                int(item.get("number") or 0)
                for item in (
                    (by_role.get("structure") or {}).get("content", {}).get(
                        "episodes", ()
                    )
                )
                if isinstance(item, Mapping) and int(item.get("number") or 0) > 0
            ],
            "currentDraftRevisionId": str(
                (by_role.get("screenplayDraft") or {}).get("revisionId") or ""
            ) or None,
        }
        if episode_number:
            reviewed_draft_id = str(
                unit_input.get("reviewedDraftId") or ""
            ) or None
            episode_context = await self._context.episode_context(
                project_id,
                episode_number,
                draft_revision_id=reviewed_draft_id or base_revision_id,
                scene_list_revision_id=scene_list_revision_id,
            )
            scene_ids = tuple(unit_input.get("sceneIds") or ())
            plan_ids = tuple(
                str(item.get("id") or "")
                for item in (episode_context.get("episode") or {}).get("scenes", ())
                if isinstance(item, Mapping)
            )
            if scene_ids and scene_ids != plan_ids:
                raise ValueError("evidence Revision does not match the Manifest scene ids")
            descriptor.update({
                "episodeNumber": episode_number,
                "sceneIds": list(scene_ids or plan_ids),
                "sceneListRevisionId": scene_list_revision_id,
                "reviewedDraftId": reviewed_draft_id,
                "evidenceKind": str(unit_input.get("evidenceKind") or "writing"),
            })
            if unit_input.get("evidenceKind") == "review_input":
                if reviewed_draft_id is None:
                    raise ValueError("review input requires an immutable Draft Revision")
                review_input = _review_episode_input(
                    reviewed_draft_id=reviewed_draft_id,
                    episode_number=episode_number,
                    episode_context=episode_context,
                )
                descriptor["reviewInputRef"] = _review_input_ref(
                    reviewed_draft_id=reviewed_draft_id,
                    episode_number=episode_number,
                    scene_ids=tuple(review_input["sceneIds"]),
                    scene_plan_revision_id=scene_list_revision_id,
                    content_digest=str(review_input["contentDigest"]),
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

    async def _hydrate_evidence(
        self,
        task: Mapping[str, Any],
        unit: Mapping[str, Any],
    ) -> dict[str, Any]:
        checkpoint = _dependency_output(task, unit, "collect_evidence")
        legacy = checkpoint.get("evidence")
        if isinstance(legacy, Mapping):
            return dict(legacy)
        descriptor = checkpoint.get("evidenceDescriptor")
        if not isinstance(descriptor, Mapping):
            raise RuntimeError("screenplay evidence Artifact is missing")
        project_id = str(descriptor.get("projectId") or task["projectId"])
        refs = tuple(str(value) for value in descriptor.get("sourceRevisionRefs") or ())
        accepted = await self._context.revisions(refs, text_limit=18_000)
        evidence: dict[str, Any] = {
            "projectId": project_id,
            "targetRole": str(descriptor.get("targetRole") or task["targetRole"]),
            "acceptedDeliverables": accepted,
        }
        episode_number = int(descriptor.get("episodeNumber") or 0)
        if episode_number:
            scene_list_revision_id = str(
                descriptor.get("sceneListRevisionId") or ""
            ) or None
            reviewed_draft_id = str(
                descriptor.get("reviewedDraftId") or ""
            ) or None
            base_revision_id = str(
                descriptor.get("baseRevisionId") or ""
            ) or None
            episode_context = await self._context.episode_context(
                project_id,
                episode_number,
                draft_revision_id=reviewed_draft_id or base_revision_id,
                scene_list_revision_id=scene_list_revision_id,
            )
            evidence.update({
                "episodeNumber": episode_number,
                "manifest": {
                    "sceneListId": scene_list_revision_id,
                    "sceneIds": tuple(descriptor.get("sceneIds") or ()),
                },
                "episodeContext": episode_context,
            })
            if descriptor.get("evidenceKind") == "review_input":
                if reviewed_draft_id is None:
                    raise RuntimeError("review evidence has no immutable Draft Revision")
                review_input = _review_episode_input(
                    reviewed_draft_id=reviewed_draft_id,
                    episode_number=episode_number,
                    episode_context=episode_context,
                )
                expected = descriptor.get("reviewInputRef")
                actual_ref = _review_input_ref(
                    reviewed_draft_id=reviewed_draft_id,
                    episode_number=episode_number,
                    scene_ids=tuple(review_input["sceneIds"]),
                    scene_plan_revision_id=scene_list_revision_id,
                    content_digest=str(review_input["contentDigest"]),
                )
                if not isinstance(expected, Mapping) or (
                    ReviewEpisodeInputRef.from_mapping(expected) != actual_ref
                ):
                    raise RuntimeError("review evidence digest changed")
                evidence["reviewInput"] = review_input
            else:
                evidence["writingContext"] = (
                    await self._context.episode_writing_context(
                        project_id,
                        episode_number,
                        draft_revision_id=base_revision_id,
                        source_revision_refs=refs,
                    )
                )
        else:
            base_revision_id = str(descriptor.get("baseRevisionId") or "") or None
            if base_revision_id:
                evidence["baseCandidate"] = await self._context.revision(
                    base_revision_id,
                    text_limit=18_000,
                )
            if task["targetRole"] == "sourceAnalysis":
                evidence["sourceMaterial"] = await self._context.source_context(
                    project_id
                )
        return evidence

    async def _generate_draft_scene(self, task, unit, runtime, signal):
        unit_input = dict(unit.get("input") or {})
        episode_number = int(unit_input.get("episodeNumber") or 0)
        scene_id = str(unit_input.get("sceneId") or "").strip()
        evidence = await self._hydrate_evidence(task, unit)
        writing = dict(evidence.get("writingContext") or {})
        scene_plans = dict(writing.get("scenePlans") or {})
        if not scene_id or scene_id not in scene_plans:
            raise ValueError("draft scene is absent from the accepted scene Manifest")
        completed_scenes = _completed_part_outputs(
            task,
            kind="generate_draft_scene",
            episode_number=episode_number,
        )
        payload = {
            "task": "create_screenplay_scene",
            "episodeNumber": episode_number,
            "sceneId": scene_id,
            "scenePosition": list(unit_input.get("sceneIds") or ()).index(scene_id) + 1,
            "sceneCount": len(tuple(unit_input.get("sceneIds") or ())),
            "instruction": unit_input.get("instruction"),
            "constraints": unit_input.get("constraints") or [],
            "preserve": unit_input.get("preserve") or [],
            "baseRevisionId": unit_input.get("baseRevisionId"),
            "scenePlan": scene_plans[scene_id],
            "currentDraftScene": dict(
                writing.get("currentDraftScenes") or {}
            ).get(scene_id),
            "revisionIssues": [
                {
                    **dict(issue),
                    "directlyReferencesCurrentScene": (
                        scene_id in issue.get("relatedSceneIds", ())
                    ),
                }
                for issue in writing.get("reviewIssues") or ()
                if isinstance(issue, Mapping)
            ],
            "reviewRevisionId": writing.get("reviewRevisionId"),
            "acceptedGuidance": writing.get("acceptedGuidance"),
            "previousEpisodeContinuity": (
                _previous_episode_validation(task, episode_number)
                or writing.get("previousEpisodeContinuity")
            ),
            "completedScenes": [
                {"sceneId": str(output.get("sceneId") or "")}
                for output in completed_scenes
            ],
            "previousSceneTail": str(
                (completed_scenes[-1] if completed_scenes else {}).get("sceneText")
                or ""
            )[-1_200:],
        }
        if self._tool_calls is not None:
            result = await self._tool_calls.run_candidate(
                runtime=runtime,
                session_id=int(task["sessionId"]),
                prompt=str(unit_input.get("instruction") or "创作剧本场景"),
                system_instruction=_scene_tool_instruction(episode_number, scene_id),
                user_payload=payload,
                domain_context=await self._domain_context(
                    task,
                    unit,
                    expected_part_type="scene",
                    expected_part_key=scene_id,
                    runtime=runtime,
                ),
                conversation_turn_id=str(task["turnId"]),
                lineage=_child_lineage(task),
                reasoning_mode=ReasoningMode.DISABLED,
                host_candidate_template=_host_scene_candidate_template(
                    scene_id,
                    scene_plans[scene_id],
                ),
                candidate_validation_contract={
                    "protocol": SCREENPLAY_CANDIDATE_VALIDATION_PROTOCOL,
                    "kind": "scene",
                    "expectedSceneId": scene_id,
                },
                signal=signal,
            )
            candidate = result.candidate
            scene = dict(candidate["payload"])
            return {
                **scene,
                "episodeNumber": episode_number,
                "sceneListId": str(evidence["manifest"]["sceneListId"]),
                "runId": result.run_id,
                "artifactId": str(candidate["artifactId"]),
            }
        assert self._models is not None
        result = await self._models.run_json(
            runtime=runtime,
            session_id=int(task["sessionId"]),
            prompt=str(unit_input.get("instruction") or "创作剧本场景"),
            system_instruction=_scene_json_instruction(episode_number, scene_id),
            user_payload=payload,
            binding_namespace="screenplay.agent.task",
            binding_aggregate_id=str(task["projectId"]),
            binding_command_id=f"{task['id']}:{unit['id']}",
            conversation_turn_id=str(task["turnId"]),
            task_id=str(task["id"]),
            unit_id=str(unit["id"]),
            expected_part_key=scene_id,
            phase="screenplay_scene_generation",
            lineage=_child_lineage(task),
            validate=lambda value: _validate_scene_json(value, scene_id),
            signal=signal,
        )
        return {
            **result.value,
            "episodeNumber": episode_number,
            "sceneListId": str(evidence["manifest"]["sceneListId"]),
            "runId": result.run_id,
        }

    async def _generate_episode_metadata(self, task, unit, runtime, signal):
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
        user_payload = {
            "task": "finalize_screenplay_episode_metadata",
            "episodeNumber": episode_number,
            "scenes": [{"sceneId": scene["sceneId"]} for scene in scenes],
            "finalSceneTail": str(scenes[-1]["sceneText"])[-1_200:],
        }
        if self._tool_calls is not None:
            result = await self._tool_calls.run_candidate(
                runtime=runtime,
                session_id=int(task["sessionId"]),
                prompt=f"整理第 {episode_number} 集标题和连续性摘要",
                system_instruction=_episode_metadata_tool_instruction(episode_number),
                user_payload=user_payload,
                domain_context=await self._domain_context(
                    task,
                    unit,
                    expected_part_type="episode_metadata",
                    expected_part_key=str(episode_number),
                    runtime=runtime,
                ),
                conversation_turn_id=str(task["turnId"]),
                lineage=_child_lineage(task),
                reasoning_mode=ReasoningMode.DISABLED,
                candidate_validation_contract={
                    "protocol": SCREENPLAY_CANDIDATE_VALIDATION_PROTOCOL,
                    "kind": "episode_metadata",
                    "episodeNumber": episode_number,
                },
                signal=signal,
            )
            return {
                **dict(result.candidate["payload"]),
                "runId": result.run_id,
                "artifactId": str(result.candidate["artifactId"]),
            }
        assert self._models is not None
        result = await self._models.run_json(
            runtime=runtime,
            session_id=int(task["sessionId"]),
            prompt=f"整理第 {episode_number} 集标题和连续性摘要",
            system_instruction=_episode_metadata_json_instruction(episode_number),
            user_payload=user_payload,
            binding_namespace="screenplay.agent.task",
            binding_aggregate_id=str(task["projectId"]),
            binding_command_id=f"{task['id']}:{unit['id']}",
            conversation_turn_id=str(task["turnId"]),
            task_id=str(task["id"]),
            unit_id=str(unit["id"]),
            expected_part_key=str(episode_number),
            phase="screenplay_episode_metadata",
            lineage=_child_lineage(task),
            validate=lambda value: _validate_episode_metadata_json(
                value,
                episode_number,
            ),
            signal=signal,
        )
        return {**result.value, "runId": result.run_id}

    async def _generate_review_dimension(self, task, unit, runtime, signal):
        unit_input = dict(unit.get("input") or {})
        episode_number = int(unit_input.get("episodeNumber") or 0)
        dimension = str(unit_input.get("reviewDimension") or "")
        reviewed_draft_id = str(unit_input.get("reviewedDraftId") or "")
        evidence = await self._hydrate_evidence(task, unit)
        review_input = dict(evidence.get("reviewInput") or {})
        if not review_input:
            raise RuntimeError("review dimension has no immutable input packet")
        if self._tool_calls is None:
            raise RuntimeError("review dimension requires screenplay candidate tools")
        result = await self._tool_calls.run_candidate(
            runtime=runtime,
            session_id=int(task["sessionId"]),
            prompt=str(unit_input.get("instruction") or "审阅剧本"),
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
                "reviewInput": review_input,
            },
            domain_context=await self._domain_context(
                task,
                unit,
                expected_part_type="review_dimension",
                expected_part_key=f"{episode_number}:{dimension}",
                runtime=runtime,
            ),
            conversation_turn_id=str(task["turnId"]),
            lineage=_child_lineage(task),
            candidate_validation_contract={
                "protocol": SCREENPLAY_CANDIDATE_VALIDATION_PROTOCOL,
                "kind": "review_dimension",
                "episodeNumber": episode_number,
                "dimension": dimension,
                "allowedSceneIds": list(unit_input.get("sceneIds") or ()),
                "reviewedDraftId": reviewed_draft_id,
                "reviewedContentDigest": str(
                    review_input.get("contentDigest") or ""
                ),
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
            "artifactId": str(result.candidate["artifactId"]),
        }

    async def _generate_document_section(self, task, unit, runtime, signal):
        if self._tool_calls is None:
            raise RuntimeError("document section requires screenplay candidate tools")
        unit_input = dict(unit.get("input") or {})
        role = str(task["targetRole"])
        section_key = str(unit_input.get("sectionKey") or "")
        evidence = await self._hydrate_evidence(task, unit)
        episode_number = (
            int(section_key.removeprefix("episode-"))
            if role == "sceneList" and section_key.startswith("episode-")
            else 0
        )
        result = await self._tool_calls.run_candidate(
            runtime=runtime,
            session_id=int(task["sessionId"]),
            prompt=str(unit_input.get("instruction") or "生成剧本交付物章节"),
            system_instruction=(
                _scene_list_fragment_tool_instruction(episode_number)
                if episode_number
                else _document_section_tool_instruction(role, section_key)
            ),
            user_payload={
                "task": "create_screenplay_document_section",
                "targetRole": role,
                "sectionKey": section_key,
                "episodeNumber": episode_number or None,
                "instruction": unit_input.get("instruction"),
                "constraints": unit_input.get("constraints") or [],
                "preserve": unit_input.get("preserve") or [],
                "evidence": evidence,
            },
            domain_context=await self._domain_context(
                task,
                unit,
                expected_part_type="document_section",
                expected_part_key=section_key,
                runtime=runtime,
            ),
            conversation_turn_id=str(task["turnId"]),
            lineage=_child_lineage(task),
            candidate_validation_contract=(
                {
                    "protocol": SCREENPLAY_CANDIDATE_VALIDATION_PROTOCOL,
                    "kind": "scene_list_fragment",
                    "episodeNumber": episode_number,
                }
                if episode_number
                else {
                    "protocol": SCREENPLAY_CANDIDATE_VALIDATION_PROTOCOL,
                    "kind": "document_section",
                    "sectionKey": section_key,
                }
            ),
            signal=signal,
        )
        return {
            **dict(result.candidate["payload"]),
            "contentText": str(result.candidate.get("contentText") or ""),
            "sectionKey": section_key,
            "runId": result.run_id,
            "artifactId": str(result.candidate["artifactId"]),
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
        result = await self._models.run_public_text(
            runtime=runtime,
            session_id=int(task["sessionId"]),
            prompt=payload["request"] or payload["instruction"],
            system_instruction=_final_response_instruction(),
            user_payload=payload,
            binding_namespace="screenplay.agent.task",
            binding_aggregate_id=str(task["projectId"]),
            binding_command_id=f"{task['id']}:{unit['id']}",
            task_id=str(task["id"]),
            unit_id=str(unit["id"]),
            expected_part_key="final_response",
            phase="screenplay_final_response_composition",
            conversation_turn_id=str(task["turnId"]),
            lineage=_child_lineage(task),
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
        unit_id: str | None = None,
    ) -> ScreenplayAgentDomainContext:
        project = await self._db.fetch_one(
            "SELECT source_book_id, source_scope_json "
            "FROM screenplay_projects WHERE id = ?",
            [str(task["projectId"])],
        )
        if project is None:
            raise AppError("剧本项目不存在", 404)
        return ScreenplayAgentDomainContext(
            project_id=str(task["projectId"]),
            task_id=str(task["id"]),
            unit_id=str(unit_id or unit["id"]),
            target_role=str(task["targetRole"]),
            expected_part_type=expected_part_type,
            expected_part_key=expected_part_key,
            source_book_id=str(project.get("source_book_id") or "") or None,
            source_scope=parse_source_scope(project.get("source_scope_json")),
            locale=str(getattr(runtime, "locale", "zh-CN") or "zh-CN"),
            tool_access=(
                "candidate_write"
                if str(unit.get("kind") or "") in {
                    "generate_draft_scene",
                    "generate_episode_metadata",
                    "generate_review_dimension",
                    "generate_document_section",
                }
                else "all"
            ),
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
        self._runtime = runtime
        self._delegate = ScreenplayTaskModelCalls(
            db,
            composition=composition,
            tool_calling_service=tool_calling_service,
        )
        self._parts = ScreenplayPartArtifactQuery(db)

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
        output = dict(await self._delegate.execute(
            task=task,
            unit=unit,
            runtime=self._runtime,
            signal=signal,
        ))
        artifact_id = str(output.get("artifactId") or "").strip()
        run_id = str(output.get("runId") or "").strip()
        semantic_key = str(context.unit.semantic_key or context.unit.id)
        if artifact_id and run_id:
            ref = await self._parts.validated_ref(
                artifact_id=artifact_id,
                run_id=run_id,
                semantic_key=semantic_key,
            )
        else:
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
        return classify_screenplay_run_failure(error)

    async def _task_view(
        self,
        context: DurableUnitExecutionContext,
    ) -> dict[str, Any]:
        metadata = thaw_json_mapping(context.task.metadata)
        recipe = metadata.get("recipe") or {}
        outputs = await self._parts.list_task_outputs(context.task.id)
        units = []
        for raw in recipe.get("steps") or ():
            unit = dict(raw)
            unit_id = str(unit.get("id") or "")
            units.append({
                "id": unit_id,
                "kind": str(unit.get("kind") or ""),
                "input": dict(unit.get("input") or {}),
                "dependsOn": list(unit.get("dependsOn") or ()),
                "status": "completed" if unit_id in outputs else "pending",
                "output": outputs.get(unit_id, {}),
            })
        return {
            "id": context.task.id,
            "projectId": str(metadata["projectId"]),
            "sessionId": int(metadata["sessionId"]),
            "turnId": str(metadata["turnId"]),
            "rootRunId": context.task.created_by_run_id,
            "targetRole": str(metadata["targetRole"]),
            "sourceRevisionRefs": list(metadata.get("sourceRevisionRefs") or ()),
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


def _child_lineage(task: Mapping[str, Any]) -> RunLineage:
    root_run_id = str(task.get("rootRunId") or "").strip()
    if not root_run_id:
        raise RuntimeError("screenplay AI Part requires its Root Run identity")
    return RunLineage(
        parent_run_id=root_run_id,
        root_run_id=root_run_id,
        delegation_id=None,
        agent_role="screenplay-part",
        depth=1,
    )


def _requires_child_run(kind: str) -> bool:
    return str(kind or "").strip() in SCREENPLAY_AI_PART_KINDS


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


def _previous_episode_validation(
    task: Mapping[str, Any],
    episode_number: int,
) -> dict[str, Any] | None:
    candidates = []
    for unit in task.get("units") or ():
        if not isinstance(unit, Mapping) or unit.get("status") != "completed":
            continue
        unit_input = unit.get("input")
        output = unit.get("output")
        if (
            str(unit.get("kind") or "") != "validate_manifest_part"
            or not isinstance(unit_input, Mapping)
            or unit_input.get("validationKind") != "draft_episode"
            or not isinstance(output, Mapping)
        ):
            continue
        number = int(unit_input.get("episodeNumber") or 0)
        if 0 < number < episode_number:
            draft = output.get("episodeDraft")
            if isinstance(draft, Mapping):
                candidates.append((number, {
                    "episodeNumber": number,
                    "title": str(draft.get("title") or ""),
                    "continuitySummary": str(
                        draft.get("continuitySummary") or ""
                    )[:2_000],
                    "finalSceneTail": str(draft.get("contentText") or "")[-1_200:],
                }))
    return max(candidates, default=(0, None), key=lambda item: item[0])[1]


def _scene_tool_instruction(episode_number: int, scene_id: str) -> str:
    return f"""你是专业剧本编剧，只创作第 {episode_number} 集中的场景 {scene_id}。
当前任务、场景计划、对应旧稿、审阅问题和已采纳创作依据均已由宿主完整提供。不得扩大到其他场景或重新规划任务。
最终回复只输出当前场景的完整可拍摄剧本文本。不得输出 JSON、Markdown 代码块、过程说明、整集或其他场景。场景身份和写入由宿主负责。"""


def _host_scene_candidate_template(
    scene_id: str,
    scene_plan: Mapping[str, Any],
) -> dict[str, Any]:
    del scene_plan
    return {"sceneId": scene_id}


def _scene_json_instruction(episode_number: int, scene_id: str) -> str:
    return f"""只创作第 {episode_number} 集场景 {scene_id}，只输出 JSON：
{{"sceneId":"{scene_id}","sceneText":"完整场景正文"}}
不得生成其他场景，sceneId 必须保持不变。"""


def _validate_scene_json(
    value: dict[str, Any],
    expected_scene_id: str,
) -> dict[str, Any]:
    scene_id = str(value.get("sceneId") or "").strip()
    scene_text = str(value.get("sceneText") or "").strip()
    if scene_id != expected_scene_id or not scene_text:
        raise ValueError("scene output does not match the requested Manifest Part")
    return {
        "sceneId": scene_id,
        "sceneText": scene_text,
    }


def _episode_metadata_tool_instruction(episode_number: int) -> str:
    return f"""你只负责整理第 {episode_number} 集的短元数据，不生成或复述剧本正文。
完成后必须且只能调用一次 writeScreenplayCandidatePart。candidate 必须严格为：
{{"episodeNumber":{episode_number},"title":"简洁集标题","continuitySummary":"供下一集续写的连续性摘要"}}
写入成功后只回复一句简短确认。"""


def _episode_metadata_json_instruction(episode_number: int) -> str:
    return f"""只整理第 {episode_number} 集元数据，只输出 JSON：
{{"episodeNumber":{episode_number},"title":"简洁集标题","continuitySummary":"连续性摘要"}}
不得输出或复述剧本正文。"""


def _validate_episode_metadata_json(
    value: dict[str, Any],
    episode_number: int,
) -> dict[str, Any]:
    if int(value.get("episodeNumber") or 0) != episode_number:
        raise ValueError("episode metadata number does not match")
    title = str(value.get("title") or "").strip()
    continuity = str(value.get("continuitySummary") or "").strip()
    if not title or not continuity:
        raise ValueError("episode metadata title and continuity are required")
    return {
        "episodeNumber": episode_number,
        "title": title,
        "continuitySummary": continuity,
    }


def _review_dimension_tool_instruction(
    episode_number: int,
    dimension: str,
    scene_ids: Sequence[str],
) -> str:
    return f"""你是剧本审阅 Agent，只审阅第 {episode_number} 集的 {dimension} 维度。
宿主已在 reviewInput 中完整提供指定不可变版本的本集正文、场景计划和必要上下文。只能依据这些材料审阅，不得另行检索、声称材料不可读或把系统错误写成审阅意见。
完成后必须且只能调用一次 writeScreenplayCandidatePart。candidate 必须为：
{{"episodeNumber":{episode_number},"reviewDimension":"{dimension}","title":"第 {episode_number} 集 {dimension} 审阅","contentText":"当前维度的 Markdown 审阅意见","contentJson":{{"verdict":"ready|revise|major_rework","issues":[{{"id":"维度内唯一 ID","severity":"critical|major|minor","description":"具体问题与修改方向","sceneIds":["场景ID"]}}]}}}}
问题只能引用这些场景 ID：{list(scene_ids)}。没有问题时 issues=[] 且 verdict=ready。"""


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
        int(raw.get("episodeNumber") or 0) != episode_number
        or str(raw.get("reviewDimension") or "") != dimension
    ):
        raise ValueError("review dimension candidate identity does not match")
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


def _document_section_tool_instruction(role: str, section_key: str) -> str:
    return f"""你只生成 {role} 文档中的 {section_key} 章节。
宿主已提供本章节需要的项目证据。完成后必须且只能调用一次 writeScreenplayCandidatePart。candidate 必须为：
{{"sectionKey":"{section_key}","title":"章节标题","contentText":"当前章节的 Markdown 正文","contentJson":{{"当前章节对应的结构化字段":"值"}}}}
contentJson 必须是可与同一文档其他章节确定性合并的顶层片段；不得输出其他章节或完整文档。"""


def _scene_list_fragment_tool_instruction(episode_number: int) -> str:
    return f"""你只规划已采纳结构中的第 {episode_number} 集场景。
完成后必须且只能调用一次 writeScreenplayCandidatePart。candidate 必须为：
{{"sectionKey":"episode-{episode_number}","title":"第 {episode_number} 集场景表","contentText":"当前集场景表的 Markdown 文档","contentJson":{{"scenes":[{{"id":"全局唯一场景 ID","episodeNumber":{episode_number},"heading":"内外景·地点·时间","objective":"目标","conflict":"冲突","turn":"转折","synopsis":"场景梗概"}}]}}}}
只提交当前集，场景顺序必须可直接用于后续剧本创作。"""


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


def _review_episode_input(
    *,
    reviewed_draft_id: str,
    episode_number: int,
    episode_context: Mapping[str, Any],
) -> dict[str, Any]:
    current_draft = dict(episode_context.get("currentDraft") or {})
    scene_plan = dict(episode_context.get("episode") or {})
    scene_ids = _review_scene_ids(current_draft, scene_plan)
    packet = {
        "contractVersion": 2,
        "draftRevisionId": reviewed_draft_id,
        "episodeNumber": episode_number,
        "sceneIds": list(scene_ids),
        "draftContentText": _review_draft_text(current_draft),
        "scenePlan": scene_plan,
        "requiredContext": {
            "previousEpisode": episode_context.get("previousEpisode"),
        },
    }
    return {**packet, "contentDigest": _canonical_digest(packet)}


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


def _review_scene_ids(
    current_draft: Mapping[str, Any],
    scene_plan: Mapping[str, Any],
) -> tuple[str, ...]:
    direct = current_draft.get("sceneIds")
    draft_ids = (
        tuple(str(value or "").strip() for value in direct)
        if isinstance(direct, list)
        else tuple(
            str(item.get("sceneId") or item.get("id") or "").strip()
            for item in current_draft.get("sceneTexts") or ()
            if isinstance(item, Mapping)
        )
    )
    plan_ids = tuple(
        str(item.get("id") or "").strip()
        for item in scene_plan.get("scenes") or ()
        if isinstance(item, Mapping)
    )
    if not draft_ids or any(not value for value in draft_ids) or draft_ids != plan_ids:
        raise ValueError("review episode scene identity is incomplete")
    return draft_ids


def _review_draft_text(current_draft: Mapping[str, Any]) -> str:
    text = "\n\n".join(
        str(item.get("contentText") or "").strip()
        for item in current_draft.get("sceneTexts") or ()
        if isinstance(item, Mapping) and str(item.get("contentText") or "").strip()
    ) or str(current_draft.get("contentText") or "").strip()
    if not text:
        raise ValueError("review episode draft text is empty")
    return text


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
    legacy_evidence = evidence_output.get("evidence")
    evidence_scene_list_id = (
        str(descriptor.get("sceneListRevisionId") or "")
        if isinstance(descriptor, Mapping)
        else str((legacy_evidence or {}).get("manifest", {}).get("sceneListId") or "")
        if isinstance(legacy_evidence, Mapping)
        else ""
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
    review_ref = (
        descriptor.get("reviewInputRef")
        if isinstance(descriptor, Mapping)
        else None
    )
    legacy_evidence = evidence_output.get("evidence")
    legacy_input = (
        legacy_evidence.get("reviewInput")
        if isinstance(legacy_evidence, Mapping)
        else None
    )
    reviewed_draft_id = str(
        (review_ref or {}).get("reviewedRevisionId")
        or (legacy_input or {}).get("draftRevisionId")
        or unit_input.get("reviewedDraftId")
        or ""
    )
    reviewed_digest = str(
        (review_ref or {}).get("contentDigest")
        or (legacy_input or {}).get("contentDigest")
        or ""
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
    unit_input = dict(unit.get("input") or {})
    role = str(task["targetRole"])
    sections = _completed_part_outputs(task, kind="generate_document_section")
    if not sections:
        raise ValueError("document validation requires section Parts")
    expected_keys = tuple(
        str(item.get("input", {}).get("sectionKey") or "")
        for item in task.get("units") or ()
        if isinstance(item, Mapping)
        and str(item.get("kind") or "") == "generate_document_section"
    )
    if tuple(str(section.get("sectionKey") or "") for section in sections) != expected_keys:
        raise ValueError("document section Parts are incomplete or out of order")
    merged: dict[str, Any] = {}
    for section in sections:
        merged = _merge_document_json(merged, dict(section.get("contentJson") or {}))
    evidence_output = _dependency_output(task, unit, "collect_evidence")
    descriptor = evidence_output.get("evidenceDescriptor")
    evidence = dict(evidence_output.get("evidence") or {})
    heads = {
        str(item.get("role") or ""): item
        for item in evidence.get("acceptedDeliverables") or ()
        if isinstance(item, Mapping)
    }
    structure = heads.get("structure") or {}
    structure_numbers = (
        tuple(int(value) for value in descriptor.get("structureEpisodeNumbers") or ())
        if isinstance(descriptor, Mapping)
        else tuple(
            int(item.get("number") or 0)
            for item in structure.get("content", {}).get("episodes", [])
            if isinstance(item, Mapping)
        )
    )
    structure_revision_id = (
        str(descriptor.get("structureRevisionId") or "") or None
        if isinstance(descriptor, Mapping)
        else str(structure.get("revisionId") or "") or None
    )
    draft = heads.get("screenplayDraft") or {}
    reviewed_draft_id = (
        str(descriptor.get("currentDraftRevisionId") or "") or None
        if isinstance(descriptor, Mapping)
        else str(draft.get("revisionId") or "") or None
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
        "sourceRunIds": _source_run_ids(sections),
        "runId": str(sections[-1].get("runId") or "") or None,
    })


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
使用 2 至 4 句自然语言，先准确说明完成了哪些候选内容，再说明用户可以在候选稿区域查看和继续编辑。
只能依据输入中的公开事实，不得声称候选稿已经采纳，不得编造版本号、链接或未提供的结果。
不得复述剧本正文，不得输出工具过程、内部协议、推理过程或固定套话。"""


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
    else:
        fact = {"title": str(output.get("title") or "").strip()}
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
    value = dict(candidate.get("payload") or {})
    if int(value.get("episodeNumber") or 0) != episode_number:
        raise ValueError("episode metadata number does not match")
    title = str(value.get("title") or "").strip()
    continuity = str(value.get("continuitySummary") or "").strip()
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
    if role == "creativeBrief":
        fields = normalized.get("fields")
        if not isinstance(fields, Mapping):
            raise ValueError("creative brief fields are required")
        if (
            not str(fields.get("approach") or "").strip()
            or not str(fields.get("premise") or "").strip()
        ):
            raise ValueError("creative brief approach and premise are required")
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
    value = dict(candidate.get("payload") or {})
    title = str(value.get("title") or "").strip()
    text = str(candidate.get("contentText") or "").strip()
    content = value.get("contentJson")
    if not title or not text or not isinstance(content, Mapping):
        raise ValueError("scene list fragment is incomplete")
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
    episodes = content.get("episodes")
    if not isinstance(episodes, list) or not episodes:
        raise ValueError("structure episodes are required")
    numbers = [int(item.get("number") or 0) for item in episodes if isinstance(item, Mapping)]
    ids = [str(item.get("id") or "").strip() for item in episodes if isinstance(item, Mapping)]
    if len(numbers) != len(episodes) or any(number <= 0 for number in numbers):
        raise ValueError("structure episode numbers are invalid")
    if len(numbers) != len(set(numbers)) or len(ids) != len(set(ids)) or any(not item for item in ids):
        raise ValueError("structure episode identity is invalid")
    if any(
        not str(item.get("title") or "").strip()
        or not str(item.get("summary") or "").strip()
        for item in episodes
        if isinstance(item, Mapping)
    ):
        raise ValueError("structure episode title and summary are required")


def _validate_scene_list(
    content: Mapping[str, Any],
    structure_episode_numbers: Sequence[int],
) -> None:
    scenes = content.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        raise ValueError("scene list scenes are required")
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
        candidates = values if isinstance(values, list) else (output.get("runId"),)
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
    if kind == "scene_list_fragment":
        episode_number = int(value.get("episodeNumber") or 0)
        return _validate_scene_list_fragment_candidate(
            normalized_candidate,
            episode_number,
        )
    raise ValueError("candidate validation kind is unsupported")


__all__ = [
    "ScreenplayTaskModelCalls",
    "ScreenplayTaskUnitExecutor",
    "normalize_screenplay_candidate",
]
