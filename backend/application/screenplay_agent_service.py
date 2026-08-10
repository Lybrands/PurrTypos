"""Application service for the screenplay Agent on PurrA durable tasks.

The model Planner owns semantic interpretation. The host validates that intent
against project truth, compiles a product recipe, and delegates durable
execution to PurrA.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any, Protocol

from purra.contracts import (
    AgentMessage,
    AgentRunRequest,
    DomainContext,
    MessageRole,
    StepExecutor,
    StepType,
    TaskPlan,
    TaskSpec,
    TaskStep,
)
from purra.errors import ModelGatewayError
from purra.long_tasks import (
    DurableExecutorRegistry,
    DurableTaskDescriptor,
    RecipeLongTaskDispatcher,
)
from purra.task_admission import (
    ExecutionMode,
    LongTaskExecutionUpdate,
    LongTaskExecutionStatus,
    TaskAdmissionDecision,
)
from application.model_runtime import model_request_from_runtime
from application.request_mapping import context_window_tokens
from domains.screenplay_agent import (
    ScreenplayIntent,
    ScreenplayIntentAction,
    ScreenplayOperationCreateCommand,
)
from application.screenplay_manifest_compiler import (
    compile_screenplay_manifest,
)
from application.screenplay_candidate_assembler import ScreenplayCandidateAssembler
from application.screenplay_part_artifacts import ScreenplayPartArtifactQuery
from exceptions import NotFoundError
from infrastructure.persistence.sqlite_long_task_repository import (
    SqliteLongTaskRepository,
)
from infrastructure.persistence.sqlite_screenplay_agent_repository import (
    SqliteScreenplayAgentRepository,
)
from infrastructure.persistence.sqlite_screenplay_operation_repository import (
    SqliteScreenplayOperationRepository,
)
from infrastructure.persistence.sqlite_screenplay_operation_finalizer import (
    ScreenplayOperationFinalizationCommand,
    SqliteScreenplayOperationFinalizer,
)
from infrastructure.persistence.sqlite_work_item_repository import (
    SqliteWorkItemRepository,
)
from application.screenplay_agent_stream import ScreenplayAgentChunkProjector


_ACTIVE_TASKS: dict[str, asyncio.Task[None]] = {}


@dataclass(frozen=True, slots=True)
class PlannedScreenplayIntent:
    intent: ScreenplayIntent
    run_id: str | None = None


@dataclass(frozen=True, slots=True)
class ResolvedScreenplayTask:
    target_role: str
    episode_numbers: tuple[int, ...] = ()
    base_revision_id: str | None = None
    episode_scene_ids: Mapping[int, tuple[str, ...]] = field(default_factory=dict)
    source_revision_refs: tuple[str, ...] = ()
    reviewed_draft_id: str | None = None
    document_sections: tuple[str, ...] = ()


class ScreenplayIntentPlanner(Protocol):
    async def plan(
        self,
        *,
        workspace: Mapping[str, Any],
        history: Sequence[Mapping[str, str]],
        user_content: str,
        runtime,
        session_id: int,
        turn_id: str,
    ) -> PlannedScreenplayIntent: ...


class ScreenplayTaskResolver(Protocol):
    async def resolve(
        self,
        *,
        workspace: Mapping[str, Any],
        intent: ScreenplayIntent,
    ) -> ResolvedScreenplayTask: ...


class ScreenplayAgentService:
    def __init__(
        self,
        db,
        *,
        owner_id: str,
        planner: ScreenplayIntentPlanner,
        resolver: ScreenplayTaskResolver,
        unit_executor_factory: Callable[[Any], object],
        projects,
        track_background=None,
    ) -> None:
        self._repository = SqliteScreenplayAgentRepository(
            db,
            owner_id=owner_id,
        )
        self._owner_id = str(owner_id)
        self._stream = ScreenplayAgentChunkProjector(db)
        self._planner = planner
        self._resolver = resolver
        self._unit_executor_factory = unit_executor_factory
        self._projects = projects
        self._track_background = track_background
        self._long_tasks = SqliteLongTaskRepository(db)
        self._operations = SqliteScreenplayOperationRepository(db)
        self._work_items = SqliteWorkItemRepository(db)
        self._parts = ScreenplayPartArtifactQuery(db)
        self._finalizer = SqliteScreenplayOperationFinalizer(
            db,
            candidate_assembler=ScreenplayCandidateAssembler(db),
        )

    async def submit_turn(
        self,
        *,
        command_id: str,
        project_id: str,
        request,
    ) -> dict[str, Any]:
        turn = await self._repository.begin_turn(
            command_id=str(command_id or "").strip(),
            project_id=str(project_id or "").strip(),
            session_id=request.sessionId,
            content=request.content,
            runtime_profile={
                "provider": request.runtime.apiProvider,
                "model": str(request.runtime.options.get("model") or ""),
                "contextWindow": request.runtime.contextWindow,
            },
        )
        await self._stream.started(str(turn["id"]))
        return turn

    def dispatch_turn(self, turn_id: str, runtime) -> asyncio.Task[None]:
        active = _ACTIVE_TASKS.get(f"turn:{turn_id}")
        if active is not None and not active.done():
            return active
        task = asyncio.create_task(self.execute_turn(turn_id, runtime))
        self._remember_task(f"turn:{turn_id}", task)
        if callable(self._track_background):
            self._track_background(task)
        return task

    async def execute_turn(self, turn_id: str, runtime) -> None:
        if not await self._repository.claim_turn(turn_id):
            return
        await self._stream.plan(turn_id)
        turn = await self._repository.load_turn(turn_id)
        if turn is None:
            return
        try:
            workspace = await self._projects.get_workspace(turn["projectId"])
            snapshot = await self._repository.get_snapshot(
                project_id=turn["projectId"],
                session_id=turn["sessionId"],
            )
            history = tuple(
                {"role": role, "content": content}
                for item in snapshot["turns"]
                if item["id"] != turn_id
                for role, content in (
                    ("user", str(item["userContent"])),
                    ("assistant", str(item["assistantContent"])),
                )
                if content
            )
            planned = await self._planner.plan(
                workspace=workspace,
                history=history,
                user_content=turn["userContent"],
                runtime=runtime,
                session_id=turn["sessionId"],
                turn_id=turn_id,
            )
            await self._repository.record_intent(
                turn_id,
                intent=planned.intent,
                planner_run_id=planned.run_id,
            )
            await self._stream.plan(turn_id)
            if planned.intent.action is ScreenplayIntentAction.ANSWER:
                await self._repository.complete_answer(
                    turn_id,
                    planned.intent.reply or "",
                )
                await self._stream.terminal(turn_id)
                return
            resolved = await self._resolver.resolve(
                workspace=workspace,
                intent=planned.intent,
            )
            compiled = compile_screenplay_manifest(
                intent=planned.intent,
                target_role=resolved.target_role,
                source_revision_refs=resolved.source_revision_refs,
                episode_scene_ids=resolved.episode_scene_ids,
                reviewed_draft_id=resolved.reviewed_draft_id,
                base_revision_id=resolved.base_revision_id,
                document_sections=resolved.document_sections,
                original_request=str(turn["userContent"]),
            )
            requirements = {
                "intent": planned.intent.to_mapping(),
                "targetRole": compiled.target_role,
                "episodeNumbers": list(resolved.episode_numbers),
                "baseRevisionId": resolved.base_revision_id,
                "sourceRevisionRefs": list(resolved.source_revision_refs),
                "manifestId": compiled.manifest.id,
                "manifest": {
                    "artifactKind": compiled.manifest.artifact_kind,
                    "assemblyStrategy": compiled.manifest.assembly_strategy,
                    "partSemanticKeys": [
                        part.semantic_key for part in compiled.manifest.parts
                    ],
                },
                "recipe": compiled.recipe.to_metadata(),
            }
            operation = await self._operations.create(
                ScreenplayOperationCreateCommand(
                    turn_id=turn_id,
                    project_id=str(turn["projectId"]),
                    session_id=int(turn["sessionId"]),
                    target_role=compiled.target_role,
                    requirements_json=requirements,
                    manifest_digest=compiled.manifest.digest,
                )
            )
            plan = _durable_plan(planned.intent)
            decision = TaskAdmissionDecision(
                mode=ExecutionMode.DURABLE,
                reason_code="screenplay_deliverable_requires_durable_execution",
                estimated_units=len(compiled.recipe.steps),
                estimated_model_calls=len(compiled.recipe.steps) - 1,
                covered_step_ids=("create", "publish"),
                execution_recipe=compiled.recipe,
            )
            descriptor = DurableTaskDescriptor(
                namespace="purrtypos.screenplay",
                owner_id=str(turn["projectId"]),
                idempotency_key=str(turn["commandId"]),
                metadata={
                    "projectId": str(turn["projectId"]),
                    "sessionId": int(turn["sessionId"]),
                    "turnId": turn_id,
                    "targetRole": compiled.target_role,
                    "plannerRunId": planned.run_id,
                    "sourceRevisionRefs": list(resolved.source_revision_refs),
                },
            )
            dispatcher = RecipeLongTaskDispatcher(
                work_item_repository=self._work_items,
                long_task_repository=self._long_tasks,
                descriptor_resolver=_FixedDescriptorResolver(descriptor),
                executor_registry=DurableExecutorRegistry({
                    "screenplay": self._unit_executor_factory(runtime),
                }),
                worker_id=self._owner_id,
            )
            request = _durable_request(turn, runtime)
            planner_run_id = str(planned.run_id or "").strip()
            if not planner_run_id:
                raise RuntimeError("screenplay intent Planner returned no Run id")
            receipt = await dispatcher.dispatch(
                request,
                plan,
                decision,
                parent_run_id=planner_run_id,
            )
            await self._operations.attach_long_task(
                operation.id,
                long_task_id=receipt.task_id,
                command_id=f"operation:dispatch:{operation.id}:{receipt.task_id}",
            )
            await self._repository.attach_operation(
                turn_id,
                operation_id=operation.id,
                task_id=receipt.task_id,
                target_role=compiled.target_role,
            )
            await self._stream.plan(turn_id)
            result = await dispatcher.execute(
                receipt.task_id,
                parent_run_id=planner_run_id,
                observer=lambda update: self._observe_task(turn_id, update),
            )
            if result.status is LongTaskExecutionStatus.PAUSED:
                code = result.error or "screenplay_task_paused"
                message = _task_failure_message(code)
                await self._operations.pause(
                    operation.id,
                    code=code,
                    message=message,
                    command_id=f"operation:pause:{operation.id}:{code}",
                )
                await self._repository.pause_task(
                    turn_id,
                    code=code,
                    message=message,
                )
                await self._stream.terminal(turn_id)
                return
            if result.status is LongTaskExecutionStatus.CANCELED:
                await self._operations.cancel(
                    operation.id,
                    cancel_receipt_id=f"spacancel:{operation.id}:runtime",
                    command_id=f"operation:cancel:{operation.id}:runtime",
                )
                await self._repository.cancel_turn(turn_id)
                await self._stream.terminal(turn_id)
                return
            if result.status is LongTaskExecutionStatus.FAILED:
                code = result.error or "screenplay_task_failed"
                message = _task_failure_message(code)
                await self._operations.fail(
                    operation.id,
                    code=code,
                    message=message,
                    command_id=f"operation:fail:{operation.id}:{code}",
                )
                await self._repository.fail_task(
                    turn_id,
                    code=code,
                    message=message,
                )
                await self._stream.terminal(turn_id)
                return
            candidate_refs = []
            for step in compiled.recipe.steps:
                if step.kind != "validate_manifest_part":
                    continue
                ref = await self._parts.validated_unit_ref(
                    receipt.task_id,
                    step.id,
                )
                if ref is None:
                    raise RuntimeError(
                        f"screenplay validation Part is missing: {step.id}"
                    )
                candidate_refs.append(ref)
            final_response_ref = await self._parts.validated_unit_ref(
                receipt.task_id,
                "compose-final-response",
            )
            if final_response_ref is None:
                raise RuntimeError("screenplay final response Part is missing")
            await self._finalizer.finalize(
                ScreenplayOperationFinalizationCommand(
                    operation_id=operation.id,
                    expected_manifest_digest=compiled.manifest.digest,
                    candidate_part_refs=tuple(candidate_refs),
                    final_response_ref=final_response_ref,
                )
            )
            await self._stream.terminal(turn_id)
        except Exception as error:
            code, message = _task_failure(error)
            with suppress(Exception):
                current = await self._repository.load_turn(turn_id)
                operation = await self._operations.load_for_turn(turn_id)
                if operation is not None and not operation.status.terminal:
                    await self._operations.fail(
                        operation.id,
                        code=code,
                        message=message,
                        command_id=f"operation:fail:{operation.id}:{code}",
                    )
                if operation is not None:
                    await self._repository.fail_task(
                        turn_id,
                        code=code,
                        message=message,
                    )
                else:
                    await self._repository.fail_turn(
                        turn_id,
                        code="screenplay_intent_failed",
                        message=message,
                    )
                await self._stream.terminal(turn_id)

    async def _observe_task(
        self,
        turn_id: str,
        update: LongTaskExecutionUpdate,
    ) -> None:
        await self._stream.task_progress(turn_id, update.event)

    async def cancel_turn(self, turn_id: str):
        turn = await self._repository.load_turn(turn_id)
        operation = await self._operations.load_for_turn(turn_id)
        self._cancel_task(f"turn:{turn_id}")
        if operation and operation.long_task_id:
            task = await self._long_tasks.load(operation.long_task_id)
            if task is not None and not task.status.terminal:
                await self._long_tasks.cancel(task.id)
        if operation is not None and not operation.status.terminal:
            await self._operations.cancel(
                operation.id,
                cancel_receipt_id=f"spacancel:{operation.id}:legacy",
                command_id=f"operation:cancel:{operation.id}:legacy",
            )
        result = await self._repository.cancel_turn(turn_id)
        await self._stream.terminal(turn_id)
        return result

    async def truncate_from_turn(self, turn_id: str):
        result = await self._repository.truncate_from_turn(turn_id)
        for deleted_turn_id in result["deletedTurnIds"]:
            self._cancel_task(f"turn:{deleted_turn_id}")
        return result

    @staticmethod
    def _remember_task(key: str, task: asyncio.Task[None]) -> None:
        _ACTIVE_TASKS[key] = task
        task.add_done_callback(
            lambda completed: _ACTIVE_TASKS.pop(key, None)
            if _ACTIVE_TASKS.get(key) is completed
            else None
        )

    @staticmethod
    def _cancel_task(key: str) -> None:
        task = _ACTIVE_TASKS.pop(key, None)
        if task is not None and not task.done():
            task.cancel()

    async def get_snapshot(self, *, project_id: str, session_id: int):
        snapshot = await self._repository.get_snapshot(
            project_id=project_id,
            session_id=session_id,
        )
        for task in snapshot["tasks"]:
            task["resultRevision"] = None
            revision_id = str(task.get("resultRevisionId") or "").strip()
            if not revision_id:
                continue
            try:
                task["resultRevision"] = await self._projects.get_revision(
                    revision_id,
                    view="summary",
                )
            except NotFoundError:
                # Legacy or synthetic Task records can retain a stable result
                # reference after the Revision itself has been removed. Keep
                # the reference visible without inventing replacement metadata.
                continue
        return snapshot

    async def list_events(
        self,
        *,
        project_id: str,
        session_id: int,
        after: int,
        limit: int,
    ):
        return await self._repository.list_events(
            project_id=project_id,
            session_id=session_id,
            after=after,
            limit=limit,
        )


def _task_failure(error: Exception) -> tuple[str, str]:
    if not isinstance(error, ModelGatewayError):
        return "screenplay_task_failed", str(error) or "剧本任务执行失败。"
    message = _task_failure_message(error.code)
    if message.startswith("剧本任务执行失败") and str(error):
        message = str(error)
    return error.code, message


def _task_failure_message(code: str) -> str:
    messages = {
        "model_output_truncated": (
            "模型本轮输出额度耗尽，未形成完整候选稿；不完整结果未被保存。"
            "请重试；若重复出现，请更换模型或减少本次生成的内容量。"
        ),
        "model_output_filtered": "模型输出被服务商安全策略中止，请调整要求后重试。",
        "upstream_stream_interrupted": "模型流式响应在完成前中断，请检查网络后重试。",
        "unsupported_model_finish_reason": "模型以不受支持的状态结束，请更换模型后重试。",
    }
    return messages.get(code, "剧本任务执行失败，请查看诊断信息后重试。")


class _FixedDescriptorResolver:
    def __init__(self, descriptor: DurableTaskDescriptor) -> None:
        self._descriptor = descriptor

    async def resolve(self, request, plan, decision):
        del request, plan, decision
        return self._descriptor


def _durable_plan(intent: ScreenplayIntent) -> TaskPlan:
    return TaskPlan(
        title="完成剧本交付物",
        task_spec=TaskSpec(
            goal=intent.instruction,
            operation=intent.action.value,
        ),
        steps=(
            TaskStep(
                id="create",
                title="创作并校验内容",
                type=StepType.WRITE,
                executor=StepExecutor.TOOL,
            ),
            TaskStep(
                id="publish",
                title="生成候选稿",
                type=StepType.WRITE,
                executor=StepExecutor.TOOL,
                depends_on=("create",),
            ),
        ),
    )


def _durable_request(turn: Mapping[str, Any], runtime) -> AgentRunRequest:
    return AgentRunRequest(
        messages=(AgentMessage(
            role=MessageRole.USER,
            content=str(turn["userContent"]),
        ),),
        model=model_request_from_runtime(runtime),
        domain_context=DomainContext(
            namespace="purrtypos.screenplay",
            payload={"projectId": str(turn["projectId"])},
        ),
        session_id=int(turn["sessionId"]),
        mode="agent",
        context_window=context_window_tokens(
            runtime.contextWindow or runtime.options.get("context_window")
        ),
        tools_enabled=True,
    )


__all__ = [
    "PlannedScreenplayIntent",
    "ResolvedScreenplayTask",
    "ScreenplayAgentService",
    "ScreenplayIntentPlanner",
    "ScreenplayTaskResolver",
]
