"""Start, recover, review, and publish durable source analyses."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import replace
from uuid import uuid4

from purra.api import (
    AgentCoreRunOptions,
    DurableTaskContinuation,
    RunRecoverySnapshot,
)
from purra.contracts import (
    AgentMessage,
    AgentRunRequest,
    MessageRole,
    PlanningMode,
    RunBinding,
    StepStatus,
)
from purra.long_tasks import LongTaskRunRelation
from purra.json_values import thaw_json_mapping
from purra.model_protocol import InvocationOutputLimit, resolve_invocation_output_limit
from purra.output import (
    PublicPresentationMode,
    ResponseTransactionMode,
    ResponseTransactionPolicy,
)
from purra.task_admission import (
    ExecutionMode,
    LongTaskDispatchReceipt,
    TaskAdmissionDecision,
)

from application.agent_cancellation_service import AgentCancellationService
from application.agent_run_service import AgentRunService
from application.model_runtime import (
    model_request_from_runtime,
    reasoning_mode_from_options,
    runtime_context_window_tokens,
)
from application.novel_analysis_artifacts import NovelAnalysisArtifactStore
from application.novel_analysis_executor import NovelAnalysisTaskUnitExecutor
from application.novel_analysis_source import NovelAnalysisSourceReader
from domains.novel_analysis import (
    NOVEL_ANALYSIS_ARTIFACT_KIND,
    NOVEL_ANALYSIS_ARTIFACT_REF_PREFIX,
    NOVEL_ANALYSIS_DOMAIN_NAMESPACE,
    NOVEL_ANALYSIS_REVIEW_ARTIFACT_KIND,
    NOVEL_ANALYSIS_SCHEMA_VERSION,
    NovelAnalysisDomainContext,
    NovelAnalysisSegment,
    canonical_digest,
    compile_novel_analysis_recipe,
    novel_analysis_model_call_count,
)
from exceptions import AppError, NotFoundError
from infrastructure.persistence.sqlite_long_task_repository import (
    SqliteLongTaskRepository,
)
from infrastructure.persistence.sqlite_run_repository import SqliteRunRepository


_ACTIVE_ANALYSES: dict[str, tuple[str, asyncio.Task[None]]] = {}


def _discard_active_analysis(
    source_revision_id: str,
    completed: asyncio.Task[None],
) -> None:
    active = _ACTIVE_ANALYSES.get(source_revision_id)
    if active is not None and active[1] is completed:
        _ACTIVE_ANALYSES.pop(source_revision_id, None)


class _NovelAnalysisContinuationLifecycle:
    """Start one continuation without leaving a resumed task orphaned."""

    def __init__(self, repository, *, task_id: str, retry_failed: bool) -> None:
        self._repository = repository
        self._task_id = task_id
        self._retry_failed = retry_failed

    async def validate(self) -> None:
        task = await self._repository.load(self._task_id)
        allowed = {"failed"} if self._retry_failed else {"paused"}
        if task is None or task.status.value not in allowed:
            raise AppError("来源分析任务状态不允许恢复", 409)

    async def before_submit(self) -> None:
        await self._repository.resume(
            self._task_id,
            additional_attempts=1 if self._retry_failed else 0,
        )

    async def on_run_started(self, run_id: str) -> None:
        await self._repository.bind_run(
            self._task_id,
            run_id,
            relation=LongTaskRunRelation.CONTINUATION,
        )

    async def on_run_finished(self, result) -> None:
        del result

    async def on_start_failed(self, code: str) -> None:
        task = await self._repository.load(self._task_id)
        if task is not None and task.status.value == "running":
            await self._repository.pause(
                self._task_id,
                reason_code=str(code or "continuation_start_failed"),
            )


class NovelAnalysisService:
    def __init__(self, db, composition=None) -> None:
        self._db = db
        self._composition = composition
        self._runs = AgentRunService(composition) if composition is not None else None
        self._source = NovelAnalysisSourceReader(db)
        self._artifacts = NovelAnalysisArtifactStore(db)
        self._long_tasks = SqliteLongTaskRepository(db)
        self._run_repository = SqliteRunRepository(db)
        self._cancellation = (
            AgentCancellationService(db, composition)
            if composition is not None else None
        )

    async def start(
        self,
        *,
        source_revision_id: str,
        command_id: str,
        prompt: str = "分析这部小说的全局故事概览、事实脉络和写作技法。",
        runtime,
    ) -> dict:
        sections = await self._source.list_bound_sections(source_revision_id)
        active = await self._long_tasks.find_active(
            namespace=NOVEL_ANALYSIS_DOMAIN_NAMESPACE,
            owner_id=source_revision_id,
            kind="novel_source_analysis",
        )
        if active is not None:
            if active.metadata.get("idempotencyKey") != command_id:
                raise AppError("已有来源分析尚未结束，请恢复或取消当前任务", 409)
            return {
                "status": "accepted",
                "sourceRevisionId": source_revision_id,
                "commandId": command_id,
                "sectionCount": len(sections),
                "dispatchActive": False,
            }
        task = self.dispatch(
            source_revision_id=source_revision_id,
            section_ids=tuple(str(row["id"]) for row in sections),
            task_idempotency_key=command_id,
            run_command_id=command_id,
            prompt=str(prompt or "").strip(),
            runtime=runtime,
            failed_resume_attempts=0,
        )
        return {
            "status": "accepted",
            "sourceRevisionId": source_revision_id,
            "commandId": command_id,
            "sectionCount": len(sections),
            "dispatchActive": not task.done(),
        }

    async def follow_up(
        self,
        *,
        source_revision_id: str,
        artifact_ref: str,
        prompt: str,
        command_id: str,
        runtime,
    ) -> dict:
        question = str(prompt or "").strip()
        if not question:
            raise AppError("请输入要追问的内容", 422)
        if len(question) > 20_000:
            raise AppError("追问内容过长", 422)
        active = await self._long_tasks.find_active(
            namespace=NOVEL_ANALYSIS_DOMAIN_NAMESPACE,
            owner_id=source_revision_id,
            kind="novel_source_analysis",
        )
        if active is not None:
            raise AppError("来源分析尚未结束，请先恢复或取消当前任务", 409)
        artifact = await self._artifacts.require(artifact_ref)
        if str(artifact.get("sourceRevisionId") or "") != source_revision_id:
            raise AppError("追问所用分析结果不属于当前来源版本", 409)
        sections = await self._source.list_bound_sections(source_revision_id)
        task = self._dispatch_follow_up(
            source_revision_id=source_revision_id,
            section_ids=tuple(str(row["id"]) for row in sections),
            artifact_ref=artifact_ref,
            prompt=question,
            command_id=command_id,
            runtime=runtime,
        )
        return {
            "status": "accepted",
            "sourceRevisionId": source_revision_id,
            "commandId": command_id,
            "dispatchActive": not task.done(),
        }

    def dispatch(
        self,
        *,
        source_revision_id: str,
        section_ids: tuple[str, ...],
        task_idempotency_key: str,
        run_command_id: str,
        prompt: str,
        runtime,
        failed_resume_attempts: int,
        segments: tuple[NovelAnalysisSegment, ...] = (),
        input_token_budget: int = 0,
        durable_continuation: DurableTaskContinuation | None = None,
        run_binding_lifecycle=None,
    ) -> asyncio.Task[None]:
        if self._composition is None or self._runs is None:
            raise RuntimeError("novel analysis Agent composition is required")
        key = str(run_command_id or "").strip()
        active = _ACTIVE_ANALYSES.get(source_revision_id)
        if active is not None and not active[1].done():
            if active[0] == key:
                return active[1]
            raise AppError("已有来源分析正在启动，请稍后再试", 409)
        task = asyncio.create_task(self._execute(
            source_revision_id=source_revision_id,
            section_ids=section_ids,
            task_idempotency_key=task_idempotency_key,
            run_command_id=run_command_id,
            prompt=prompt,
            runtime=runtime,
            failed_resume_attempts=failed_resume_attempts,
            segments=segments,
            input_token_budget=input_token_budget,
            durable_continuation=durable_continuation,
            run_binding_lifecycle=run_binding_lifecycle,
        ))
        _ACTIVE_ANALYSES[source_revision_id] = (key, task)
        track = getattr(self._composition, "track_background_run", None)
        if callable(track):
            track(task)
        task.add_done_callback(
            lambda completed: _discard_active_analysis(
                source_revision_id, completed
            )
        )
        return task

    def _dispatch_follow_up(
        self,
        *,
        source_revision_id: str,
        section_ids: tuple[str, ...],
        artifact_ref: str,
        prompt: str,
        command_id: str,
        runtime,
    ) -> asyncio.Task[None]:
        if self._composition is None or self._runs is None:
            raise RuntimeError("novel analysis Agent composition is required")
        active = _ACTIVE_ANALYSES.get(source_revision_id)
        if active is not None and not active[1].done():
            if active[0] == command_id:
                return active[1]
            raise AppError("已有来源分析交互正在进行，请稍后再试", 409)
        task = asyncio.create_task(self._execute_follow_up(
            source_revision_id=source_revision_id,
            section_ids=section_ids,
            artifact_ref=artifact_ref,
            prompt=prompt,
            command_id=command_id,
            runtime=runtime,
        ))
        _ACTIVE_ANALYSES[source_revision_id] = (command_id, task)
        track = getattr(self._composition, "track_background_run", None)
        if callable(track):
            track(task)
        task.add_done_callback(
            lambda completed: _discard_active_analysis(
                source_revision_id, completed
            )
        )
        return task

    async def _execute(
        self,
        *,
        source_revision_id: str,
        section_ids: tuple[str, ...],
        task_idempotency_key: str,
        run_command_id: str,
        prompt: str,
        runtime,
        failed_resume_attempts: int,
        segments: tuple[NovelAnalysisSegment, ...],
        input_token_budget: int,
        durable_continuation: DurableTaskContinuation | None,
        run_binding_lifecycle,
    ) -> None:
        context = NovelAnalysisDomainContext(
            source_revision_id=source_revision_id,
            command_id=task_idempotency_key,
            section_ids=section_ids,
            segments=segments,
            input_token_budget=input_token_budget,
        )
        model_request = model_request_from_runtime(runtime)
        window = runtime_context_window_tokens(runtime)
        output_limit = resolve_invocation_output_limit(
            model_request.capability_snapshot,
            model_request.options.get("max_tokens"),
        )
        if output_limit.max_tokens >= window:
            output_limit = InvocationOutputLimit(
                max_tokens=max(1_024, window // 4),
                source=output_limit.source,
                profile_max_tokens=output_limit.profile_max_tokens,
            )
        request = AgentRunRequest(
            messages=(AgentMessage(
                role=MessageRole.USER,
                content=(
                    prompt or "分析这部小说的全局故事概览、事实脉络和写作技法。"
                ),
            ),),
            model=model_request,
            domain_context=context.to_core_context(),
            mode="novel_source_analysis",
            context_window=window,
            tools_enabled=False,
            planning_mode=PlanningMode.PLANNED,
            metadata={
                "failedResumeAttempts": failed_resume_attempts,
            },
        )
        try:
            async for _update in self._runs.run(
                request=request,
                api_key=runtime.apiKey.get_secret_value(),
                options=AgentCoreRunOptions(
                    turn_id=f"novel-analysis:{run_command_id}",
                    output_limit=output_limit,
                    default_context_window_tokens=window,
                    force_planned_tool_choice=False,
                    require_tool_call=False,
                    reasoning_mode=reasoning_mode_from_options(runtime.options),
                    binding=RunBinding(
                        namespace="novel_source_analysis",
                        aggregate_id=source_revision_id,
                        command_id=run_command_id,
                        attributes={
                            "taskIdempotencyKey": task_idempotency_key,
                        },
                    ),
                    response_transaction_policy=ResponseTransactionPolicy(
                        mode=ResponseTransactionMode.DIRECT_LIVE,
                        public_presentation=PublicPresentationMode.NONE,
                    ),
                    durable_continuation=durable_continuation,
                ),
                signal=asyncio.Event(),
                run_binding_lifecycle=run_binding_lifecycle,
                long_task_executor=NovelAnalysisTaskUnitExecutor(
                    self._db,
                    composition=self._composition,
                    runtime=runtime,
                ),
            ):
                pass
        except asyncio.CancelledError:
            raise
        except Exception:
            # The Run and LongTask repositories are the authoritative failure
            # record. Avoid a parallel application-only status store.
            return

    async def _execute_follow_up(
        self,
        *,
        source_revision_id: str,
        section_ids: tuple[str, ...],
        artifact_ref: str,
        prompt: str,
        command_id: str,
        runtime,
    ) -> None:
        context = NovelAnalysisDomainContext(
            source_revision_id=source_revision_id,
            command_id=command_id,
            section_ids=section_ids,
            interaction_kind="follow_up",
            analysis_artifact_ref=artifact_ref,
        )
        model_request = model_request_from_runtime(runtime)
        window = runtime_context_window_tokens(runtime)
        output_limit = resolve_invocation_output_limit(
            model_request.capability_snapshot,
            model_request.options.get("max_tokens"),
        )
        if output_limit.max_tokens >= window:
            output_limit = InvocationOutputLimit(
                max_tokens=max(1_024, window // 4),
                source=output_limit.source,
                profile_max_tokens=output_limit.profile_max_tokens,
            )
        request = AgentRunRequest(
            messages=(AgentMessage(role=MessageRole.USER, content=prompt),),
            model=model_request,
            domain_context=context.to_core_context(),
            mode="novel_source_analysis_follow_up",
            context_window=window,
            tools_enabled=False,
            planning_mode=PlanningMode.REACTIVE,
        )
        try:
            async for _update in self._runs.run(
                request=request,
                api_key=runtime.apiKey.get_secret_value(),
                options=AgentCoreRunOptions(
                    turn_id=f"novel-analysis-follow-up:{command_id}",
                    output_limit=output_limit,
                    default_context_window_tokens=window,
                    force_planned_tool_choice=False,
                    require_tool_call=False,
                    reasoning_mode=reasoning_mode_from_options(runtime.options),
                    binding=RunBinding(
                        namespace="novel_source_analysis",
                        aggregate_id=source_revision_id,
                        command_id=command_id,
                        attributes={
                            "interactionKind": "follow_up",
                            "analysisArtifactRef": artifact_ref,
                        },
                    ),
                    response_transaction_policy=ResponseTransactionPolicy(
                        mode=ResponseTransactionMode.DIRECT_LIVE,
                        public_presentation=PublicPresentationMode.NONE,
                    ),
                ),
                signal=asyncio.Event(),
            ):
                pass
        except asyncio.CancelledError:
            raise
        except Exception:
            return

    async def list_for_revision(self, source_revision_id: str) -> list[dict]:
        rows = await self._db.fetch_all(
            "SELECT r.id AS run_id, r.status AS run_status, r.binding_command_id, "
            "r.binding_attributes_json, r.prompt, r.final_response, r.error, "
            "r.create_time, r.update_time, "
            "r.provider_output_events, "
            "t.id AS task_id, t.status AS task_status, t.revision AS task_revision, "
            "t.total_units, t.completed_units, t.failed_units, "
            "t.metadata_json AS task_metadata_json "
            "FROM ai_agent_runs AS r "
            "LEFT JOIN ai_agent_long_task_runs AS ltr ON ltr.run_id = r.id "
            "LEFT JOIN ai_agent_long_tasks AS t ON t.id = ltr.task_id "
            "WHERE r.binding_namespace = 'novel_source_analysis' "
            "AND r.binding_aggregate_id = ? "
            "ORDER BY CASE WHEN t.status IN ('pending', 'running', 'paused') "
            "THEN 0 ELSE 1 END, r.create_time DESC, r.rowid DESC LIMIT 1",
            [source_revision_id],
        )
        results: list[dict] = []
        seen_tasks: set[str] = set()
        for row in rows:
            try:
                binding_attributes = json.loads(
                    str(row.get("binding_attributes_json") or "{}")
                )
            except (TypeError, ValueError):
                binding_attributes = {}
            task_id = str(row.get("task_id") or "")
            key = task_id or str(row["run_id"])
            if key in seen_tasks:
                continue
            seen_tasks.add(key)
            final = None
            units = ()
            analysis_plan = None
            related_runs = []
            provider_output_events = int(row.get("provider_output_events") or 0)
            if task_id:
                # This is a read projection, so do not hydrate the executable
                # LongTaskRecord merely to show its plan. Resume paths still
                # load the record and enforce the current budget contract.
                try:
                    task_metadata = json.loads(
                        str(row.get("task_metadata_json") or "{}")
                    )
                except (TypeError, ValueError):
                    task_metadata = {}
                if not isinstance(task_metadata, Mapping):
                    task_metadata = {}
                raw_plan = task_metadata.get("analysisPlan")
                if isinstance(raw_plan, Mapping):
                    analysis_plan = thaw_json_mapping(raw_plan)
                units = await self._long_tasks.list_units(task_id)
                task_runs = await self._list_task_runs(task_id)
                related_runs = [
                    {"runId": item["id"], "status": item["status"]}
                    for item in task_runs if item["id"] != row["run_id"]
                ]
                provider_output_events = sum(
                    int(item.get("provider_output_events") or 0)
                    for item in task_runs
                )
                unit = await self._db.fetch_one(
                    "SELECT output_ref FROM ai_agent_long_task_units "
                    "WHERE task_id = ? AND unit_id = 'artifact:review' "
                    "AND status = 'completed'",
                    [task_id],
                )
                final = str((unit or {}).get("output_ref") or "") or None
            unit_views = []
            for unit in units:
                unit_views.append({
                    "unitId": unit.id,
                    "title": str(unit.metadata.get("displayTitle") or unit.id),
                    "kind": str(unit.metadata.get("unitKind") or ""),
                    **(
                        {"plannerStepId": str(unit.metadata["plannerStepId"])}
                        if unit.metadata.get("plannerStepId") else {}
                    ),
                    "status": unit.status.value,
                    "attempt": unit.attempt,
                    "maxAttempts": unit.max_attempts,
                    "errorCode": unit.error_code,
                    "updateTime": unit.update_time,
                })
            results.append({
                "runId": str(row["run_id"]),
                "runStatus": str(row["run_status"]),
                "commandId": str(row.get("binding_command_id") or ""),
                "interactionKind": str(
                    binding_attributes.get("interactionKind") or "analysis"
                ),
                "analysisArtifactRef": (
                    str(binding_attributes.get("analysisArtifactRef") or "")
                    or None
                ),
                "prompt": str(row.get("prompt") or ""),
                "finalResponse": (
                    str(row.get("final_response") or "")
                    if binding_attributes.get("interactionKind") == "follow_up"
                    else ""
                ),
                "taskId": task_id or None,
                "taskStatus": str(row.get("task_status") or "") or None,
                "taskRevision": row.get("task_revision"),
                "totalUnits": int(row.get("total_units") or 0),
                "completedUnits": int(row.get("completed_units") or 0),
                "failedUnits": int(row.get("failed_units") or 0),
                "error": row.get("error"),
                "artifactRef": final,
                "providerOutputEvents": provider_output_events,
                "relatedRuns": related_runs,
                "analysisPlan": analysis_plan,
                "units": unit_views,
                "createTime": row.get("create_time"),
                "updateTime": row.get("update_time"),
            })
        return results

    async def get_artifact(self, reference: str) -> dict:
        return await self._artifacts.require(reference)

    async def _list_task_runs(self, task_id: str) -> list[dict]:
        # Durable unit Runs are recorded on units (including retry history),
        # not in the task's root/continuation Run binding table.
        return await self._db.fetch_all(
            "SELECT r.id, r.status, r.provider_output_events FROM ai_agent_runs AS r "
            "WHERE r.id IN ("
            "SELECT run_id FROM ai_agent_long_task_runs WHERE task_id = ? "
            "UNION SELECT run_id FROM ai_agent_long_task_units WHERE task_id = ? "
            "UNION SELECT json_extract(history.value, '$.runId') "
            "FROM ai_agent_long_task_units AS unit, "
            "json_each(unit.metadata_json, '$.runHistory') AS history "
            "WHERE unit.task_id = ?) "
            "AND r.binding_namespace IN ('novel_source_analysis', 'novel_source_analysis.unit') "
            "ORDER BY r.rowid",
            [task_id, task_id, task_id],
        )

    async def pause(self, task_id: str, expected_revision: int | None = None):
        try:
            task = await self._long_tasks.pause(
                task_id,
                expected_revision=expected_revision,
                reason_code="user_paused_novel_analysis",
            )
        except (LookupError, ValueError) as error:
            raise AppError(str(error), 409) from error
        return _task_mapping(task)

    async def cancel(self, task_id: str):
        task = await self._long_tasks.load(task_id)
        if task is None:
            raise NotFoundError("来源分析任务不存在")
        if not task.status.terminal:
            task = await self._long_tasks.cancel(task.id)
        runs = await self._list_task_runs(task.id)
        if self._cancellation is not None:
            for run in runs:
                if run["status"] != "running":
                    continue
                with suppress(Exception):
                    await self._cancellation.cancel(run["id"])
        return _task_mapping(task)

    async def resume(
        self,
        *,
        task_id: str,
        run_command_id: str,
        runtime,
        retry_failed: bool,
    ) -> dict:
        task = await self._long_tasks.load(task_id)
        if task is None or task.namespace != NOVEL_ANALYSIS_DOMAIN_NAMESPACE:
            raise NotFoundError("来源分析任务不存在")
        if task.status.value not in ({"failed"} if retry_failed else {"paused"}):
            raise AppError("来源分析任务状态不允许恢复", 409)
        metadata = dict(task.metadata)
        source = await self._run_repository.get(task.created_by_run_id)
        plan = source.execution_plan
        if plan is None or plan.task_spec is None:
            raise AppError("来源分析缺少可恢复的冻结计划", 409)
        plan = replace(
            plan,
            steps=tuple(
                step if step.status is StepStatus.DONE else replace(
                    step,
                    status=StepStatus.PENDING,
                    result_summary=None,
                    error=None,
                )
                for step in plan.steps
            ),
        )
        raw_segments = metadata.get("segments") or ()
        segments = tuple(
            NovelAnalysisSegment.from_mapping(item)
            for item in raw_segments
            if isinstance(item, Mapping)
        )
        recipe = compile_novel_analysis_recipe(
            section_ids=tuple(metadata["sectionIds"]),
            segments=segments,
            plan_step_ids=tuple(step.id for step in plan.steps),
        )
        model_call_count = novel_analysis_model_call_count(recipe)
        admission = TaskAdmissionDecision(
            mode=ExecutionMode.DURABLE,
            reason_code="novel_analysis_durable_continuation",
            estimated_units=len(recipe.steps),
            estimated_model_calls=model_call_count,
            covered_step_ids=tuple(step.id for step in plan.steps),
            execution_recipe=recipe,
        )
        continuation = DurableTaskContinuation(
            source=RunRecoverySnapshot(
                run_id=source.run_id,
                status=source.status,
                execution_plan=plan,
                agent_preset_snapshot=source.agent_preset_snapshot,
            ),
            continuation_command=run_command_id,
            receipt=LongTaskDispatchReceipt(
                task_id=task.id,
                message="恢复已冻结的来源分析任务。",
                admission=admission,
            ),
        )
        lifecycle = _NovelAnalysisContinuationLifecycle(
            self._long_tasks,
            task_id=task.id,
            retry_failed=retry_failed,
        )
        self.dispatch(
            source_revision_id=str(metadata["sourceRevisionId"]),
            section_ids=tuple(metadata["sectionIds"]),
            task_idempotency_key=str(metadata["idempotencyKey"]),
            run_command_id=run_command_id,
            prompt=str(
                metadata.get("prompt")
                or "分析这部小说的全局故事概览、事实脉络和写作技法。"
            ),
            runtime=runtime,
            failed_resume_attempts=1 if retry_failed else 0,
            segments=segments,
            input_token_budget=int(metadata.get("inputTokenBudget") or 0),
            durable_continuation=continuation,
            run_binding_lifecycle=lifecycle,
        )
        return {
            "status": "accepted",
            "taskId": task.id,
            "commandId": run_command_id,
        }

    async def review(
        self,
        *,
        artifact_ref: str,
        command_id: str,
        payload: Mapping[str, object],
    ) -> dict:
        source = await self._artifacts.require(artifact_ref)
        if source["artifactKind"] not in {
            NOVEL_ANALYSIS_ARTIFACT_KIND,
            NOVEL_ANALYSIS_REVIEW_ARTIFACT_KIND,
        }:
            raise AppError("不是可审核的来源分析 Artifact", 409)
        corrected = {
            "analysisSchemaVersion": NOVEL_ANALYSIS_SCHEMA_VERSION,
            "sourceRevisionId": str(source["sourceRevisionId"]),
            "sectionIds": list(source["sectionIds"]),
            "facts": list(payload.get("facts") or ()),
            "craftCards": list(payload.get("craftCards") or ()),
            **(
                {"storyOverview": dict(payload["storyOverview"])}
                if isinstance(payload.get("storyOverview"), Mapping)
                else {
                    "storyOverview": dict(source["storyOverview"])
                }
                if isinstance(source.get("storyOverview"), Mapping)
                else {}
            ),
            "coverage": dict(source.get("coverage") or {}),
            "conflicts": list(source.get("conflicts") or ()),
            "reviewStatus": "reviewed",
        }
        validated = await self._validated_publish_payload(corrected)
        return await self._artifacts.write(
            namespace=NOVEL_ANALYSIS_DOMAIN_NAMESPACE,
            kind=NOVEL_ANALYSIS_REVIEW_ARTIFACT_KIND,
            owner_id=str(source["sourceRevisionId"]),
            owner_ref_kind="analysis_review_command",
            owner_ref_id=command_id,
            run_id=str(source["createdByRunId"]),
            semantic_key="user-reviewed-analysis",
            payload=validated,
            metadata={
                "sourceArtifactId": source["artifactId"],
                "taskId": source.get("metadata", {}).get("taskId"),
            },
        )

    async def publish(self, artifact_ref: str) -> dict:
        artifact = await self._artifacts.require(artifact_ref)
        if artifact["artifactKind"] not in {
            NOVEL_ANALYSIS_ARTIFACT_KIND,
            NOVEL_ANALYSIS_REVIEW_ARTIFACT_KIND,
        }:
            raise AppError("不是可发布的来源分析 Artifact", 409)
        task_id = str(artifact.get("metadata", {}).get("taskId") or "")
        if task_id:
            task = await self._long_tasks.load(task_id)
            if task is None or task.status.value != "completed":
                raise AppError("未完成的来源分析 Artifact 不能发布", 409)
        payload = await self._validated_publish_payload(artifact)
        return await self._publish_transaction(
            payload,
            artifact_id=str(artifact["artifactId"]),
        )

    async def _validated_publish_payload(self, value: Mapping[str, object]):
        revision_id = str(value.get("sourceRevisionId") or "")
        section_ids = tuple(value.get("sectionIds") or ())
        await self._source.list_bound_sections(revision_id, section_ids)
        facts = []
        cards = []
        for key, target in (("facts", facts), ("craftCards", cards)):
            raw_items = value.get(key)
            if not isinstance(raw_items, (list, tuple)):
                raise AppError(f"{key} 必须是数组", 422)
            for raw in raw_items:
                if not isinstance(raw, Mapping):
                    raise AppError(f"{key} 条目无效", 422)
                item = dict(raw)
                evidence = []
                for raw_evidence in item.get("evidence") or ():
                    if not isinstance(raw_evidence, Mapping):
                        raise AppError("分析证据无效", 422)
                    evidence.append(await self._source.validate_excerpt(
                        source_revision_id=revision_id,
                        bound_section_ids=section_ids,
                        section_id=str(raw_evidence.get("sectionId") or ""),
                        excerpt=str(raw_evidence.get("excerpt") or ""),
                        start_character=_optional_int(
                            raw_evidence.get("segmentStartCharacter")
                        ),
                        end_character=_optional_int(
                            raw_evidence.get("segmentEndCharacter")
                        ),
                    ))
                if not evidence:
                    raise AppError("每条事实和技法卡必须至少有一条证据", 422)
                item["evidence"] = evidence
                item["contentDigest"] = canonical_digest({
                    name: item_value
                    for name, item_value in item.items()
                    if name != "contentDigest"
                })
                target.append(item)
        overview = value.get("storyOverview")
        validated_overview = None
        if isinstance(overview, Mapping):
            summary_markdown = str(overview.get("summaryMarkdown") or "").strip()
            if not summary_markdown:
                raise AppError("故事概览不能为空", 422)
            overview_evidence = []
            for raw_evidence in overview.get("evidence") or ():
                if not isinstance(raw_evidence, Mapping):
                    raise AppError("故事概览证据无效", 422)
                overview_evidence.append(await self._source.validate_excerpt(
                    source_revision_id=revision_id,
                    bound_section_ids=section_ids,
                    section_id=str(raw_evidence.get("sectionId") or ""),
                    excerpt=str(raw_evidence.get("excerpt") or ""),
                    start_character=_optional_int(
                        raw_evidence.get("segmentStartCharacter")
                    ),
                    end_character=_optional_int(
                        raw_evidence.get("segmentEndCharacter")
                    ),
                ))
            if not overview_evidence:
                raise AppError("故事概览至少需要一条原文证据", 422)
            validated_overview = {
                "summaryMarkdown": summary_markdown,
                "evidence": overview_evidence,
            }
            validated_overview["contentDigest"] = canonical_digest(
                validated_overview
            )
        return {
            "analysisSchemaVersion": NOVEL_ANALYSIS_SCHEMA_VERSION,
            "sourceRevisionId": revision_id,
            "sectionIds": list(section_ids),
            "facts": facts,
            "craftCards": cards,
            **({"storyOverview": validated_overview} if validated_overview else {}),
            "coverage": dict(value.get("coverage") or {}),
            "conflicts": list(value.get("conflicts") or ()),
            "reviewStatus": str(value.get("reviewStatus") or "pending"),
        }

    async def _publish_transaction(self, payload: Mapping[str, object], *, artifact_id: str):
        revision_id = str(payload["sourceRevisionId"])
        digest = canonical_digest(payload)
        async with self._db.transaction(cancellation_linearizable=True):
            existing = await self._db.fetch_one(
                "SELECT * FROM novel_source_analyses WHERE source_revision_id = ? "
                "AND content_digest = ?",
                [revision_id, digest],
            )
            if existing is not None:
                return await self._analysis_mapping(str(existing["id"]))
            latest = await self._db.fetch_one(
                "SELECT COALESCE(MAX(version_no), 0) AS version_no "
                "FROM novel_source_analyses WHERE source_revision_id = ?",
                [revision_id],
            )
            section_rows = await self._source.list_bound_sections(
                revision_id,
                tuple(payload["sectionIds"]),
            )
            analysis_id = f"analysis_{uuid4().hex}"
            await self._db.execute(
                "INSERT INTO novel_source_analyses "
                "(id, source_revision_id, version_no, coverage_end_ordinal, "
                "schema_version, content_digest, summary_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    analysis_id,
                    revision_id,
                    int((latest or {}).get("version_no") or 0) + 1,
                    max(int(row["ordinal"]) for row in section_rows),
                    NOVEL_ANALYSIS_SCHEMA_VERSION,
                    digest,
                    json.dumps({
                        "artifactId": artifact_id,
                        "coverage": payload["coverage"],
                        "conflicts": payload["conflicts"],
                        **(
                            {"storyOverview": payload["storyOverview"]}
                            if isinstance(payload.get("storyOverview"), Mapping)
                            else {}
                        ),
                    }, ensure_ascii=False, separators=(",", ":")),
                ],
            )
            for fact in payload["facts"]:
                fact_id = f"fact_{uuid4().hex}"
                ordinals = [int(item["sectionOrdinal"]) for item in fact["evidence"]]
                await self._db.execute(
                    "INSERT INTO novel_source_analysis_facts "
                    "(id, analysis_id, fact_kind, subject_key, predicate, value_json, "
                    "lifecycle_status, first_section_ordinal, last_section_ordinal, "
                    "content_digest) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        fact_id,
                        analysis_id,
                        str(fact.get("factKind") or ""),
                        str(fact.get("subjectKey") or ""),
                        str(fact.get("predicate") or ""),
                        json.dumps(fact.get("value"), ensure_ascii=False, separators=(",", ":")),
                        str(fact.get("lifecycleStatus") or "active"),
                        min(ordinals),
                        max(ordinals),
                        str(fact["contentDigest"]),
                    ],
                )
                await self._insert_evidence(
                    analysis_id, "fact", fact_id, fact["evidence"]
                )
            for card in payload["craftCards"]:
                card_id = f"craft_{uuid4().hex}"
                await self._db.execute(
                    "INSERT INTO novel_source_craft_cards "
                    "(id, analysis_id, card_kind, title, body_markdown, metadata_json, "
                    "status, content_digest) VALUES (?, ?, ?, ?, ?, ?, 'verified', ?)",
                    [
                        card_id,
                        analysis_id,
                        str(card.get("cardKind") or ""),
                        str(card.get("title") or ""),
                        str(card.get("bodyMarkdown") or ""),
                        "{}",
                        str(card["contentDigest"]),
                    ],
                )
                await self._insert_evidence(
                    analysis_id, "craft_card", card_id, card["evidence"]
                )
        return await self._analysis_mapping(analysis_id)

    async def _insert_evidence(self, analysis_id, owner_type, owner_id, evidence):
        for item in evidence:
            await self._db.execute(
                "INSERT INTO novel_source_analysis_evidence "
                "(id, analysis_id, owner_type, owner_id, section_id, excerpt, "
                "locator_json, excerpt_digest) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    f"evidence_{uuid4().hex}",
                    analysis_id,
                    owner_type,
                    owner_id,
                    item["sectionId"],
                    item["excerpt"],
                    json.dumps(item["locator"], separators=(",", ":")),
                    item["excerptDigest"],
                ],
            )

    async def list_published(self, revision_id: str):
        rows = await self._db.fetch_all(
            "SELECT id FROM novel_source_analyses WHERE source_revision_id = ? "
            "ORDER BY version_no DESC LIMIT 1",
            [revision_id],
        )
        return [await self._analysis_mapping(str(row["id"])) for row in rows]

    async def get_published(self, analysis_id: str):
        row = await self._db.fetch_one(
            "SELECT id FROM novel_source_analyses WHERE id = ?",
            [analysis_id],
        )
        if row is None:
            raise NotFoundError("正式来源分析不存在")
        return await self._analysis_mapping(analysis_id)

    async def _analysis_mapping(self, analysis_id: str):
        analysis = await self._db.fetch_one(
            "SELECT * FROM novel_source_analyses WHERE id = ?",
            [analysis_id],
        )
        facts = await self._db.fetch_all(
            "SELECT * FROM novel_source_analysis_facts WHERE analysis_id = ? ORDER BY id",
            [analysis_id],
        )
        cards = await self._db.fetch_all(
            "SELECT * FROM novel_source_craft_cards WHERE analysis_id = ? ORDER BY id",
            [analysis_id],
        )
        evidence = await self._db.fetch_all(
            "SELECT e.*, s.ordinal AS section_ordinal, s.title AS section_title "
            "FROM novel_source_analysis_evidence AS e "
            "LEFT JOIN novel_source_sections AS s ON s.id = e.section_id "
            "WHERE e.analysis_id = ? ORDER BY e.id",
            [analysis_id],
        )
        by_owner: dict[tuple[str, str], list[dict]] = {}
        for item in evidence:
            by_owner.setdefault(
                (str(item["owner_type"]), str(item["owner_id"])), []
            ).append({
                "id": item["id"],
                "sectionId": item["section_id"],
                "sectionOrdinal": item.get("section_ordinal"),
                "sectionTitle": item.get("section_title"),
                "excerpt": item["excerpt"],
                "locator": json.loads(str(item["locator_json"])),
                "excerptDigest": item["excerpt_digest"],
            })
        summary = json.loads(str(analysis["summary_json"]))
        return {
            "id": analysis["id"],
            "sourceRevisionId": analysis["source_revision_id"],
            "versionNo": analysis["version_no"],
            "coverageEndOrdinal": analysis["coverage_end_ordinal"],
            "schemaVersion": analysis["schema_version"],
            "contentDigest": analysis["content_digest"],
            "summary": summary,
            "storyOverview": summary.get("storyOverview"),
            "facts": [{
                "id": item["id"],
                "factKind": item["fact_kind"],
                "subjectKey": item["subject_key"],
                "predicate": item["predicate"],
                "value": json.loads(str(item["value_json"])),
                "lifecycleStatus": item["lifecycle_status"],
                "firstSectionOrdinal": item["first_section_ordinal"],
                "lastSectionOrdinal": item["last_section_ordinal"],
                "contentDigest": item["content_digest"],
                "evidence": by_owner.get(("fact", str(item["id"])), []),
            } for item in facts],
            "craftCards": [{
                "id": item["id"],
                "cardKind": item["card_kind"],
                "title": item["title"],
                "bodyMarkdown": item["body_markdown"],
                "status": item["status"],
                "contentDigest": item["content_digest"],
                "evidence": by_owner.get(("craft_card", str(item["id"])), []),
            } for item in cards],
            "createTime": analysis["create_time"],
        }


def _task_mapping(task) -> dict:
    return {
        "taskId": task.id,
        "status": task.status.value,
        "revision": task.revision,
        "totalUnits": task.total_units,
        "completedUnits": task.completed_units,
        "failedUnits": task.failed_units,
    }


def _optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


__all__ = ["NovelAnalysisService"]
