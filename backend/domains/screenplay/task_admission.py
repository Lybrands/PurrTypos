"""Resolve Planner semantics against authoritative screenplay state."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from agent_core.contracts import (
    AgentRunRequest,
    TaskPlan,
)
from agent_core.task_admission import (
    ExecutionMode,
    TaskAdmissionDecision,
)
from domains.screenplay.contracts import (
    SCREENPLAY_DOMAIN_NAMESPACE,
    ScreenplayDomainContext,
)
from domains.screenplay.adaptation_brief import SERIES_FORMATS
from domains.screenplay.scene_order import ordered_scene_mappings
from domains.screenplay.stage_tasks import select_draft_scenes
from domains.screenplay.workflow_compiler import (
    ScreenplayDraftWorkflow,
    build_screenplay_draft_workflow,
)
from domains.screenplay.query_port import ScreenplayQueryPort


SCREENPLAY_DRAFT_LONG_TASK_KIND = "screenplay_draft_generation"
_STAGE_COMPLETION_TOOLS = {
    "source_analysis": "finalizeSourceAnalysisProposal",
    "creative_brief": "finalizeCreativeBriefProposal",
    "episode_outline": "finalizeScreenplayStructureProposal",
    "beat_sheet": "finalizeScreenplayStructureProposal",
    "scene_list": "finalizeSceneListProposal",
}

_STAGE_TITLES = {
    "source_analysis": "原作范围分析",
    "creative_brief": "创作简报",
    "episode_outline": "分集结构",
    "beat_sheet": "节拍结构",
    "scene_list": "场景表",
}

@dataclass(frozen=True, slots=True)
class ResolvedScreenplayDraftTask:
    project_id: str
    scene_list_document_id: str
    base_draft_document_id: str | None
    target_scenes: tuple[Mapping[str, Any], ...]
    workflow: ScreenplayDraftWorkflow | None
    scope: str

    def decision_metadata(self) -> dict[str, Any]:
        return {
            "namespace": SCREENPLAY_DOMAIN_NAMESPACE,
            "kind": SCREENPLAY_DRAFT_LONG_TASK_KIND,
            "projectId": self.project_id,
            "sceneListDocumentId": self.scene_list_document_id,
            "baseDraftDocumentId": self.base_draft_document_id,
            "scope": self.scope,
            "targetSceneIds": [str(scene["id"]) for scene in self.target_scenes],
            "maxParallelism": (
                self.workflow.max_parallelism
                if self.workflow is not None
                else 1
            ),
            "minimumModelCalls": (
                self.workflow.minimum_model_call_count
                if self.workflow is not None
                else 1
            ),
            "plannedModelCalls": (
                self.workflow.model_call_count
                if self.workflow is not None
                else 1
            ),
        }


class ScreenplayTaskAdmissionEvaluator:
    """Application-injected evaluator backed by screenplay domain rules."""

    def __init__(self, query: ScreenplayQueryPort) -> None:
        self._query = query

    async def evaluate(self, request, plan, signal=None) -> TaskAdmissionDecision:
        del signal
        context = ScreenplayDomainContext.from_core_context(
            request.domain_context
        )
        if (
            context.task_intent == "stage_deliverable"
            and context.requested_stage in {
                "orientation",
                "brief",
                "structure",
                "scenes",
            }
        ):
            planned_tools = {
                tool
                for step in plan.steps
                for tool in step.suggested_tools
            }
            resolved_stage = await resolve_screenplay_stage_task(
                self._query,
                request,
            )
            if resolved_stage is None:
                return TaskAdmissionDecision(
                    mode=ExecutionMode.REJECT,
                    reason_code="screenplay_stage_scope_unresolvable",
                    estimated_units=0,
                    estimated_model_calls=0,
                    message=(
                        "当前阶段或上游已接受版本已经变化，已停止本次执行。"
                        "请刷新项目后重试。"
                    ),
                )
            completion_capability = str(
                resolved_stage["completionCapability"]
            )
            if completion_capability not in planned_tools:
                return TaskAdmissionDecision(
                    mode=ExecutionMode.REJECT,
                    reason_code="screenplay_stage_planner_capability_invalid",
                    estimated_units=0,
                    estimated_model_calls=0,
                    message=(
                        "Planner 未选择当前阶段的正式交付能力，"
                        "已停止本次执行。"
                    ),
                )
            return TaskAdmissionDecision(
                mode=ExecutionMode.INLINE,
                reason_code="screenplay_stage_executes_in_primary_run",
                estimated_units=len(plan.steps),
                estimated_model_calls=sum(
                    1 for step in plan.steps
                    if str(step.executor.value) == "tool"
                ),
                metadata=resolved_stage,
            )
        try:
            resolved = await resolve_screenplay_draft_task(
                self._query,
                request,
                plan,
            )
        except ValueError as error:
            return TaskAdmissionDecision(
                mode=ExecutionMode.REJECT,
                reason_code="screenplay_draft_workflow_invalid",
                estimated_units=0,
                estimated_model_calls=0,
                message=(
                    "当前剧本范围无法生成可靠的持久化执行流程："
                    + str(error)
                ),
            )
        if resolved is None:
            if _is_screenplay_draft_plan(request, plan):
                # A draft plan whose semantic range no longer resolves must
                # never fall through to the generic inline default. Doing so
                # would let a large or stale draft request bypass durable
                # batching and ask one model round to emit the full payload.
                return TaskAdmissionDecision(
                    mode=ExecutionMode.REJECT,
                    reason_code="screenplay_draft_scope_unresolvable",
                    estimated_units=0,
                    estimated_model_calls=0,
                    message=(
                        "当前创作范围无法与已接受的场景表对应，已停止本次执行。"
                        "请刷新项目后重试。"
                    ),
                )
            return TaskAdmissionDecision()
        scene_count = len(resolved.target_scenes)
        model_calls = (
            resolved.workflow.model_call_count
            if resolved.workflow is not None
            else 1
        )
        if scene_count == 1 and resolved.workflow is None:
            return TaskAdmissionDecision(
                mode=ExecutionMode.INLINE,
                reason_code="screenplay_draft_fits_inline_run",
                estimated_units=scene_count,
                estimated_model_calls=1,
                metadata=resolved.decision_metadata(),
            )
        return TaskAdmissionDecision(
            mode=ExecutionMode.DURABLE,
            reason_code="screenplay_draft_requires_multiple_runs",
            estimated_units=scene_count,
            estimated_model_calls=model_calls,
            # Core Planner owns the user-visible capability step. The host
            # owns the durable mechanics once that step is admitted.
            covered_step_ids=tuple(step.id for step in plan.steps),
            execution_recipe=(
                resolved.workflow.to_execution_recipe()
                if resolved.workflow is not None
                else None
            ),
            metadata=resolved.decision_metadata(),
        )


async def resolve_screenplay_stage_task(
    query: ScreenplayQueryPort,
    request: AgentRunRequest,
) -> dict[str, Any] | None:
    """Validate the current stage and accepted inputs for primary-Run execution."""

    if request.domain_context.namespace != SCREENPLAY_DOMAIN_NAMESPACE:
        return None
    context = ScreenplayDomainContext.from_core_context(request.domain_context)
    if context.task_intent != "stage_deliverable":
        return None
    project = await query.get_project(context.project_id)
    if project is None:
        return None
    stage = str(project.get("active_stage") or "orientation").strip()
    if stage != context.requested_stage:
        return None
    if stage == "orientation":
        deliverable_kind = (
            "source_analysis"
            if str(project.get("source_book_id") or "").strip()
            else "creative_brief"
        )
        required_kinds: tuple[str, ...] = ()
    elif stage == "brief":
        deliverable_kind = "creative_brief"
        required_kinds = (
            ("source_analysis",)
            if str(project.get("source_book_id") or "").strip()
            else ()
        )
    elif stage == "structure":
        deliverable_kind = (
            "episode_outline"
            if str(project.get("format") or "") in SERIES_FORMATS
            else "beat_sheet"
        )
        required_kinds = ("creative_brief",)
    elif stage == "scenes":
        deliverable_kind = "scene_list"
        required_kinds = ("episode_outline", "beat_sheet")
    else:
        return None
    rows = await query.list_current_documents(context.project_id)
    accepted_by_kind: dict[str, str] = {}
    for row in rows:
        kind = str(row.get("kind") or "").strip()
        if kind and kind not in accepted_by_kind:
            accepted_by_kind[kind] = str(row["id"])
    if required_kinds and not any(
        kind in accepted_by_kind for kind in required_kinds
    ):
        return None
    title = _STAGE_TITLES[deliverable_kind]
    return {
        "namespace": SCREENPLAY_DOMAIN_NAMESPACE,
        "projectId": context.project_id,
        "stage": stage,
        "deliverableKind": deliverable_kind,
        "taskTitle": title,
        "completionCapability": (
            _STAGE_COMPLETION_TOOLS[deliverable_kind]
        ),
        "acceptedInputIds": {
            kind: accepted_by_kind[kind]
            for kind in required_kinds
            if kind in accepted_by_kind
        },
    }


def _is_screenplay_draft_plan(
    request: AgentRunRequest,
    plan: TaskPlan,
) -> bool:
    if request.domain_context.namespace != SCREENPLAY_DOMAIN_NAMESPACE:
        return False
    context = ScreenplayDomainContext.from_core_context(request.domain_context)
    if context.requested_stage != "draft":
        return False
    planned_tools = {
        tool
        for step in plan.steps
        for tool in step.suggested_tools
    }
    return "proposeSceneDraft" in planned_tools


async def resolve_screenplay_draft_task(
    query: ScreenplayQueryPort,
    request: AgentRunRequest,
    plan: TaskPlan,
) -> ResolvedScreenplayDraftTask | None:
    if request.domain_context.namespace != SCREENPLAY_DOMAIN_NAMESPACE:
        return None
    context = ScreenplayDomainContext.from_core_context(request.domain_context)
    if context.requested_stage != "draft":
        return None
    planned_tools = {
        tool
        for step in plan.steps
        for tool in step.suggested_tools
    }
    if "proposeSceneDraft" not in planned_tools:
        return None
    target = plan.task_spec.target if plan.task_spec is not None else {}
    rows = [
        row for row in await query.list_current_documents(context.project_id)
        if str(row.get("kind") or "") in {"scene_list", "scene_draft"}
    ]
    scene_list = next(
        (row for row in rows if str(row.get("kind")) == "scene_list"),
        None,
    )
    latest_draft = next(
        (row for row in rows if str(row.get("kind")) == "scene_draft"),
        None,
    )
    if scene_list is None:
        return None
    scene_episode_rows = await query.list_episode_rows(
        str(scene_list.get("id") or ""),
        include_content=True,
    )
    if scene_episode_rows:
        scene_values = [
            dict(scene)
            for row in scene_episode_rows
            for scene in row.get("content_json", {}).get("scenes", [])
            if isinstance(scene, Mapping)
        ]
    else:
        scene_content = _mapping(scene_list.get("content_json"))
        scene_values = [
            dict(scene)
            for scene in scene_content.get("scenes", [])
            if isinstance(scene, Mapping)
        ]
    draft_content = _mapping(
        latest_draft.get("content_json") if latest_draft else None
    )
    completed = {
        str(item)
        for item in draft_content.get("completedSceneIds", [])
        if str(item).strip()
    } if isinstance(draft_content.get("completedSceneIds"), list) else set()
    raw_scenes = ordered_scene_mappings({
        "scenes": scene_values,
    })
    pending = tuple(
        dict(scene)
        for scene in raw_scenes
        if isinstance(scene, Mapping)
        and str(scene.get("id") or "").strip()
        and str(scene.get("id")) not in completed
    )
    if not pending:
        return None
    scope = str(target.get("scope") or "").strip()
    if context.draft_scope != "planner":
        requested = select_draft_scenes(
            pending,
            scope=context.draft_scope,
            fallback_count=context.draft_scene_count,
        )
        if not requested:
            return None
        # Explicit UI/application commands bind the domain range. The shared
        # Core Planner still authors the visible execution plan, but cannot
        # broaden the host-bound scene range.
        selected = tuple(requested)
        scope = context.draft_scope
    else:
        selected = _select_target_scenes(
            pending,
            scope=scope,
            target=target,
            fallback_count=context.draft_scene_count,
        )
        if not selected:
            return None
    workflow = (
        build_screenplay_draft_workflow(selected)
        if len(selected) > 1
        else None
    )
    return ResolvedScreenplayDraftTask(
        project_id=context.project_id,
        scene_list_document_id=str(scene_list["id"]),
        base_draft_document_id=(
            str(latest_draft["id"]) if latest_draft is not None else None
        ),
        target_scenes=selected,
        workflow=workflow,
        scope=scope or "count",
    )


def _select_target_scenes(
    pending: tuple[Mapping[str, Any], ...],
    *,
    scope: str,
    target: Mapping[str, Any],
    fallback_count: int,
) -> tuple[Mapping[str, Any], ...]:
    if scope in {
        "all_remaining",
        "next_scene",
        "next_episode",
        "current_episode_remaining",
    }:
        return select_draft_scenes(
            pending,
            scope=scope,
            fallback_count=fallback_count,
        )
    if scope == "next_episodes":
        return select_draft_scenes(
            pending,
            scope=scope,
            fallback_count=fallback_count,
            episode_count=_positive_int(target.get("count")),
        )
    if scope == "explicit_scene_ids":
        raw_ids = target.get("sceneIds")
        requested = (
            [str(item) for item in raw_ids]
            if isinstance(raw_ids, Sequence)
            and not isinstance(raw_ids, (str, bytes, bytearray))
            else []
        )
        selected = tuple(scene for scene in pending if str(scene.get("id")) in requested)
        expected_prefix = pending[:len(selected)]
        return selected if selected == expected_prefix and len(selected) == len(requested) else ()
    raw_count = target.get("count") if scope == "count" else fallback_count
    try:
        count = max(1, int(raw_count))
    except (TypeError, ValueError):
        count = max(1, int(fallback_count))
    return pending[:count]


def _positive_int(value: object) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _mapping(value: object) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, Mapping) else {}


__all__ = [
    "ResolvedScreenplayDraftTask",
    "SCREENPLAY_DRAFT_LONG_TASK_KIND",
    "ScreenplayTaskAdmissionEvaluator",
    "resolve_screenplay_draft_task",
    "resolve_screenplay_stage_task",
]
