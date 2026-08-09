"""Application services for durable screenplay generation tasks."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Mapping

from agent_core.contracts import AgentRunRequest, TaskPlan
from agent_core.json_values import thaw_json_mapping
from agent_core.long_tasks import (
    LongTaskCreateCommand,
    LongTaskStatus,
    LongTaskUnitSpec,
)
from agent_core.task_admission import (
    LongTaskDispatchReceipt,
    LongTaskExecutionResult,
    LongTaskExecutionUpdate,
    TaskAdmissionDecision,
)


ScreenplayLongTaskExecutor = Callable[
    [str, str, Callable[[LongTaskExecutionUpdate], Awaitable[None]], object],
    Awaitable[LongTaskExecutionResult],
]
from agent_core.work_items import WorkItemLifecycle
from agent_core.work_items.contracts import (
    WorkItemCreateCommand,
    WorkItemRunLinkCommand,
    WorkItemRunRelation,
    WorkItemTransitionCommand,
)
from domains.screenplay.contracts import SCREENPLAY_DOMAIN_NAMESPACE
from domains.screenplay.task_admission import (
    SCREENPLAY_DRAFT_LONG_TASK_KIND,
)


class ScreenplayLongTaskDispatcher:
    def __init__(
        self,
        *,
        work_items: WorkItemLifecycle,
        long_tasks,
        executor: ScreenplayLongTaskExecutor | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._work_items = work_items
        self._long_tasks = long_tasks
        self._executor = executor
        self._id_factory = id_factory or (lambda: uuid.uuid4().hex)

    async def dispatch(
        self,
        request: AgentRunRequest,
        plan: TaskPlan,
        decision: TaskAdmissionDecision,
        *,
        parent_run_id: str,
        signal=None,
    ) -> LongTaskDispatchReceipt:
        del signal
        metadata = thaw_json_mapping(decision.metadata)
        if metadata.get("namespace") != SCREENPLAY_DOMAIN_NAMESPACE:
            raise ValueError("unsupported durable task namespace")
        task_kind = str(metadata.get("kind") or "").strip()
        if task_kind != SCREENPLAY_DRAFT_LONG_TASK_KIND:
            raise ValueError("unsupported durable screenplay task kind")
        project_id = str(metadata.get("projectId") or "").strip()
        if not project_id:
            raise ValueError("durable screenplay task has no project scope")
        session_id = _session_id(getattr(request, "session_id", None))
        recipe = decision.execution_recipe
        recipe_metadata = recipe.to_metadata() if recipe is not None else None
        task_metadata = {
            **metadata,
            "sessionId": session_id,
            **(
                {
                    "workflowUnits": recipe_metadata["steps"],
                    "maxParallelism": recipe.max_parallelism,
                }
                if recipe_metadata is not None
                else {}
            ),
        }
        if task_kind == SCREENPLAY_DRAFT_LONG_TASK_KIND:
            raw_units = metadata.get("workflowUnits")
            if not isinstance(raw_units, list) or not raw_units:
                raise ValueError(
                    "durable screenplay task has no compiled workflow"
                )
        active = await self._long_tasks.find_active(
            namespace=SCREENPLAY_DOMAIN_NAMESPACE,
            owner_id=project_id,
            kind=task_kind,
            session_id=session_id,
            match_session=True,
        )
        if active is not None:
            return await self._reuse_active_task(
                active,
                metadata=task_metadata,
                parent_run_id=parent_run_id,
                decision=decision,
            )
        recent_tasks = await self._long_tasks.list_for_owner(
            namespace=SCREENPLAY_DOMAIN_NAMESPACE,
            owner_id=project_id,
            kind=task_kind,
            limit=5,
        )
        reusable_same_scope = next((
            item for item in recent_tasks
            if item.status in {
                LongTaskStatus.COMPLETED,
                LongTaskStatus.FAILED,
            }
            and _same_origin_session(item.metadata, session_id)
            and _same_task_scope(item.metadata, task_metadata)
            and _workflows_are_compatible(item.metadata, task_metadata)
        ), None)
        if reusable_same_scope is not None:
            return await self._reuse_active_task(
                reusable_same_scope,
                metadata=task_metadata,
                parent_run_id=parent_run_id,
                decision=decision,
            )
        work_item = await self._work_items.begin(WorkItemCreateCommand(
            namespace=SCREENPLAY_DOMAIN_NAMESPACE,
            kind=task_kind,
            owner_id=project_id,
            created_by_run_id=parent_run_id,
            metadata={
                "goal": plan.task_spec.goal if plan.task_spec else plan.title,
                "scope": metadata.get("scope"),
                "targetSceneIds": metadata.get("targetSceneIds", []),
                "sceneListDocumentId": metadata.get("sceneListDocumentId"),
                "baseDraftDocumentId": metadata.get("baseDraftDocumentId"),
                "stage": metadata.get("stage"),
                "deliverableKind": metadata.get("deliverableKind"),
                "artifactKind": metadata.get("artifactKind"),
                "acceptedInputIds": metadata.get("acceptedInputIds", {}),
            },
        ))
        units = _task_units(task_kind, project_id, task_metadata)
        task_id = f"longtask_{self._id_factory()}"
        try:
            task = await self._long_tasks.create(
                task_id,
                LongTaskCreateCommand(
                    namespace=SCREENPLAY_DOMAIN_NAMESPACE,
                    kind=task_kind,
                    owner_id=project_id,
                    work_item_id=work_item.id,
                    created_by_run_id=parent_run_id,
                    units=tuple(units),
                    max_parallelism=max(
                        1,
                        min(3, int(task_metadata.get("maxParallelism") or 1)),
                    ),
                    metadata={
                        **task_metadata,
                        "goal": plan.task_spec.goal if plan.task_spec else plan.title,
                    },
                ),
            )
        except BaseException:
            await self._work_items.cancel(WorkItemTransitionCommand(
                work_item_id=work_item.id,
                expected_revision=work_item.revision,
            ))
            raise
        if task.work_item_id != work_item.id:
            # A concurrent dispatcher won the storage-level active-task
            # uniqueness race. Retire the losing Work Item and attach this Run
            # to the canonical task instead of leaking a second workflow.
            await self._work_items.cancel(WorkItemTransitionCommand(
                work_item_id=work_item.id,
                expected_revision=work_item.revision,
            ))
            return await self._reuse_active_task(
                task,
                metadata=task_metadata,
                parent_run_id=parent_run_id,
                decision=decision,
            )
        task_title = str(task_metadata.get("taskTitle") or "长篇正文").strip()
        minimum_calls = max(
            1,
            int(task_metadata.get("minimumModelCalls") or 1),
        )
        planned_calls = max(
            minimum_calls,
            int(
                task_metadata.get("plannedModelCalls")
                or decision.estimated_model_calls
                or minimum_calls
            ),
        )
        call_summary = (
            f"预计 {minimum_calls} 次基础调用"
            if minimum_calls == planned_calls
            else f"预计 {minimum_calls}–{planned_calls} 次模型调用"
        )
        message = (
            f"长篇正文任务已开始，共 {decision.estimated_units} 场、"
            f"{call_summary}。"
            "本轮将持续显示逐场创作、全局审阅、定向修订与最终结果。"
        )
        return LongTaskDispatchReceipt(
            task_id=task.id,
            message=message,
            metadata={
                "projectId": project_id,
                "kind": task_kind,
                "status": task.status.value,
                "totalUnits": task.total_units,
                "completedUnits": task.completed_units,
                "taskTitle": task_title,
                "estimatedScenes": (
                    decision.estimated_units
                    if task_kind == SCREENPLAY_DRAFT_LONG_TASK_KIND
                    else None
                ),
            },
        )

    async def execute(
        self,
        task_id: str,
        *,
        parent_run_id: str,
        observer,
        signal=None,
    ) -> LongTaskExecutionResult:
        if self._executor is None:
            raise RuntimeError("durable screenplay executor is not configured")
        return await self._executor(
            task_id,
            parent_run_id,
            observer,
            signal,
        )

    async def _reuse_active_task(
        self,
        task,
        *,
        metadata: dict,
        parent_run_id: str,
        decision: TaskAdmissionDecision,
    ) -> LongTaskDispatchReceipt:
        requested_session_id = _session_id(metadata.get("sessionId"))
        if not _same_origin_session(task.metadata, requested_session_id):
            raise RuntimeError("screenplay_long_task_session_mismatch")
        same_scope = _same_task_scope(task.metadata, metadata)
        if not same_scope:
            # Never let an unrelated active task satisfy the new root Run's
            # admitted Planner step. The caller must finish or cancel the
            # existing task before starting a different durable scope.
            raise RuntimeError("screenplay_long_task_scope_conflict")
        try:
            step_aliases = _workflow_step_aliases(task.metadata, metadata)
        except ValueError as error:
            raise RuntimeError(
                "screenplay_long_task_workflow_conflict"
            ) from error
        work_item = await self._work_items.get(task.work_item_id)
        await self._work_items.link_run(WorkItemRunLinkCommand(
            work_item_id=work_item.id,
            run_id=parent_run_id,
            relation=WorkItemRunRelation.REFERENCE,
            expected_revision=work_item.revision,
        ))
        resumed = False
        if task.status in {
            LongTaskStatus.PAUSED,
            LongTaskStatus.FAILED,
        }:
            task = await self._long_tasks.resume(task.id)
            resumed = True
        task_title = str(
            thaw_json_mapping(task.metadata).get("taskTitle")
            or "长篇正文"
        ).strip()
        recovered = task.status is LongTaskStatus.COMPLETED
        if recovered:
            message = (
                f"已找到同一范围已完成的{task_title}任务，将直接恢复持久化"
                "提案，不重复调用 Writer 或 Reviewer。"
            )
        elif resumed:
            message = (
                f"已恢复原有{task_title}任务，将从上次检查点继续；"
                "本轮将持续更新执行进度与结果。"
            )
        else:
            message = (
                f"已关联到正在执行的同一项{task_title}任务，"
                "本轮将持续显示它的真实进度与结果。"
            )
        return LongTaskDispatchReceipt(
            task_id=task.id,
            message=message,
            metadata={
                "projectId": task.owner_id,
                "kind": task.kind,
                "status": task.status.value,
                "totalUnits": task.total_units,
                "completedUnits": task.completed_units,
                "estimatedScenes": decision.estimated_units,
                "deduplicated": True,
                "resumed": resumed,
                "recovered": recovered,
                "sameScope": same_scope,
                "taskTitle": task_title,
                "durableStepAliases": step_aliases,
            },
        )


def _same_task_scope(existing, requested: dict) -> bool:
    current = thaw_json_mapping(existing)
    return (
        current.get("sceneListDocumentId")
        == requested.get("sceneListDocumentId")
        and current.get("baseDraftDocumentId")
        == requested.get("baseDraftDocumentId")
        and current.get("targetSceneIds")
        == requested.get("targetSceneIds")
    )


def _workflows_are_compatible(existing, requested: dict) -> bool:
    try:
        _workflow_step_aliases(existing, requested)
    except ValueError:
        return False
    return True


def _workflow_step_aliases(existing, requested: dict) -> dict[str, str]:
    """Bind persisted workflow nodes to a new Planner's equivalent ids."""

    existing_units = _workflow_units(existing)
    requested_units = _workflow_units(requested)
    existing_by_key = _workflow_units_by_semantic_key(existing_units)
    requested_by_key = _workflow_units_by_semantic_key(requested_units)
    if set(existing_by_key) != set(requested_by_key):
        raise ValueError("screenplay_long_task_workflow_conflict")

    existing_id_to_key = {
        _workflow_unit_id(unit): key
        for key, unit in existing_by_key.items()
    }
    requested_id_to_key = {
        _workflow_unit_id(unit): key
        for key, unit in requested_by_key.items()
    }
    aliases: dict[str, str] = {}
    for key, existing_unit in existing_by_key.items():
        requested_unit = requested_by_key[key]
        existing_dependencies = _workflow_dependency_keys(
            existing_unit,
            existing_id_to_key,
        )
        requested_dependencies = _workflow_dependency_keys(
            requested_unit,
            requested_id_to_key,
        )
        if existing_dependencies != requested_dependencies:
            raise ValueError("screenplay_long_task_workflow_conflict")
        aliases[_workflow_planner_step_id(existing_unit)] = (
            _workflow_planner_step_id(requested_unit)
        )
    if len(aliases) != len(existing_units):
        raise ValueError("screenplay_long_task_workflow_conflict")
    return aliases


def _workflow_units(metadata) -> list[Mapping[str, object]]:
    raw = thaw_json_mapping(metadata).get("workflowUnits")
    if not isinstance(raw, list) or not raw:
        raise ValueError("screenplay_long_task_workflow_conflict")
    units = [item for item in raw if isinstance(item, Mapping)]
    if len(units) != len(raw):
        raise ValueError("screenplay_long_task_workflow_conflict")
    return units


def _workflow_units_by_semantic_key(
    units: list[Mapping[str, object]],
) -> dict[tuple[str, str, tuple[str, ...]], Mapping[str, object]]:
    result: dict[
        tuple[str, str, tuple[str, ...]],
        Mapping[str, object],
    ] = {}
    for unit in units:
        key = (
            str(unit.get("kind") or "").strip(),
            str(unit.get("agentRole") or "").strip(),
            tuple(str(item) for item in (unit.get("sceneIds") or [])),
        )
        if not key[0] or key in result:
            raise ValueError("screenplay_long_task_workflow_conflict")
        result[key] = unit
    return result


def _workflow_unit_id(unit: Mapping[str, object]) -> str:
    unit_id = str(unit.get("id") or "").strip()
    if not unit_id:
        raise ValueError("screenplay_long_task_workflow_conflict")
    return unit_id


def _workflow_planner_step_id(unit: Mapping[str, object]) -> str:
    step_id = str(
        unit.get("plannerStepId") or unit.get("id") or ""
    ).strip()
    if not step_id:
        raise ValueError("screenplay_long_task_workflow_conflict")
    return step_id


def _workflow_dependency_keys(
    unit: Mapping[str, object],
    id_to_key: Mapping[str, tuple[str, str, tuple[str, ...]]],
) -> frozenset[tuple[str, str, tuple[str, ...]]]:
    raw = unit.get("dependsOn")
    if not isinstance(raw, list):
        raise ValueError("screenplay_long_task_workflow_conflict")
    try:
        return frozenset(id_to_key[str(item)] for item in raw)
    except KeyError as error:
        raise ValueError("screenplay_long_task_workflow_conflict") from error


def _session_id(value: object) -> int | None:
    if value is None:
        return None
    try:
        session_id = int(value)
    except (TypeError, ValueError):
        return None
    return session_id if session_id > 0 else None


def _same_origin_session(existing, requested_session_id: int | None) -> bool:
    current = thaw_json_mapping(existing)
    return _session_id(current.get("sessionId")) == requested_session_id


def _task_units(
    task_kind: str,
    project_id: str,
    metadata: dict,
) -> list[LongTaskUnitSpec]:
    raw_units = metadata.get("workflowUnits")
    assert isinstance(raw_units, list)
    units: list[LongTaskUnitSpec] = []
    for index, raw_unit in enumerate(raw_units):
        if not isinstance(raw_unit, dict):
            raise ValueError("compiled screenplay workflow unit is invalid")
        unit_id = str(raw_unit.get("id") or "").strip()
        unit_kind = str(raw_unit.get("kind") or "").strip()
        raw_dependencies = raw_unit.get("dependsOn")
        if (
            not unit_id
            or unit_kind not in {
                "scene_generation",
                "continuity_review",
                "scene_revision",
                "finalize",
            }
            or not isinstance(raw_dependencies, list)
        ):
            raise ValueError("compiled screenplay workflow unit is invalid")
        units.append(LongTaskUnitSpec(
            id=unit_id,
            position=index,
            dependencies=tuple(str(item) for item in raw_dependencies),
            input_ref=(
                f"screenplay://{project_id}/draft-finalize"
                if unit_kind == "finalize"
                else f"screenplay://{project_id}/draft-unit/{unit_id}"
            ),
            max_attempts=3,
            metadata={
                "unitKind": unit_kind,
                "plannerStepId": str(
                    raw_unit.get("plannerStepId") or unit_id
                ),
                "agentRole": raw_unit.get("agentRole"),
                "label": raw_unit.get("label", ""),
                "sceneIds": raw_unit.get("sceneIds", []),
                "sceneHeadings": raw_unit.get("sceneHeadings", []),
                "dependencyReason": raw_unit.get("dependencyReason", ""),
            },
        ))
    return units


__all__ = ["ScreenplayLongTaskDispatcher"]
