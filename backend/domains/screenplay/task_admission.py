"""Resolve Planner semantics against authoritative screenplay state."""

from __future__ import annotations

import json
import re
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


SCREENPLAY_DRAFT_LONG_TASK_KIND = "screenplay_draft_generation"
_MAX_SCREENPLAY_CHILD_AGENTS = 3
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
    execution_units: tuple[Mapping[str, Any], ...]
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
            "executionUnits": [dict(unit) for unit in self.execution_units],
            # Planner owns the graph and batch boundaries.  The host derives
            # only the executor capacity from that graph, capped by the
            # screenplay writer pool's resource limit.
            "maxParallelism": _planned_parallelism(self.execution_units),
        }


class ScreenplayTaskAdmissionEvaluator:
    """Application-injected evaluator backed by screenplay domain rules."""

    def __init__(self, db) -> None:
        self._db = db

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
            task_spec = plan.task_spec
            planned_tools = {
                tool
                for step in plan.steps
                for tool in step.suggested_tools
            }
            if (
                task_spec is None
                or str(
                    task_spec.target.get("domainAction") or ""
                ).strip() != "generate_stage_deliverable"
                or str(task_spec.target.get("scope") or "").strip()
                != "current_stage"
            ):
                return TaskAdmissionDecision(
                    mode=ExecutionMode.REJECT,
                    reason_code="screenplay_stage_planner_scope_invalid",
                    estimated_units=0,
                    estimated_model_calls=0,
                    message=(
                        "Planner 未生成当前阶段所需的合法执行范围，"
                        "已停止本次执行。"
                    ),
                )
            resolved_stage = await resolve_screenplay_stage_task(
                self._db,
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
        resolved = await resolve_screenplay_draft_task(self._db, request, plan)
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
        model_calls = sum(
            1 for unit in resolved.execution_units
            if str(unit.get("kind") or "") in {
                "scene_generation",
                "continuity_review",
            }
        )
        if (
            scene_count == 1
            and not resolved.execution_units
        ):
            return TaskAdmissionDecision(
                mode=ExecutionMode.INLINE,
                reason_code="screenplay_draft_fits_inline_run",
                estimated_units=scene_count,
                estimated_model_calls=1,
                metadata=resolved.decision_metadata(),
            )
        if not resolved.execution_units or model_calls < 1:
            return TaskAdmissionDecision(
                mode=ExecutionMode.REJECT,
                reason_code="screenplay_draft_planner_units_missing",
                estimated_units=0,
                estimated_model_calls=0,
                message=(
                    "Planner 未生成完整的长任务执行图，已停止本次执行。"
                ),
            )
        return TaskAdmissionDecision(
            mode=ExecutionMode.DURABLE,
            reason_code="screenplay_draft_requires_multiple_runs",
            estimated_units=scene_count,
            estimated_model_calls=model_calls,
            metadata=resolved.decision_metadata(),
        )


async def resolve_screenplay_stage_task(
    db,
    request: AgentRunRequest,
) -> dict[str, Any] | None:
    """Validate the current stage and accepted inputs for primary-Run execution."""

    if request.domain_context.namespace != SCREENPLAY_DOMAIN_NAMESPACE:
        return None
    context = ScreenplayDomainContext.from_core_context(request.domain_context)
    if context.task_intent != "stage_deliverable":
        return None
    project = await db.fetch_one(
        "SELECT id, active_stage, source_kind, source_book_id, format "
        "FROM screenplay_projects WHERE id = ?",
        [context.project_id],
    )
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
    rows = await db.fetch_all(
        "SELECT id, kind, version FROM screenplay_documents "
        "WHERE project_id = ? AND status = 'accepted' "
        "ORDER BY version DESC",
        [context.project_id],
    )
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
    if context.requested_stage != "draft" or plan.task_spec is None:
        return False
    planned_tools = {
        tool
        for step in plan.steps
        for tool in step.suggested_tools
    }
    return (
        str(plan.task_spec.target.get("domainAction") or "").strip()
        == "generate_scene_drafts"
        or "proposeSceneDraft" in planned_tools
    )


async def resolve_screenplay_draft_task(
    db,
    request: AgentRunRequest,
    plan: TaskPlan,
) -> ResolvedScreenplayDraftTask | None:
    if request.domain_context.namespace != SCREENPLAY_DOMAIN_NAMESPACE:
        return None
    context = ScreenplayDomainContext.from_core_context(request.domain_context)
    if context.requested_stage != "draft" or plan.task_spec is None:
        return None
    planned_tools = {
        tool
        for step in plan.steps
        for tool in step.suggested_tools
    }
    target = plan.task_spec.target
    domain_action = str(target.get("domainAction") or "").strip()
    if (
        domain_action != "generate_scene_drafts"
        and "proposeSceneDraft" not in planned_tools
    ):
        return None
    rows = await db.fetch_all(
        "SELECT id, kind, version, content_json FROM screenplay_documents "
        "WHERE project_id = ? AND status = 'accepted' "
        "AND kind IN ('scene_list', 'scene_draft') "
        "ORDER BY version DESC",
        [context.project_id],
    )
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
    scene_content = _mapping(scene_list.get("content_json"))
    draft_content = _mapping(
        latest_draft.get("content_json") if latest_draft else None
    )
    completed = {
        str(item)
        for item in draft_content.get("completedSceneIds", [])
        if str(item).strip()
    } if isinstance(draft_content.get("completedSceneIds"), list) else set()
    raw_scenes = ordered_scene_mappings(scene_content)
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
        if not requested or not _planner_scope_matches_bound_request(
            target,
            requested_scope=context.draft_scope,
            requested_scenes=requested,
            requested_count=context.draft_scene_count,
        ):
            return None
        # A button or inferred stable scope is resolved exactly once from the
        # accepted scene-list checkpoint. Planner owns execution-unit
        # decomposition, not a second interpretation of that user range.
        selected = tuple(requested)
    else:
        selected = _select_target_scenes(
            pending,
            scope=scope,
            target=target,
            fallback_count=context.draft_scene_count,
        )
        if not selected:
            return None
    execution_units = _resolve_planned_execution_units(target, selected)
    if target.get("executionUnits") is not None and execution_units is None:
        return None
    return ResolvedScreenplayDraftTask(
        project_id=context.project_id,
        scene_list_document_id=str(scene_list["id"]),
        base_draft_document_id=(
            str(latest_draft["id"]) if latest_draft is not None else None
        ),
        target_scenes=selected,
        execution_units=execution_units or (),
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


def _planner_scope_matches_bound_request(
    target: Mapping[str, Any],
    *,
    requested_scope: str,
    requested_scenes: Sequence[Mapping[str, Any]],
    requested_count: int,
) -> bool:
    """Validate Planner semantics without re-resolving a host-bound range."""

    scope = str(target.get("scope") or "").strip()
    if scope == "explicit_scene_ids":
        raw_ids = target.get("sceneIds")
        if not isinstance(raw_ids, Sequence) or isinstance(
            raw_ids,
            (str, bytes, bytearray),
        ):
            return False
        return [str(item) for item in raw_ids] == [
            str(scene.get("id")) for scene in requested_scenes
        ]
    if requested_scope == "next_scene":
        return scope == "next_scene"
    if requested_scope == "next_episode":
        return scope in {"next_episode", "current_episode_remaining"}
    stable_episode_scope = re.fullmatch(
        r"next_(\d+)_episodes",
        requested_scope,
    )
    if stable_episode_scope is not None:
        episode_count = int(stable_episode_scope.group(1))
        return (
            scope == "next_episodes"
            and _positive_int(target.get("count")) == episode_count
        )
    if requested_scope == "all_remaining":
        return scope == "all_remaining"
    if requested_scope == "count":
        return (
            scope == "count"
            and _positive_int(target.get("count"))
            == max(1, int(requested_count))
        )
    return False


def _positive_int(value: object) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _resolve_planned_execution_units(
    target: Mapping[str, Any],
    scenes: Sequence[Mapping[str, Any]],
) -> tuple[Mapping[str, Any], ...] | None:
    raw_units = target.get("executionUnits")
    if raw_units is None:
        return ()
    if not isinstance(raw_units, Sequence) or isinstance(
        raw_units,
        (str, bytes, bytearray),
    ) or not raw_units:
        return None
    scenes_by_id = {str(scene.get("id")): scene for scene in scenes}
    expected_ids = [str(scene.get("id")) for scene in scenes]
    covered_ids: list[str] = []
    seen_unit_ids: set[str] = set()
    dependencies_by_id: dict[str, tuple[str, ...]] = {}
    units: list[Mapping[str, Any]] = []
    terminal_ids: list[str] = []
    item_ids_by_unit: dict[str, tuple[str, ...]] = {}
    for position, raw_unit in enumerate(raw_units):
        if not isinstance(raw_unit, Mapping):
            return None
        unit_id = str(raw_unit.get("id") or "").strip()
        kind = str(raw_unit.get("kind") or "").strip()
        raw_dependencies = raw_unit.get("dependsOn")
        if (
            not unit_id
            or unit_id in seen_unit_ids
            or kind not in {
                "scene_generation",
                "continuity_review",
                "finalize",
            }
            or not isinstance(raw_dependencies, Sequence)
            or isinstance(raw_dependencies, (str, bytes, bytearray))
        ):
            return None
        dependencies = tuple(str(item).strip() for item in raw_dependencies)
        dependency_reason = str(
            raw_unit.get("dependencyReason") or ""
        ).strip()
        if (
            any(not item for item in dependencies)
            or len(dependencies) != len(set(dependencies))
            or any(item not in seen_unit_ids for item in dependencies)
            or (
                kind == "scene_generation"
                and dependencies
                and not dependency_reason
            )
        ):
            return None
        raw_item_ids = raw_unit.get("itemIds")
        if kind in {"scene_generation", "continuity_review"}:
            if (
                not isinstance(raw_item_ids, Sequence)
                or isinstance(raw_item_ids, (str, bytes, bytearray))
                or not raw_item_ids
            ):
                return None
            scene_ids = [str(item).strip() for item in raw_item_ids]
            if any(scene_id not in scenes_by_id for scene_id in scene_ids):
                return None
            if kind == "scene_generation":
                covered_ids.extend(scene_ids)
            elif not _review_items_are_generated_by_ancestors(
                scene_ids,
                dependencies,
                dependencies_by_id,
                item_ids_by_unit,
            ):
                return None
            units.append({
                "id": unit_id,
                "kind": kind,
                "position": position,
                "dependsOn": list(dependencies),
                "sceneIds": scene_ids,
                "sceneHeadings": [
                    str(scenes_by_id[scene_id].get("heading") or "")
                    for scene_id in scene_ids
                ],
                "dependencyReason": dependency_reason,
            })
        else:
            if raw_item_ids not in (None, (), []):
                return None
            terminal_ids.append(unit_id)
            units.append({
                "id": unit_id,
                "kind": kind,
                "position": position,
                "dependsOn": list(dependencies),
            })
        seen_unit_ids.add(unit_id)
        dependencies_by_id[unit_id] = dependencies
        item_ids_by_unit[unit_id] = tuple(
            str(item).strip()
            for item in raw_item_ids
        ) if kind != "finalize" else ()
    if covered_ids != expected_ids or len(terminal_ids) != 1:
        return None
    terminal_id = terminal_ids[0]
    if str(units[-1].get("id") or "") != terminal_id:
        return None
    depended_on = {
        dependency
        for unit in units
        if str(unit.get("id") or "") != terminal_id
        for dependency in dependencies_by_id[str(unit["id"])]
    }
    execution_leaves = {
        str(unit["id"])
        for unit in units
        if unit.get("kind") != "finalize"
        and str(unit["id"]) not in depended_on
    }
    if set(dependencies_by_id[terminal_id]) != execution_leaves:
        return None
    return tuple(units)


def _review_items_are_generated_by_ancestors(
    scene_ids: Sequence[str],
    dependencies: Sequence[str],
    dependencies_by_id: Mapping[str, Sequence[str]],
    item_ids_by_unit: Mapping[str, Sequence[str]],
) -> bool:
    """Reject reviewer nodes that cannot observe their assigned draft prose."""

    ancestors: set[str] = set()
    pending = list(dependencies)
    while pending:
        dependency_id = str(pending.pop() or "").strip()
        if not dependency_id or dependency_id in ancestors:
            continue
        ancestors.add(dependency_id)
        pending.extend(dependencies_by_id.get(dependency_id, ()))
    available = {
        scene_id
        for dependency_id in ancestors
        for scene_id in item_ids_by_unit.get(dependency_id, ())
    }
    return set(scene_ids).issubset(available)


def _planned_parallelism(
    units: Sequence[Mapping[str, Any]],
) -> int:
    """Return the widest Planner-authored child-agent frontier.

    This does not invent batches or dependencies.  It merely turns the
    validated DAG into a bounded worker-pool size; serial Planner graphs stay
    serial and independent branches may run together.
    """

    levels: dict[str, int] = {}
    width_by_level: dict[int, int] = {}
    for unit in units:
        unit_id = str(unit.get("id") or "").strip()
        if not unit_id:
            continue
        dependencies = tuple(
            str(item or "").strip()
            for item in unit.get("dependsOn", [])
            if str(item or "").strip()
        )
        level = 0 if not dependencies else 1 + max(
            levels.get(dependency, 0) for dependency in dependencies
        )
        levels[unit_id] = level
        if str(unit.get("kind") or "") not in {
            "scene_generation",
            "continuity_review",
        }:
            continue
        width_by_level[level] = width_by_level.get(level, 0) + 1
    return min(
        _MAX_SCREENPLAY_CHILD_AGENTS,
        max(width_by_level.values(), default=1),
    )


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
