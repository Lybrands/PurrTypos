"""Application services for durable screenplay generation tasks."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

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
        task_metadata = {**metadata, "sessionId": session_id}
        if task_kind == SCREENPLAY_DRAFT_LONG_TASK_KIND:
            raw_units = metadata.get("executionUnits")
            if not isinstance(raw_units, list) or not raw_units:
                raise ValueError(
                    "durable screenplay task has no Planner execution graph"
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
        failed_same_scope = next((
            item for item in recent_tasks
            if item.status is LongTaskStatus.FAILED
            and _same_origin_session(item.metadata, session_id)
            and _same_task_scope(item.metadata, task_metadata)
        ), None)
        if failed_same_scope is not None:
            return await self._reuse_active_task(
                failed_same_scope,
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
        units = _task_units(task_kind, project_id, task_metadata, plan)
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
        message = (
            f"长篇正文任务已开始，共 {decision.estimated_units} 场、"
            f"{decision.estimated_model_calls} 个批次。"
            "本轮将持续显示批次执行、校验、重试与最终结果。"
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
        work_item = await self._work_items.get(task.work_item_id)
        await self._work_items.link_run(WorkItemRunLinkCommand(
            work_item_id=work_item.id,
            run_id=parent_run_id,
            relation=WorkItemRunRelation.REFERENCE,
            expected_revision=work_item.revision,
        ))
        same_scope = _same_task_scope(task.metadata, metadata)
        resumed = False
        if same_scope and task.status in {
            LongTaskStatus.PAUSED,
            LongTaskStatus.FAILED,
        }:
            task = await self._long_tasks.resume(task.id)
            resumed = True
        task_title = str(
            thaw_json_mapping(task.metadata).get("taskTitle")
            or "长篇正文"
        ).strip()
        if not same_scope:
            message = (
                f"当前项目已有一项未完成的{task_title}任务，未重复创建新任务。"
                "请先继续或取消现有任务。"
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
                "sameScope": same_scope,
                "taskTitle": task_title,
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
    plan: TaskPlan,
) -> list[LongTaskUnitSpec]:
    raw_units = metadata.get("executionUnits")
    assert isinstance(raw_units, list)
    units: list[LongTaskUnitSpec] = []
    for index, raw_unit in enumerate(raw_units):
        if not isinstance(raw_unit, dict):
            raise ValueError("Planner durable execution unit is invalid")
        unit_id = str(raw_unit.get("id") or "").strip()
        unit_kind = str(raw_unit.get("kind") or "").strip()
        raw_dependencies = raw_unit.get("dependsOn")
        if (
            not unit_id
            or unit_kind not in {
                "scene_generation",
                "continuity_review",
                "finalize",
            }
            or not isinstance(raw_dependencies, list)
        ):
            raise ValueError("Planner durable execution unit is invalid")
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
                "sceneIds": raw_unit.get("sceneIds", []),
                "sceneHeadings": raw_unit.get("sceneHeadings", []),
                "dependencyReason": raw_unit.get("dependencyReason", ""),
            },
        ))
    return units


__all__ = ["ScreenplayLongTaskDispatcher"]
