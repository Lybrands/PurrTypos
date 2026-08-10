"""Execute screenplay business units while PurrA owns orchestration."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from purra.model_execution import ManagedModelExecutor
from purra.errors import ModelGatewayError
from purra.json_values import thaw_json_mapping
from purra.long_tasks import DurableUnitExecutionContext, LongTaskUnitResult
from application.output_budget_policies import (
    SCREENPLAY_DELIVERABLE_OUTPUT_POLICY,
    SCREENPLAY_EPISODE_OUTPUT_POLICY,
    SCREENPLAY_FINAL_RESPONSE_OUTPUT_POLICY,
    SCREENPLAY_REVIEW_OUTPUT_POLICY,
)
from application.screenplay_agent_context import ScreenplayAgentContextQuery
from application.screenplay_incremental_generation import (
    ScreenplayIncrementalGeneration,
)
from domains.screenplay_agent.recovery import classify_screenplay_run_failure
from application.screenplay_structured_call import ScreenplayStructuredCallService
from application.screenplay_tool_calling import ScreenplayToolCallingService
from domains.screenplay.source_scope import parse_source_scope
from domains.screenplay_agent.agent_context import ScreenplayAgentDomainContext
from exceptions import AppError
from infrastructure.persistence.sqlite_screenplay_v2_repository import (
    SqliteScreenplayV2Repository,
)
from infrastructure.persistence.sqlite_screenplay_task_output_store import (
    SqliteScreenplayTaskOutputStore,
)


_PROPOSAL_KIND = {
    "sourceAnalysis": "source_analysis",
    "creativeBrief": "creative_brief",
    "structure": "episode_outline",
    "sceneList": "scene_list",
    "screenplayDraft": "scene_draft",
    "review": "review",
}
_DOCUMENT_KIND = dict(_PROPOSAL_KIND)
_ROLE_LABELS = {
    "sourceAnalysis": "原作分析",
    "creativeBrief": "创作简报",
    "structure": "分集结构",
    "sceneList": "场景表",
    "screenplayDraft": "剧本正文",
    "review": "审阅报告",
}


class ScreenplayTaskModelCalls:
    def __init__(
        self,
        db,
        *,
        model_executor_factory: Callable[[str], ManagedModelExecutor] | None = None,
        tool_calling_service: ScreenplayToolCallingService | None = None,
    ) -> None:
        self._db = db
        self._context = ScreenplayAgentContextQuery(db)
        self._models = (
            ScreenplayStructuredCallService(
                db,
                model_executor_factory=model_executor_factory,
            )
            if model_executor_factory is not None
            else None
        )
        self._tool_calls = tool_calling_service
        if self._models is None and self._tool_calls is None:
            raise ValueError("screenplay task requires a model execution service")
        self._revisions = SqliteScreenplayV2Repository(db)
        self._outputs = SqliteScreenplayTaskOutputStore(db)

    async def execute(
        self,
        *,
        task: Mapping[str, Any],
        unit: Mapping[str, Any],
        runtime,
        signal=None,
    ) -> Mapping[str, Any]:
        kind = str(unit.get("kind") or "")
        if kind == "collect_evidence":
            return await self._collect_evidence(task, unit)
        if kind == "generate_candidate":
            return await self._generate_candidate(
                task, unit, runtime, signal
            )
        if kind == "validate_candidate":
            return self._validate_candidate(task, unit)
        if kind == "compose_final_response":
            return await self._compose_final_response(
                task, unit, runtime, signal
            )
        if kind == "generate_episode_draft":
            return await self._generate_episode(task, unit, runtime, signal)
        if kind == "generate_deliverable":
            return await self._generate_deliverable(task, unit, runtime, signal)
        if kind == "publish_candidate_revision":
            return await self._publish(task)
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
        heads = await self._context.heads(project_id, text_limit=18_000)
        evidence: dict[str, Any] = {
            "projectId": project_id,
            "targetRole": role,
            "acceptedDeliverables": heads,
        }
        if episode_number:
            manifest = await self._context.episode_manifest(
                project_id,
                episode_number,
            )
            evidence.update({
                "episodeNumber": episode_number,
                "manifest": manifest,
                "episodeContext": await self._context.episode_context(
                    project_id,
                    episode_number,
                    draft_revision_id=base_revision_id,
                ),
                "writingContext": await self._context.episode_writing_context(
                    project_id,
                    episode_number,
                    draft_revision_id=base_revision_id,
                ),
            })
        else:
            if base_revision_id:
                evidence["baseCandidate"] = await self._context.revision(
                    base_revision_id,
                    text_limit=18_000,
                )
            if role == "sourceAnalysis":
                evidence["sourceMaterial"] = await self._context.source_context(
                    project_id
                )
        encoded = json.dumps(
            evidence,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        return {
            "evidence": evidence,
            "evidenceReceipt": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        }

    async def _generate_candidate(self, task, unit, runtime, signal):
        evidence_output = _dependency_output(task, unit, "collect_evidence")
        evidence = dict(evidence_output.get("evidence") or {})
        if not evidence:
            raise RuntimeError("screenplay evidence checkpoint is missing")
        if int((unit.get("input") or {}).get("episodeNumber") or 0):
            return await self._generate_episode(
                task,
                unit,
                runtime,
                signal,
                evidence=evidence,
            )
        return await self._generate_deliverable(
            task,
            unit,
            runtime,
            signal,
            evidence=evidence,
        )

    def _validate_candidate(self, task, unit) -> dict[str, Any]:
        generated = dict(_dependency_output(task, unit, "generate_candidate"))
        if not generated:
            raise RuntimeError("screenplay candidate checkpoint is missing")
        role = str(task["targetRole"])
        if role == "screenplayDraft":
            draft = generated.get("episodeDraft")
            if not isinstance(draft, Mapping):
                raise ValueError("screenplay episode candidate is missing")
            if not str(generated.get("sceneListId") or "").strip():
                raise ValueError("screenplay episode scene list is missing")
            if not str(draft.get("contentText") or "").strip():
                raise ValueError("screenplay episode text is empty")
            if not tuple(draft.get("sceneIds") or ()):
                raise ValueError("screenplay episode scenes are empty")
        else:
            evidence = dict(
                _dependency_output(task, unit, "collect_evidence").get(
                    "evidence"
                ) or {}
            )
            heads = {
                str(item.get("role") or ""): item
                for item in evidence.get("acceptedDeliverables") or ()
                if isinstance(item, Mapping)
            }
            structure = heads.get("structure")
            structure_numbers = tuple(
                int(item.get("number") or 0)
                for item in (
                    (structure or {}).get("content", {}).get("episodes", [])
                )
                if isinstance(item, Mapping)
            )
            draft = heads.get("screenplayDraft")
            normalized = _validate_deliverable(
                role,
                generated,
                structure_id=(structure or {}).get("revisionId"),
                structure_episode_numbers=structure_numbers,
                reviewed_draft_id=(draft or {}).get("revisionId"),
            )
            generated.update(normalized)
        digest_source = json.dumps(
            generated,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        return {
            **generated,
            "validationReceipt": hashlib.sha256(
                digest_source.encode("utf-8")
            ).hexdigest(),
        }

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
            if str(item.get("kind") or "") == "validate_candidate"
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
        result = await self._models.run_json(
            runtime=runtime,
            session_id=int(task["sessionId"]),
            prompt=payload["request"] or payload["instruction"],
            system_instruction=_final_response_instruction(),
            user_payload=payload,
            binding_namespace="screenplay.agent.task",
            binding_aggregate_id=str(task["projectId"]),
            binding_command_id=f"{task['id']}:{unit['id']}",
            conversation_turn_id=str(task["turnId"]),
            task_id=str(task["id"]),
            phase="screenplay_final_response_composition",
            output_policy=SCREENPLAY_FINAL_RESPONSE_OUTPUT_POLICY,
            repair_instruction=(
                "只返回包含非空 finalResponse 的 JSON 对象；"
                "答复保持简短，不得加入候选正文或内部字段。"
            ),
            validate=_validate_final_response,
            signal=signal,
        )
        return {
            "finalResponse": result.value["finalResponse"],
            "runId": result.run_id,
        }

    async def _generate_episode(
        self,
        task,
        unit,
        runtime,
        signal,
        evidence: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        project_id = str(task["projectId"])
        episode_number = int(unit["input"]["episodeNumber"])
        base_revision_id = str(unit["input"].get("baseRevisionId") or "") or None
        manifest = (
            dict(evidence.get("manifest") or {})
            if evidence is not None
            else await self._context.episode_manifest(project_id, episode_number)
        )
        scene_ids = tuple(manifest["sceneIds"])
        if self._tool_calls is not None:
            return await self._generate_episode_incrementally(
                task=task,
                unit=unit,
                runtime=runtime,
                signal=signal,
                episode_number=episode_number,
                base_revision_id=base_revision_id,
                scene_ids=scene_ids,
                scene_list_id=str(manifest["sceneListId"]),
                writing_context=(
                    dict(evidence.get("writingContext") or {})
                    if evidence is not None else None
                ),
            )
        context = (
            dict(evidence.get("episodeContext") or {})
            if evidence is not None
            else await self._context.episode_context(
                project_id,
                episode_number,
                draft_revision_id=base_revision_id,
            )
        )
        heads = (
            list(evidence.get("acceptedDeliverables") or ())
            if evidence is not None
            else await self._context.heads(
                project_id,
                roles=("creativeBrief", "structure"),
                text_limit=8_000,
            )
        )
        previous = _last_generated_episode(task.get("units") or (), episode_number)
        payload = {
            "projectId": project_id,
            "episodeNumber": episode_number,
            "instruction": unit["input"].get("instruction"),
            "constraints": unit["input"].get("constraints") or [],
            "preserve": unit["input"].get("preserve") or [],
            "acceptedGuidance": heads,
            "scenePlan": context["episode"],
            "continuity": previous or context.get("previousEpisode"),
            "currentDraft": context.get("currentDraft"),
        }
        assert self._models is not None
        result = await self._models.run_json(
            runtime=runtime,
            session_id=int(task["sessionId"]),
            prompt=str(unit["input"].get("instruction") or "创作剧本正文"),
            system_instruction=_episode_instruction(episode_number, scene_ids),
            user_payload=payload,
            binding_namespace="screenplay.agent.task",
            binding_aggregate_id=project_id,
            binding_command_id=f"{task['id']}:{unit['id']}",
            conversation_turn_id=str(task["turnId"]),
            task_id=str(task["id"]),
            phase="screenplay_episode_generation",
            output_policy=SCREENPLAY_EPISODE_OUTPUT_POLICY,
            work_units=len(scene_ids),
            repair_instruction="严格按指定 JSON 协议重写；集数和 sceneId 必须与场景表完全一致，每场 processSummary 和 sceneText 都不能为空。",
            validate=lambda value: _validate_episode(value, episode_number, scene_ids),
            execution_progress_fields={
                "executionSummary": "",
                "processSummary": "",
            },
            project_execution=lambda value: (
                value["executionSummary"],
            ),
            signal=signal,
        )
        episode = result.value
        scene_texts = [{
            "sceneId": str(scene["sceneId"]),
            "contentText": str(scene["sceneText"]),
        } for scene in episode["scenes"]]
        content_text = "\n\n".join(item["contentText"] for item in scene_texts)
        return {
            "runId": result.run_id,
            "executionSummary": episode["executionSummary"],
            "sceneListId": context["sceneListId"],
            "episodeDraft": {
                "episodeNumber": episode_number,
                "title": episode["title"],
                "sceneIds": list(scene_ids),
                "sceneTexts": scene_texts,
                "sceneExecutions": [{
                    "sceneId": scene_id,
                    "status": "completed",
                } for scene_id in scene_ids],
                "contentText": content_text,
                "continuitySummary": episode["continuitySummary"],
            },
        }

    def _incremental(self) -> ScreenplayIncrementalGeneration:
        if self._tool_calls is None:
            raise RuntimeError("incremental generation requires screenplay tools")
        return ScreenplayIncrementalGeneration(
            context=self._context,
            outputs=self._outputs,
            tool_calls=self._tool_calls,
            domain_context_factory=self._domain_context,
        )

    async def _generate_episode_incrementally(
        self,
        **kwargs,
    ) -> dict[str, Any]:
        return await self._incremental().generate_episode(
            **kwargs,
            validate_scene=_validate_scene_candidate,
            validate_metadata=_validate_episode_metadata_candidate,
        )

    async def _generate_review_incrementally(
        self,
        **kwargs,
    ) -> dict[str, Any]:
        return await self._incremental().generate_review(
            **kwargs,
            draft_scene_ids=_draft_scene_ids,
            validate_fragment=lambda candidate, number, scene_ids, draft_id, digest: (
                _validate_review_fragment_candidate(
                    candidate,
                    episode_number=number,
                    allowed_scene_ids=scene_ids,
                    reviewed_draft_id=draft_id,
                    reviewed_content_digest=digest,
                )
            ),
            aggregate_verdict=_aggregate_review_verdict,
            document_kind=_DOCUMENT_KIND["review"],
            classify_failure=classify_screenplay_run_failure,
        )

    async def _generate_scene_list_incrementally(
        self,
        **kwargs,
    ) -> dict[str, Any]:
        return await self._incremental().generate_scene_list(
            **kwargs,
            validate_fragment=_validate_scene_list_fragment_candidate,
            finalize_document=lambda scenes, text, structure_id, numbers: (
                _validate_deliverable(
                    "sceneList",
                    {
                        "title": "分集场景表",
                        "executionSummary": (
                            f"已按 {len(numbers)} 集分别规划场景并完成全量覆盖校验。"
                        ),
                        "contentText": text,
                        "contentJson": {"scenes": scenes},
                    },
                    structure_id=structure_id,
                    structure_episode_numbers=numbers,
                    reviewed_draft_id=None,
                )
            ),
        )

    async def _generate_deliverable(
        self,
        task,
        unit,
        runtime,
        signal,
        evidence: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        project_id = str(task["projectId"])
        role = str(task["targetRole"])
        base_revision_id = str(unit["input"].get("baseRevisionId") or "") or None
        heads = (
            list(evidence.get("acceptedDeliverables") or ())
            if evidence is not None
            else await self._context.heads(
                project_id,
                roles=("structure", "screenplayDraft", "review"),
                text_limit=0,
            )
        )
        heads_by_role = {head["role"]: head for head in heads}
        structure = heads_by_role.get("structure")
        structure_id = structure["revisionId"] if structure else None
        structure_episodes = (
            structure["content"].get("episodes", [])
            if structure
            else []
        )
        structure_episode_numbers = tuple(
            int(episode.get("number") or 0)
            for episode in structure_episodes
            if isinstance(episode, Mapping)
        )
        draft = heads_by_role.get("screenplayDraft")
        reviewed_draft_id = draft["revisionId"] if draft else None
        previous_review = heads_by_role.get("review")
        previous_review_content = (
            previous_review.get("content")
            if isinstance(previous_review, Mapping)
            and isinstance(previous_review.get("content"), Mapping)
            else None
        )
        if self._tool_calls is not None:
            if role == "review":
                return await self._generate_review_incrementally(
                    task=task,
                    unit=unit,
                    runtime=runtime,
                    signal=signal,
                    reviewed_draft_id=reviewed_draft_id,
                    previous_review=previous_review_content,
                )
            if role == "sceneList":
                return await self._generate_scene_list_incrementally(
                    task=task,
                    unit=unit,
                    runtime=runtime,
                    signal=signal,
                    structure_id=structure_id,
                    episode_numbers=structure_episode_numbers,
                )
            result = await self._tool_calls.run_candidate(
                runtime=runtime,
                session_id=int(task["sessionId"]),
                prompt=str(unit["input"].get("instruction") or "生成剧本交付物"),
                system_instruction=_deliverable_tool_instruction(role),
                user_payload={
                    "task": "create_screenplay_deliverable",
                    "targetRole": role,
                    "instruction": unit["input"].get("instruction"),
                    "constraints": unit["input"].get("constraints") or [],
                    "preserve": unit["input"].get("preserve") or [],
                    "baseRevisionId": base_revision_id,
                    **({"evidence": dict(evidence)} if evidence else {}),
                },
                domain_context=await self._domain_context(
                    task,
                    unit,
                    expected_part_type="document",
                    expected_part_key="main",
                    runtime=runtime,
                ),
                conversation_turn_id=str(task["turnId"]),
                output_policy=(
                    SCREENPLAY_REVIEW_OUTPUT_POLICY
                    if role == "review"
                    else SCREENPLAY_DELIVERABLE_OUTPUT_POLICY
                ),
                validate_candidate=(
                    None
                    if str(unit.get("kind") or "") == "generate_candidate"
                    else lambda candidate: _validate_deliverable_candidate(
                        role,
                        candidate,
                        structure_id=structure_id,
                        structure_episode_numbers=structure_episode_numbers,
                        reviewed_draft_id=reviewed_draft_id,
                    )
                ),
                signal=signal,
            )
            return {
                **dict(result.candidate["payload"]),
                "contentText": str(result.candidate["contentText"]),
                "runId": result.run_id,
                "artifactId": result.candidate["artifactId"],
            }
        heads = (
            list(evidence.get("acceptedDeliverables") or ())
            if evidence is not None
            else await self._context.heads(project_id, text_limit=18_000)
        )
        base_candidate = (
            evidence.get("baseCandidate")
            if evidence is not None
            else await self._context.revision(base_revision_id, text_limit=18_000)
            if base_revision_id
            else None
        )
        source = (
            evidence.get("sourceMaterial")
            if evidence is not None
            else await self._context.source_context(project_id)
            if role == "sourceAnalysis"
            else None
        )
        assert self._models is not None
        result = await self._models.run_json(
            runtime=runtime,
            session_id=int(task["sessionId"]),
            prompt=str(unit["input"].get("instruction") or "生成剧本交付物"),
            system_instruction=_deliverable_instruction(role),
            user_payload={
                "projectId": project_id,
                "targetRole": role,
                "instruction": unit["input"].get("instruction"),
                "constraints": unit["input"].get("constraints") or [],
                "preserve": unit["input"].get("preserve") or [],
                "acceptedDeliverables": heads,
                **({"baseCandidate": base_candidate} if base_candidate else {}),
                **({"sourceMaterial": source} if source else {}),
            },
            binding_namespace="screenplay.agent.task",
            binding_aggregate_id=project_id,
            binding_command_id=f"{task['id']}:{unit['id']}",
            conversation_turn_id=str(task["turnId"]),
            task_id=str(task["id"]),
            phase=f"screenplay_{role}_generation",
            output_policy=(
                SCREENPLAY_REVIEW_OUTPUT_POLICY
                if role == "review"
                else SCREENPLAY_DELIVERABLE_OUTPUT_POLICY
            ),
            repair_instruction="上一个输出不符合目标交付物协议。只输出完整合法 JSON，title、contentText 和 contentJson 都不能为空。",
            validate=lambda value: _validate_deliverable(
                role,
                value,
                structure_id=structure_id,
                structure_episode_numbers=structure_episode_numbers,
                reviewed_draft_id=reviewed_draft_id,
            ),
            execution_progress_fields={
                "executionSummary": "",
            },
            project_execution=lambda value: (
                value["executionSummary"],
            ),
            signal=signal,
        )
        return {**result.value, "runId": result.run_id}

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
                if str(unit.get("kind") or "") == "generate_candidate"
                else "all"
            ),
        )

    async def _publish(self, task: Mapping[str, Any]) -> dict[str, Any]:
        role = str(task["targetRole"])
        publish_unit = next((
            unit for unit in task.get("units") or ()
            if unit.get("kind") == "publish_candidate_revision"
        ), None)
        if publish_unit is None:
            raise RuntimeError("screenplay task has no publish unit")
        base_revision_id = str(
            (publish_unit.get("input") or {}).get("baseRevisionId") or ""
        ) or None
        generated = [
            unit.get("output") or {}
            for unit in task.get("units") or ()
            if str(unit.get("kind") or "") in {
                "validate_candidate",
                "generate_episode_draft",
                "generate_deliverable",
            }
            and unit.get("status") == "completed"
        ]
        if not generated:
            raise RuntimeError("screenplay task has no generated output")
        if role == "screenplayDraft":
            title, content, text = await self._draft_candidate(
                task,
                generated,
                base_revision_id=base_revision_id,
            )
        else:
            output = generated[-1]
            title = str(output.get("title") or "")
            content = dict(output.get("contentJson") or {})
            text = str(output.get("contentText") or "")
        final_run_id = str(generated[-1].get("runId") or "") or None
        result = await self._revisions.publish_screenplay_agent_task_candidate(
            task_id=str(task["id"]),
            project_id=str(task["projectId"]),
            target_role=role,
            proposal_kind=_PROPOSAL_KIND[role],
            title=title,
            content_json=content,
            content_text=text,
            planner_run_id=str(task.get("plannerRunId") or "") or None,
            finalizing_run_id=final_run_id,
            base_revision_id=base_revision_id,
            source_run_ids=_source_run_ids(generated),
        )
        return {"revisionId": result["revisionId"]}

    async def _draft_candidate(
        self,
        task: Mapping[str, Any],
        generated: Sequence[Mapping[str, Any]],
        *,
        base_revision_id: str | None,
    ) -> tuple[str, dict[str, Any], str]:
        drafts = [dict(output["episodeDraft"]) for output in generated]
        scene_list_ids = {str(output.get("sceneListId") or "") for output in generated}
        if len(scene_list_ids) != 1 or "" in scene_list_ids:
            raise RuntimeError("generated episodes do not share one scene list")
        available = await self._context.available_episode_numbers(
            str(task["projectId"]),
            draft_revision_id=base_revision_id,
        )
        complete_numbers = set(available["draft"]).union(
            int(draft["episodeNumber"]) for draft in drafts
        )
        content = {
            "schemaVersion": 1,
            "documentKind": "scene_draft",
            "sceneListId": next(iter(scene_list_ids)),
            "completedSceneIds": [
                scene_id for draft in drafts for scene_id in draft["sceneIds"]
            ],
            "isComplete": set(available["sceneList"]).issubset(complete_numbers),
            "episodeDrafts": drafts,
        }
        numbers = [int(draft["episodeNumber"]) for draft in drafts]
        title = (
            f"第 {min(numbers)}–{max(numbers)} 集剧本"
            if len(numbers) > 1
            else f"第 {numbers[0]} 集剧本"
        )
        return title, content, "\n\n".join(str(draft["contentText"]) for draft in drafts)


class ScreenplayTaskUnitExecutor:
    """Adapt screenplay generation to PurrA's durable unit contract."""

    def __init__(
        self,
        db,
        *,
        runtime,
        model_executor_factory: Callable[[str], ManagedModelExecutor] | None = None,
        tool_calling_service: ScreenplayToolCallingService | None = None,
    ) -> None:
        self._runtime = runtime
        self._delegate = ScreenplayTaskModelCalls(
            db,
            model_executor_factory=model_executor_factory,
            tool_calling_service=tool_calling_service,
        )
        self._outputs = SqliteScreenplayTaskOutputStore(db)

    async def execute(
        self,
        context: DurableUnitExecutionContext,
        signal=None,
    ) -> LongTaskUnitResult:
        cached = await self._outputs.load_unit(
            context.task.id,
            context.unit.id,
        )
        if cached is not None:
            output_ref, output = cached
            return _unit_result(output_ref, output)
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
        output_ref = await self._outputs.put(
            task_id=context.task.id,
            unit_id=context.unit.id,
            output=output,
        )
        return _unit_result(output_ref, output)

    def classify_failure(self, error: Exception):
        return classify_screenplay_run_failure(error)

    async def _task_view(
        self,
        context: DurableUnitExecutionContext,
    ) -> dict[str, Any]:
        metadata = thaw_json_mapping(context.task.metadata)
        recipe = metadata.get("recipe") or {}
        outputs = await self._outputs.list_for_task(context.task.id)
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
            "targetRole": str(metadata["targetRole"]),
            "plannerRunId": str(metadata.get("plannerRunId") or "") or None,
            "units": units,
        }


def _unit_result(output_ref: str, output: Mapping[str, Any]) -> LongTaskUnitResult:
    revision_id = str(output.get("revisionId") or "").strip()
    return LongTaskUnitResult(
        output_ref=output_ref,
        run_id=str(output.get("runId") or "").strip() or None,
        metadata=({"revisionId": revision_id} if revision_id else {}),
    )


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


def _episode_instruction(episode_number: int, scene_ids: Sequence[str]) -> str:
    return f"""你是专业剧本编剧。按照已采纳场景表创作第 {episode_number} 集完整可拍摄正文。
只输出 JSON：
{{"episodeNumber":{episode_number},"title":"集标题","executionSummary":"用 2 至 4 句说明如何承接前文、落实本集场景目标并控制节奏，不得复述剧本正文","continuitySummary":"供下一集续写的简要连续性摘要","scenes":[{{"sceneId":"场景ID","processSummary":"以‘场景 场景ID 推演：’开头，用 1 至 3 句说明本场人物目标、冲突推进和承接关系，不得复述剧本正文","sceneText":"该场完整剧本文本"}}]}}
scenes 必须严格按顺序且恰好覆盖这些 ID：{list(scene_ids)}。每场必须先输出 processSummary，再输出 sceneText；不得增删、合并或改写 sceneId。"""


def _deliverable_instruction(role: str) -> str:
    requirements = {
        "sourceAnalysis": "contentJson 总结人物、情节、世界观、主题、改编风险及证据；documentKind=source_analysis。",
        "creativeBrief": "contentJson 至少包含 fields，其中 approach 与 premise 明确；documentKind=creative_brief。",
        "structure": "contentJson 必须包含非空 episodes；每项含唯一正整数 number、唯一 id、title、summary；documentKind=episode_outline。",
        "sceneList": "contentJson 必须包含非空 scenes；每项含唯一 id、合法 episodeNumber、heading、objective、conflict、turn、synopsis；documentKind=scene_list。",
        "review": "contentJson 必须含 verdict=ready|revise|major_rework 和 issues 数组；每个问题含唯一 id、severity=critical|major|minor、description、sceneIds；documentKind=review。",
    }[role]
    return f"""你是专业剧本开发 Agent，生成一个可审阅、可采纳的 {role} 候选交付物。
只输出 JSON：{{"title":"标题","executionSummary":"用 2 至 4 句说明本次分析、取舍和校验，不得复述交付物正文","contentText":"完整 Markdown 文档","contentJson":{{...结构化内容...}}}}。
{requirements}
只依据提供的项目事实与已采纳交付物；用户要求修改时，保留 preserve 指定内容。"""


def _final_response_instruction() -> str:
    return """你负责为已经完成校验、但尚未向用户公布的剧本候选稿撰写最终答复。
只输出 JSON：{"finalResponse":"自然语言答复"}。
finalResponse 使用 2 至 4 句，先准确说明完成了哪些候选内容，再说明用户可以在候选稿区域查看和继续编辑。
只能依据输入中的公开事实，不得声称候选稿已经采纳，不得编造版本号、链接或未提供的结果。
不得复述剧本正文，不得输出 JSON 字段解释、工具过程、内部协议、推理过程或固定套话。"""


def _public_candidate_fact(
    role: str,
    output: Mapping[str, Any],
) -> dict[str, Any]:
    summary = " ".join(str(output.get("executionSummary") or "").split())
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
    if summary:
        fact["executionSummary"] = _execution_summary({
            "executionSummary": summary,
        })
    return fact


def _validate_final_response(value: dict[str, Any]) -> dict[str, Any]:
    response = " ".join(str(value.get("finalResponse") or "").split())
    if not response or len(response) > 1_200:
        raise ValueError("finalResponse must be non-empty and concise")
    if any(marker in response for marker in (
        "```",
        "contentText",
        "contentJson",
        "sceneText",
    )):
        raise ValueError("finalResponse contains internal candidate data")
    return {"finalResponse": response}


def _deliverable_tool_instruction(role: str) -> str:
    requirements = {
        "sourceAnalysis": "contentJson 总结人物、情节、世界观、主题、改编风险及章节证据；documentKind=source_analysis。",
        "creativeBrief": "contentJson 至少包含 fields，其中 approach 与 premise 明确；documentKind=creative_brief。",
        "structure": "contentJson 包含非空 episodes；每项含唯一正整数 number、唯一 id、title、summary；documentKind=episode_outline。",
        "sceneList": "contentJson 包含非空 scenes；每项含唯一 id、合法 episodeNumber、heading、objective、conflict、turn、synopsis；documentKind=scene_list。",
        "review": "contentJson 含 verdict=ready|revise|major_rework 和 issues 数组；问题含唯一 id、severity、description、sceneIds；documentKind=review。",
    }[role]
    return f"""你是专业剧本开发 Agent，负责生成可审阅的 {role} 候选交付物。
当前任务已经由宿主规划完成，不要重新制定任务计划。按需调用工具读取项目、已采纳交付物和原作证据，只读取完成当前判断真正需要的内容。

每次工具调用前，先用简短 Markdown 写一段面向用户的操作说明，说明正在查询、比对或校验什么。只写公开执行依据，不输出隐藏思维链，不粘贴交付物正文。

完成后必须且只能调用一次 writeScreenplayCandidatePart。candidate 必须是 {{"title":"标题","executionSummary":"2 至 4 句公开分析和取舍说明","contentText":"完整 Markdown 文档","contentJson":{{...}}}}。{requirements}
修改任务必须遵守 preserve；事实性内容必须来自工具返回的项目状态或来源证据。

候选稿写入成功后只回复一句简短确认，不要在对话中再次输出完整交付物。"""


def _validate_episode(
    value: dict[str, Any],
    episode_number: int,
    expected_scene_ids: Sequence[str],
) -> dict[str, Any]:
    if int(value.get("episodeNumber") or 0) != episode_number:
        raise ValueError("episodeNumber does not match")
    title = str(value.get("title") or "").strip()
    execution_summary = _execution_summary(value)
    summary = str(value.get("continuitySummary") or "").strip()
    scenes = value.get("scenes")
    if not title or not summary or not isinstance(scenes, list):
        raise ValueError("episode title, continuitySummary and scenes are required")
    normalized = [{
        "sceneId": str(scene.get("sceneId") or "").strip(),
        "processSummary": _scene_process_summary(scene),
        "sceneText": str(scene.get("sceneText") or "").strip(),
    } for scene in scenes if isinstance(scene, Mapping)]
    if tuple(scene["sceneId"] for scene in normalized) != tuple(expected_scene_ids):
        raise ValueError("scene ids do not match the accepted scene list")
    if any(not scene["sceneText"] for scene in normalized):
        raise ValueError("scene text must not be empty")
    return {
        "episodeNumber": episode_number,
        "title": title,
        "executionSummary": execution_summary,
        "continuitySummary": summary,
        "scenes": normalized,
    }


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
        "processSummary": _scene_process_summary(value),
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
        "executionSummary": _execution_summary(value),
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
    execution_summary = _execution_summary(value)
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
        "executionSummary": execution_summary,
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
            "executionSummary": normalized["executionSummary"],
            "contentJson": normalized["contentJson"],
        },
        "contentText": normalized["contentText"],
    }


def _validate_review_fragment_candidate(
    candidate: Mapping[str, Any],
    *,
    episode_number: int,
    allowed_scene_ids: Sequence[str],
    reviewed_draft_id: str,
    reviewed_content_digest: str,
) -> dict[str, Any]:
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
            raise ValueError("review fragment references another episode")
        issues.append({
            **dict(issue),
            "id": f"episode-{episode_number}:{issue['id']}",
            "sceneIds": list(scene_ids),
        })
    content.update({
        "issues": issues,
        "issueCount": len(issues),
        "criticalIssueCount": sum(
            issue["severity"] == "critical" for issue in issues
        ),
        "reviewedEpisode": episode_number,
        "reviewStatus": "completed",
        "reviewedContentDigest": reviewed_content_digest,
        "inputContractVersion": 2,
    })
    payload["contentJson"] = content
    return {**normalized, "payload": payload}


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
            "title": title,
            "executionSummary": _execution_summary(value),
            "contentJson": {"scenes": normalized_scenes},
        },
        "contentText": text,
    }


def _draft_scene_ids(value: object) -> tuple[str, ...]:
    draft = value if isinstance(value, Mapping) else {}
    direct = draft.get("sceneIds")
    if isinstance(direct, list):
        result = tuple(str(item or "").strip() for item in direct)
    else:
        scenes = draft.get("sceneTexts") or draft.get("scenes") or ()
        result = tuple(
            str(scene.get("sceneId") or scene.get("id") or "").strip()
            for scene in scenes
            if isinstance(scene, Mapping)
        )
    if not result or any(not scene_id for scene_id in result):
        raise ValueError("draft episode scene identity is incomplete")
    return result


def _aggregate_review_verdict(values) -> str:
    ranks = {"ready": 0, "revise": 1, "major_rework": 2}
    normalized = tuple(str(value) for value in values)
    if not normalized or any(value not in ranks for value in normalized):
        raise ValueError("review fragment verdict is invalid")
    return max(normalized, key=ranks.__getitem__)


def _execution_summary(value: Mapping[str, Any]) -> str:
    summary = " ".join(str(value.get("executionSummary") or "").split())
    if not summary or len(summary) > 600:
        raise ValueError("executionSummary must be a concise work-log summary")
    if any(marker in summary for marker in (
        "```",
        "{",
        "}",
        "sceneText",
        "contentText",
        "contentJson",
    )):
        raise ValueError("executionSummary contains artifact payload data")
    return summary


def _scene_process_summary(value: Mapping[str, Any]) -> str:
    summary = " ".join(str(value.get("processSummary") or "").split())
    if not summary or len(summary) > 600:
        raise ValueError("scene processSummary is required and must be concise")
    if any(marker in summary for marker in (
        "```",
        "{",
        "}",
        "sceneText",
        "contentText",
        "contentJson",
    )):
        raise ValueError("scene processSummary contains artifact payload data")
    return summary


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


def _last_generated_episode(units: Sequence[Mapping[str, Any]], number: int):
    candidates = [
        output.get("episodeDraft")
        for unit in units
        if unit.get("status") == "completed"
        and isinstance((output := unit.get("output") or {}), Mapping)
        and isinstance(output.get("episodeDraft"), Mapping)
        and int(output["episodeDraft"].get("episodeNumber") or 0) < number
    ]
    return candidates[-1] if candidates else None


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


__all__ = ["ScreenplayTaskModelCalls", "ScreenplayTaskUnitExecutor"]
