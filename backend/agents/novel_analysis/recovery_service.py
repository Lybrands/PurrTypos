"""Replacement-only construction of a durable Novel Analysis continuation."""

from __future__ import annotations

from dataclasses import replace

from agents.novel_analysis.planner_contract import (
    SCALABLE_ANALYSIS_RECIPE_VERSION,
    ScalableAnalysisPlan,
    compile_scalable_analysis_recipe,
)
from agents.novel_analysis.scalable_profile import (
    _bind_recipe_to_planner_steps,
    _manifest_from_mapping,
    scalable_novel_analysis_implementation,
)
from agents.shared.implementation import AgentKind
from agents.shared.implementation_registry import AgentLifecycleAction
from agents.shared.saved_model_binding import (
    capture_saved_model_binding,
    resolve_saved_model_runtime,
)
from infrastructure.persistence.sqlite_run_repository import SqliteRunRepository
from infrastructure.persistence import run_store
from purra.api import DurableTaskContinuation
from purra.contracts import StepStatus
from purra.json_values import thaw_json_mapping
from purra.long_tasks import LongTaskStatus
from purra.run_recovery import RunRecoverySnapshot
from purra.task_admission import ExecutionMode, LongTaskDispatchReceipt, TaskAdmissionDecision


class NovelAnalysisReplacementRecoveryError(RuntimeError):
    code = "novel_analysis_replacement_recovery_invalid"


class NovelAnalysisReplacementContinuationLifecycle:
    def __init__(
        self,
        repository,
        *,
        task_id: str,
        recovery_source: str = "user",
    ) -> None:
        self._repository = repository
        self._task_id = task_id
        normalized_source = str(recovery_source or "").strip()
        if normalized_source not in {"user", "automatic"}:
            raise ValueError("Novel Analysis recovery source is invalid")
        self._recovery_source = normalized_source

    async def validate(self) -> None:
        task = await self._repository.load(self._task_id)
        if task is None or task.status is not LongTaskStatus.PAUSED:
            raise NovelAnalysisReplacementRecoveryError(
                "Novel Analysis task status does not allow this continuation"
            )

    async def before_submit(self) -> None:
        await self._repository.resume(
            self._task_id,
            additional_attempts=0,
            recovery_source=self._recovery_source,
        )

    async def on_run_started(self, run_id: str) -> None:
        # PurrA's dispatcher owns the authoritative continuation binding.
        del run_id

    async def on_run_finished(self, result) -> None:
        del result

    async def on_start_failed(self, code: str) -> None:
        task = await self._repository.load(self._task_id)
        if task is not None and task.status is LongTaskStatus.RUNNING:
            await self._repository.pause(
                self._task_id,
                reason_code=str(code or "continuation_start_failed"),
            )


class NovelAnalysisReplacementRecoveryService:
    def __init__(self, db, composition, *, entry_service) -> None:
        self._db = db
        self._composition = composition
        self._entry = entry_service
        self._tasks = composition.long_task_repository
        self._runs = SqliteRunRepository(db)

    async def resolve_automatic_runtime(self, task_id: str):
        task = await self._require_task(task_id)
        binding = thaw_json_mapping(task.metadata).get("runtimeBinding")
        if not isinstance(binding, dict):
            return None
        return await resolve_saved_model_runtime(self._db, binding)

    async def resume(
        self,
        *,
        task_id: str,
        run_command_id: str,
        runtime,
        signal,
        recovery_source: str = "user",
        prompt: str | None = None,
    ):
        task = await self._require_task(task_id)
        metadata = thaw_json_mapping(task.metadata)
        source_revision_id = str(metadata.get("sourceRevisionId") or "").strip()
        task_command_id = str(metadata.get("commandId") or "").strip()
        if not source_revision_id or not task_command_id:
            raise NovelAnalysisReplacementRecoveryError(
                "Scalable Novel Analysis task scope is incomplete"
            )
        await self._require_replacement_owner(task.created_by_run_id)
        source = await self._runs.get(task.created_by_run_id)
        source_record = await run_store.get_run(
            self._db,
            task.created_by_run_id,
        )
        continuation_prompt = str(
            prompt or (source_record or {}).get("prompt") or ""
        ).strip()
        if not continuation_prompt:
            raise NovelAnalysisReplacementRecoveryError(
                "Novel Analysis source Run has no recoverable prompt"
            )
        plan = source.execution_plan
        if plan is None or plan.task_spec is None:
            raise NovelAnalysisReplacementRecoveryError(
                "Novel Analysis source Run has no recoverable plan"
            )
        recipe = _bind_recipe_to_planner_steps(
            compile_scalable_analysis_recipe(
                manifest=_manifest_from_mapping(metadata.get("sliceManifest")),
                plan=ScalableAnalysisPlan.from_mapping(metadata.get("analysisPlan")),
            ),
            plan,
        )
        if (
            task.kind != recipe.kind
            or metadata.get("recipeDigest") != recipe.metadata["recipeDigest"]
            or int(metadata.get("recipeVersion") or 0)
            != SCALABLE_ANALYSIS_RECIPE_VERSION
        ):
            raise NovelAnalysisReplacementRecoveryError(
                "Novel Analysis persisted recipe no longer matches its source scope"
            )
        requested_binding = await capture_saved_model_binding(self._db, runtime)
        persisted_binding = metadata.get("runtimeBinding")
        if requested_binding is None or requested_binding != persisted_binding:
            raise NovelAnalysisReplacementRecoveryError(
                "Novel Analysis continuation model binding changed"
            )
        units = await self._tasks.list_units(task.id)
        completed = {unit.id for unit in units if unit.status.value == "completed"}
        continuation_plan = replace(plan, steps=tuple(
            replace(
                step,
                status=(
                    StepStatus.DONE
                    if all(
                        unit.id in completed
                        for unit in recipe.steps
                        if unit.plan_step_id == step.id
                    )
                    else StepStatus.PENDING
                ),
                result_summary=None,
                error=None,
            )
            for step in plan.steps
        ))
        model_kinds = {"map", "reduce", "synthesize", "review"}
        admission = TaskAdmissionDecision(
            mode=ExecutionMode.DURABLE,
            reason_code="novel_analysis_replacement_continuation",
            estimated_units=len(recipe.steps),
            estimated_model_calls=sum(
                1 for step in recipe.steps if step.kind in model_kinds
            ),
            covered_step_ids=tuple(continuation_plan.work_step_ids or ()),
            execution_recipe=recipe,
            metadata=metadata,
        )
        continuation = DurableTaskContinuation(
            source=RunRecoverySnapshot(
                run_id=source.run_id,
                status=source.status,
                execution_plan=continuation_plan,
                agent_preset_snapshot=source.agent_preset_snapshot,
            ),
            continuation_command=run_command_id,
            receipt=LongTaskDispatchReceipt(
                task_id=task.id,
                message="恢复 replacement 来源分析任务。",
                admission=admission,
            ),
        )
        lifecycle = NovelAnalysisReplacementContinuationLifecycle(
            self._tasks,
            task_id=task.id,
            recovery_source=recovery_source,
        )
        async for update in self._entry.continue_task(
            source_revision_id=source_revision_id,
            task_command_id=task_command_id,
            run_command_id=run_command_id,
            prompt=continuation_prompt,
            runtime=runtime,
            signal=signal,
            durable_continuation=continuation,
            run_binding_lifecycle=lifecycle,
            recovery_source=recovery_source,
        ):
            yield update

    async def _require_task(self, task_id: str):
        task = await self._tasks.load(task_id)
        if task is None:
            raise NovelAnalysisReplacementRecoveryError(
                "Novel Analysis replacement task does not exist"
            )
        return task

    async def _require_replacement_owner(self, run_id: str) -> None:
        route = await self._composition.agent_implementation_router.for_run(
            run_id,
            action=AgentLifecycleAction.RESUME,
            expected_agent_kind=AgentKind.NOVEL_ANALYSIS,
        )
        if route.identity != scalable_novel_analysis_implementation(
            recipe_version=SCALABLE_ANALYSIS_RECIPE_VERSION,
        ):
            raise NovelAnalysisReplacementRecoveryError(
                "Novel Analysis task belongs to a different implementation"
            )


__all__ = [
    "NovelAnalysisReplacementContinuationLifecycle",
    "NovelAnalysisReplacementRecoveryError",
    "NovelAnalysisReplacementRecoveryService",
]
