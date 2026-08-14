"""Application service for the screenplay Agent on PurrA durable tasks.

The model Planner owns semantic interpretation. The host validates that intent
against project truth, compiles a product recipe, and delegates durable
execution to PurrA.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from purra.contracts import (
    AgentMessage,
    AgentRunRequest,
    MessageRole,
    AgentRunResult,
    RunBinding,
    RunProvenance,
    RunStatus,
)
from purra.api import AgentCoreRunOptions
from purra.errors import (
    ModelGatewayError,
    RunCommitProjectionError,
    UnsupportedModelFeatureError,
)
from purra.model_protocol import FeatureRequirement, TaskCapabilityRequirements
from purra.task_admission import (
    LongTaskExecutionUpdate,
)
from purra.output import RuntimeOutputEvent
from purra.model_protocol import InvocationOutputLimit, resolve_invocation_output_limit
from purra.output import (
    PublicPresentationMode,
    ResponseTransactionMode,
    ResponseTransactionPolicy,
)
from application.agent_run_service import AgentRunService
from application.agent_cancellation_service import AgentCancellationService
from application.screenplay_agent_task_executor import ScreenplayTaskUnitExecutor
from application.screenplay_tool_calling import ScreenplayToolCallingService
from application.run_provenance import digest_model_endpoint
from application.model_runtime import (
    model_request_from_runtime,
    reasoning_mode_from_options,
    run_execution_intent,
)
from application.request_mapping import context_window_tokens
from domains.screenplay_agent.agent_context import ScreenplayAgentDomainContext
from domains.screenplay_agent import (
    ScreenplayIntentCommandMismatchError,
    ScreenplayStageCommand,
)
from exceptions import AppError, NotFoundError
from infrastructure.persistence.sqlite_long_task_repository import (
    SqliteLongTaskRepository,
)
from infrastructure.persistence.sqlite_screenplay_agent_repository import (
    SqliteScreenplayAgentRepository,
)
from infrastructure.persistence.sqlite_screenplay_operation_repository import (
    SqliteScreenplayOperationRepository,
)


_ACTIVE_TASKS: dict[str, asyncio.Task[None]] = {}


class ScreenplayAgentService:
    def __init__(
        self,
        db,
        *,
        owner_id: str,
        composition=None,
        unit_executor_factory: Callable[[Any], object] | None = None,
        projects,
        repository=None,
        output_processor=None,
        track_background=None,
    ) -> None:
        self._db = db
        self._repository = (
            repository
            or SqliteScreenplayAgentRepository(db, owner_id=owner_id)
        )
        self._owner_id = str(owner_id)
        self._composition = composition
        self._run_service = (
            AgentRunService(composition) if composition is not None else None
        )
        self._unit_executor_factory = (
            unit_executor_factory or self._default_unit_executor
        )
        self._projects = projects
        self._track_background = track_background
        self._output_processor = output_processor
        self._long_tasks = SqliteLongTaskRepository(db)
        self._operations = SqliteScreenplayOperationRepository(db)
        self._cancellation = (
            AgentCancellationService(db, composition)
            if composition is not None else None
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
            stage_command=(
                ScreenplayStageCommand.from_mapping(
                    request.stageCommand.model_dump(mode="json")
                ).to_mapping()
                if request.stageCommand is not None
                else None
            ),
            runtime_profile={
                "provider": request.runtime.apiProvider,
                "model": str(request.runtime.options.get("model") or ""),
                "contextWindow": request.runtime.contextWindow,
            },
        )
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
        turn = await self._repository.load_turn(turn_id)
        if turn is None:
            return
        if self._run_service is None or self._composition is None:
            await self._settle_execution_exception(
                turn_id,
                RuntimeError("screenplay Root Run composition is required"),
            )
            return
        try:
            request = _root_request(turn, runtime)
            body = _ScreenplayRunInput.from_turn(turn, runtime)
            model_request = request.model
            window = int(request.context_window or 200_000)
            lifecycle = _ScreenplayTurnRunLifecycle(
                self._db,
                self._repository,
                self._operations,
                turn_id,
            )
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
            async for _update in self._run_service.run(
                body=body,
                api_key=runtime.apiKey.get_secret_value(),
                provider_options={
                    "model": model_request.model,
                    **dict(model_request.options),
                },
                signal=asyncio.Event(),
                provenance=_root_provenance(runtime, turn),
                enable_delegation=False,
                mapped_request=request,
                base_options=AgentCoreRunOptions(
                    turn_id=turn_id,
                    output_limit=output_limit,
                    default_context_window_tokens=window,
                    force_planned_tool_choice=False,
                    require_tool_call=False,
                    reasoning_mode=reasoning_mode_from_options(runtime.options),
                    binding=RunBinding(
                        namespace="screenplay.conversation_turn",
                        aggregate_id=str(turn["projectId"]),
                        command_id=str(turn["commandId"]),
                    ),
                    response_transaction_policy=ResponseTransactionPolicy(
                        mode=ResponseTransactionMode.DIRECT_LIVE,
                        public_presentation=PublicPresentationMode.NONE,
                    ),
                ),
                run_binding_lifecycle=lifecycle,
                long_task_executor=self._unit_executor_factory(runtime),
            ):
                pass
        except Exception as error:
            await self._settle_execution_exception(turn_id, error)

    def _default_unit_executor(self, runtime):
        if self._composition is None:
            raise RuntimeError("screenplay Root Run composition is required")
        return ScreenplayTaskUnitExecutor(
            self._db,
            runtime=runtime,
            composition=self._composition,
            tool_calling_service=ScreenplayToolCallingService(
                self._db,
                composition=self._composition,
            ),
        )

    def dispatch_resumed_operation(
        self,
        operation_id: str,
        runtime,
    ) -> asyncio.Task[None]:
        key = f"operation:{operation_id}"
        active = _ACTIVE_TASKS.get(key)
        if active is not None and not active.done():
            return active
        task = asyncio.create_task(
            self.execute_resumed_operation(operation_id, runtime)
        )
        self._remember_task(key, task)
        if callable(self._track_background):
            self._track_background(task)
        return task

    async def execute_resumed_operation(self, operation_id: str, runtime) -> None:
        operation = await self._operations.load(operation_id)
        if operation is None:
            raise NotFoundError("剧本 Agent Operation 不存在")
        if operation.status.value != "running":
            raise AppError("only a resumed screenplay Operation can execute", 409)
        if not operation.long_task_id:
            raise AppError("resumed screenplay Operation has no LongTask", 409)
        turn = await self._repository.load_turn(operation.turn_id)
        if turn is None:
            raise NotFoundError("剧本 Agent Turn 不存在")
        root_run_id = str(turn.get("rootRunId") or "").strip()
        if not root_run_id:
            raise AppError("resumed screenplay Operation has no Root Run", 409)
        if self._composition is None:
            raise RuntimeError("screenplay Root Run composition is required")
        try:
            dispatcher = self._composition.create_long_task_dispatcher(
                "screenplay",
                executor=self._unit_executor_factory(runtime),
            )
            if dispatcher is None:
                raise RuntimeError("screenplay durable dispatcher is unavailable")
            await dispatcher.execute(
                operation.long_task_id,
                parent_run_id=root_run_id,
                observer=lambda update: self._publish_task_update(
                    operation.turn_id,
                    update,
                ),
            )
        except Exception as error:
            await self._settle_execution_exception(operation.turn_id, error)

    async def _settle_execution_exception(
        self,
        turn_id: str,
        error: Exception,
    ) -> None:
        if isinstance(error, RunCommitProjectionError):
            # The Root terminal transaction rolled back in full. Leave the
            # durable business state retryable instead of compensating it into
            # a terminal product failure outside that transaction.
            return
        code, message = _task_failure(error)
        with suppress(Exception):
            operation = await self._operations.load_for_turn(turn_id)
            if operation is not None and not operation.status.terminal:
                await self._operations.fail(
                    operation.id,
                    code=code,
                    message=message,
                    command_id=(
                        f"operation:fail:{operation.id}:"
                        f"{operation.revision}:{code}"
                    ),
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
                    code=(
                        code
                        if isinstance(
                            error,
                            (
                                ModelGatewayError,
                                ScreenplayIntentCommandMismatchError,
                            ),
                        )
                        else "screenplay_intent_failed"
                    ),
                    message=message,
                )

    async def cancel_turn(self, turn_id: str, *, idempotency_key: str):
        try:
            receipt = await self._operations.request_cancel(
                turn_id,
                idempotency_key=idempotency_key,
            )
        except LookupError as error:
            raise NotFoundError("剧本 Agent Turn 不存在") from error
        except ValueError as error:
            raise AppError(str(error), 409) from error
        if receipt.terminal_status in {"succeeded", "failed", "canceled"}:
            return receipt.to_mapping()

        operation = await self._operations.load_for_turn(turn_id)
        turn = await self._repository.load_turn(turn_id)
        root_run_id = str((turn or {}).get("rootRunId") or "").strip()
        if root_run_id and self._cancellation is not None:
            await self._cancellation.cancel(root_run_id)

        if operation is not None:
            self._cancel_task(f"operation:{operation.id}")
        async with self._db.transaction(cancellation_linearizable=True):
            if operation is not None and operation.long_task_id:
                task = await self._long_tasks.load(operation.long_task_id)
                if task is not None and not task.status.terminal:
                    await self._long_tasks.cancel(task.id)
        # Operation and Turn remain active-but-cancel-requested until the Root
        # terminal commit closes the execution tree.  The Root projector owns
        # the atomic business settlement.
        return receipt.to_mapping()

    async def _publish_task_update(
        self,
        turn_id: str,
        update: LongTaskExecutionUpdate,
    ) -> None:
        if self._output_processor is None:
            return
        run_id = str(update.event.run_id or "").strip()
        if not run_id:
            raise RuntimeError("long task progress requires its parent Run")
        await self._output_processor.accept_runtime_event(RuntimeOutputEvent(
            event_id=f"long-task-{uuid4().hex}",
            run_id=run_id,
            turn_id=turn_id,
            event_type=str(update.event.type),
            payload=update.event.payload,
            occurred_at=datetime.now(timezone.utc),
        ))

    async def prepare_resume(self, operation_id: str, *, idempotency_key: str, request):
        operation = await self._operations.load(operation_id)
        if operation is None:
            raise NotFoundError("剧本 Agent Operation 不存在")
        requirements = _capability_requirements(
            operation.requirements_json,
            request.runtime,
        )
        try:
            model_request = model_request_from_runtime(
                request.runtime,
                requirements=requirements,
            )
        except UnsupportedModelFeatureError as error:
            raise AppError("model_capability_incompatible", 409) from error
        snapshot = model_request.capability_snapshot.to_mapping(
            include_digest=True,
        )
        try:
            resumed = await self._operations.resume_with_model(
                operation.id,
                command_id=idempotency_key,
                expected_revision=request.expectedOperationRevision,
                capability_snapshot=snapshot,
            )
        except ValueError as error:
            raise AppError(str(error), 409) from error
        return {
            "operationId": resumed.id,
            "turnId": resumed.turn_id,
            "status": resumed.status.value,
            "revision": resumed.revision,
            "capabilitySnapshotDigest": model_request.capability_snapshot.digest(),
        }

    async def truncate_from_turn(self, turn_id: str):
        result = await self._repository.truncate_from_turn(turn_id)
        for deleted_turn_id in result["deletedTurnIds"]:
            self._cancel_task(f"turn:{deleted_turn_id}")
        for deleted_operation_id in result.get("deletedOperationIds", ()):
            self._cancel_task(f"operation:{deleted_operation_id}")
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
        revisions_by_task = {
            str(task["id"]): task.get("resultRevision")
            for task in snapshot["tasks"]
        }
        for operation in snapshot.get("operations", ()):
            operation["resultRevision"] = revisions_by_task.get(
                str(operation.get("taskId") or "")
            )
        return snapshot

def _task_failure(error: Exception) -> tuple[str, str]:
    if isinstance(error, ScreenplayIntentCommandMismatchError):
        return error.code, str(error)
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


def _capability_requirements(stored: Mapping[str, Any], runtime):
    raw = dict(stored.get("capabilityRequirements") or {})
    return TaskCapabilityRequirements(
        reasoning_mode=reasoning_mode_from_options(runtime.options),
        tool_calling=FeatureRequirement(
            str(raw.get("toolCalling") or "optional")
        ),
        structured_output_level=str(
            raw.get("structuredOutputLevel") or "none"
        ),
        streaming_required=bool(raw.get("streamingRequired", True)),
        cancellation_required=bool(raw.get("cancellationRequired", True)),
    )


@dataclass(frozen=True, slots=True)
class _ScreenplayRunInput:
    messages: list[dict[str, Any]]
    apiProvider: str
    baseURL: str | None
    contextWindow: str | None
    options: dict[str, Any]

    @classmethod
    def from_turn(cls, turn: Mapping[str, Any], runtime):
        return cls(
            messages=[{"role": "user", "content": str(turn["userContent"])}],
            apiProvider=str(runtime.apiProvider),
            baseURL=runtime.baseURL,
            contextWindow=runtime.contextWindow,
            options=dict(runtime.options),
        )

    def model_copy(self, *, update: dict[str, Any] | None = None):
        return replace(self, **dict(update or {}))

    def model_dump(self, *args, **kwargs) -> dict[str, Any]:
        del args, kwargs
        return {
            "messages": list(self.messages),
            "apiProvider": self.apiProvider,
            "baseURL": self.baseURL,
            "contextWindow": self.contextWindow,
            "options": dict(self.options),
        }


class _ScreenplayTurnRunLifecycle:
    def __init__(self, db, turns, operations, turn_id: str) -> None:
        self._db = db
        self._turns = turns
        self._operations = operations
        self._turn_id = str(turn_id)

    async def validate(self) -> None:
        turn = await self._turns.load_turn(self._turn_id)
        if turn is None or turn["status"] != "planning":
            raise ValueError("screenplay Turn is not startable")

    async def before_submit(self) -> None:
        return None

    async def on_run_started(self, run_id: str) -> None:
        await self._turns.attach_root_run(self._turn_id, run_id)

    async def on_run_finished(self, result: AgentRunResult) -> None:
        turn = await self._turns.load_turn(self._turn_id)
        operation = await self._operations.load_for_turn(self._turn_id)
        if turn is None:
            raise RuntimeError("screenplay Root Turn projection disappeared")
        if result.status is RunStatus.DONE:
            if turn["status"] != "completed":
                raise RuntimeError(
                    "screenplay Root completed before its Turn projection"
                )
            if turn["assistantContent"] != result.final_response:
                raise RuntimeError(
                    "screenplay Root response conflicts with its Turn projection"
                )
            if operation is not None and operation.status.value != "succeeded":
                raise RuntimeError(
                    "screenplay durable Root completed before its Operation"
                )
            return
        expected = "failed"
        if result.status is RunStatus.CANCELED:
            expected = "canceled"
            if operation is not None and operation.long_task_id:
                task = await self._db.fetch_one(
                    "SELECT status FROM ai_agent_long_tasks WHERE id = ?",
                    [operation.long_task_id],
                )
                if task == {"status": "paused"}:
                    expected = "paused"
        if turn["status"] != expected:
            raise RuntimeError(
                "screenplay Root terminal state conflicts with Turn projection"
            )
        if operation is not None and operation.status.value != expected:
            raise RuntimeError(
                "screenplay Root terminal state conflicts with Operation projection"
            )

    async def on_start_failed(self, code: str):
        return await self._turns.fail_turn(
            self._turn_id,
            code=str(code or "screenplay_root_start_failed"),
            message="剧本 Agent Root Run 启动失败。",
        )


def _root_provenance(runtime, turn: Mapping[str, Any]) -> RunProvenance:
    request = model_request_from_runtime(runtime)
    profile = json.dumps(
        {
            "provider": runtime.apiProvider,
            "model": request.model,
            "contextWindow": runtime.contextWindow,
            "projectId": turn["projectId"],
            "turnId": turn["id"],
            "commandId": turn["commandId"],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return RunProvenance(
        model_provider=request.provider,
        model_name=request.model,
        context_window=context_window_tokens(
            runtime.contextWindow or runtime.options.get("context_window")
        ),
        endpoint_digest=digest_model_endpoint(runtime.baseURL),
        request_profile_digest=hashlib.sha256(
            profile.encode("utf-8")
        ).hexdigest(),
        capability_snapshot=request.capability_snapshot.to_mapping(
            include_digest=True,
        ),
        execution_intent=run_execution_intent(
            request,
            reasoning_mode_from_options(runtime.options),
            output_contract="assistant_text",
            tool_protocol_contract="screenplay_host_tools",
        ),
    )


def _root_request(turn: Mapping[str, Any], runtime) -> AgentRunRequest:
    return AgentRunRequest(
        messages=(AgentMessage(
            role=MessageRole.USER,
            content=str(turn["userContent"]),
        ),),
        model=model_request_from_runtime(runtime),
        domain_context=ScreenplayAgentDomainContext(
            project_id=str(turn["projectId"]),
            turn_id=str(turn["id"]),
            locale=str(getattr(runtime, "locale", "zh-CN") or "zh-CN"),
        ).to_core_context(),
        session_id=int(turn["sessionId"]),
        mode="agent",
        context_window=context_window_tokens(
            runtime.contextWindow or runtime.options.get("context_window")
        ),
        tools_enabled=True,
        metadata={"locale": str(getattr(runtime, "locale", "zh-CN"))},
    )


__all__ = [
    "ScreenplayAgentService",
]
