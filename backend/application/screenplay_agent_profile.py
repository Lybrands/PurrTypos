"""Screenplay product extension from Root TaskSpec to durable execution."""

from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

from application.agent_profile_registry import AgentProfileRegistration
from application.screenplay_agent_context import ScreenplayAgentContextQuery
from application.screenplay_manifest_compiler import compile_screenplay_manifest
from application.screenplay_task_resolver import (
    ResolvedScreenplayTask,
    SqliteScreenplayTaskResolver,
)
from application.screenplay_v2_service import ScreenplayV2ProjectService
from domains.screenplay_agent.adapter import (
    ScreenplayDomainAdapter,
    ScreenplayHostContextProvider,
)
from domains.screenplay_agent.agent_context import (
    SCREENPLAY_AGENT_DOMAIN_NAMESPACE,
    ScreenplayAgentDomainContext,
)
from domains.screenplay_agent.contracts import (
    ScreenplayIntent,
    ScreenplayIntentAction,
    ScreenplayStageCommand,
)
from domains.screenplay_agent.operation import ScreenplayOperationCreateCommand
from infrastructure.persistence.sqlite_screenplay_agent_repository import (
    SqliteScreenplayAgentRepository,
)
from infrastructure.persistence.sqlite_screenplay_operation_repository import (
    SqliteScreenplayOperationRepository,
)
from infrastructure.screenplay import (
    build_screenplay_tool_catalog,
)
from purra.contracts import AgentRunRequest, TaskPlan
from purra.long_tasks import (
    DurableExecutorRegistry,
    DurableTaskDescriptor,
    RecipeLongTaskDispatcher,
)
from purra.task_admission import ExecutionMode, TaskAdmissionDecision
from purra.task_admission import LongTaskExecutionStatus
from purra.json_values import thaw_json_mapping


class ScreenplayAgentProfileExtension:
    def __init__(self, db, *, owner_id: str | None = None, resolver=None) -> None:
        self._owner_id = str(owner_id or "").strip() or (
            f"screenplay-profile-{uuid4().hex}"
        )
        self._db = db
        self._turns = SqliteScreenplayAgentRepository(
            db,
            owner_id=self._owner_id,
        )
        self._operations = SqliteScreenplayOperationRepository(db)
        self._projects = ScreenplayV2ProjectService(db)
        self._resolver = resolver or SqliteScreenplayTaskResolver(db)
        context_query = ScreenplayAgentContextQuery(db)

        async def load_planning_context(project_id: str):
            workspace = await self._projects.get_workspace(project_id)
            return await context_query.planning_context(workspace)

        self._adapter = ScreenplayDomainAdapter(
            tool_catalog=build_screenplay_tool_catalog(db=db),
            context_provider=ScreenplayHostContextProvider(
                planning_context_loader=load_planning_context,
            ),
        )

    def profile_registration(self) -> AgentProfileRegistration:
        return AgentProfileRegistration(
            id="screenplay",
            domain_namespace=SCREENPLAY_AGENT_DOMAIN_NAMESPACE,
            adapter=self._adapter,
        )

    async def prepare_request(self, request: AgentRunRequest) -> AgentRunRequest:
        context = ScreenplayAgentDomainContext.from_core_context(
            request.domain_context
        )
        if context.is_child:
            return request
        turn = await self._turns.load_turn(str(context.turn_id))
        if turn is None:
            raise ValueError("screenplay Root Run requires a persisted Turn")
        if (
            turn["projectId"] != context.project_id
            or request.session_id is None
            or int(turn["sessionId"]) != int(request.session_id)
            or request.latest_user_text() != turn["userContent"]
        ):
            raise ValueError("screenplay Root Run scope does not match its Turn")
        workspace = await self._projects.get_workspace(context.project_id)
        source = (workspace.get("project") or {}).get("source") or {}
        stage_command = turn.get("stageCommand")
        hydrated = replace(
            context,
            stage_command=(
                ScreenplayStageCommand.from_mapping(stage_command)
                if stage_command is not None
                else None
            ),
            source_book_id=str(source.get("bookId") or "") or None,
            source_scope=dict(source.get("scope") or {}),
        )
        return replace(request, domain_context=hydrated.to_core_context())

    def context_provider_factory(self):
        return None

    def response_judge_policies(self, request):
        del request
        return ()

    def task_admission(self):
        return self

    async def evaluate(self, request, plan, signal=None):
        del signal
        if not isinstance(plan, TaskPlan) or plan.task_spec is None:
            raise ValueError("screenplay admission requires a planned TaskSpec")
        context = ScreenplayAgentDomainContext.from_core_context(
            request.domain_context
        )
        if not context.is_root:
            return TaskAdmissionDecision()
        intent = ScreenplayIntent.from_task_spec(plan.task_spec, plan.steps)
        if context.stage_command is not None:
            context.stage_command.require_compatible(intent)
        await self._turns.record_admitted_intent(
            str(context.turn_id),
            intent=intent,
        )
        if intent.action is ScreenplayIntentAction.ANSWER:
            return TaskAdmissionDecision(
                mode=ExecutionMode.INLINE,
                reason_code="screenplay_answer_runs_inline",
            )
        workspace = await self._projects.get_workspace(context.project_id)
        resolved: ResolvedScreenplayTask = await self._resolver.resolve(
            workspace=workspace,
            intent=intent,
        )
        compiled = compile_screenplay_manifest(
            intent=intent,
            target_role=resolved.target_role,
            source_revision_refs=resolved.source_revision_refs,
            episode_scene_ids=resolved.episode_scene_ids,
            reviewed_draft_id=resolved.reviewed_draft_id,
            base_revision_id=resolved.base_revision_id,
            document_sections=resolved.document_sections,
            original_request=request.latest_user_text(),
            plan_bindings=intent.plan_bindings,
            plan_steps=plan.steps,
        )
        turn = await self._turns.load_turn(str(context.turn_id))
        if turn is None:
            raise ValueError("screenplay admission Turn disappeared")
        requirements = {
            "intent": intent.to_mapping(),
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
                turn_id=str(context.turn_id),
                project_id=context.project_id,
                session_id=int(turn["sessionId"]),
                target_role=compiled.target_role,
                requirements_json=requirements,
                manifest_digest=compiled.manifest.digest,
            )
        )
        return TaskAdmissionDecision(
            mode=ExecutionMode.DURABLE,
            reason_code="screenplay_formal_task_requires_durable_execution",
            estimated_units=len(compiled.recipe.steps),
            estimated_model_calls=sum(
                step.kind.startswith("generate_")
                or step.kind == "compose_final_response"
                for step in compiled.recipe.steps
            ),
            covered_step_ids=tuple(step.id for step in plan.steps),
            execution_recipe=compiled.recipe,
            metadata={
                "operationId": operation.id,
                "commandId": str(turn["commandId"]),
                "projectId": context.project_id,
                "sessionId": int(turn["sessionId"]),
                "turnId": str(context.turn_id),
                "targetRole": compiled.target_role,
                "manifestId": compiled.manifest.id,
                "manifestDigest": compiled.manifest.digest,
                "sourceRevisionRefs": list(resolved.source_revision_refs),
            },
        )

    def create_long_task_dispatcher(
        self,
        *,
        work_item_repository=None,
        long_task_repository=None,
        executor=None,
    ):
        if executor is None:
            return None
        if work_item_repository is None or long_task_repository is None:
            raise ValueError("screenplay durable repositories are required")
        return _ScreenplayRecipeLongTaskDispatcher(
            db=self._db,
            operations=self._operations,
            turns=self._turns,
            work_item_repository=work_item_repository,
            long_task_repository=long_task_repository,
            descriptor_resolver=_ScreenplayTaskDescriptorResolver(),
            executor_registry=DurableExecutorRegistry({"screenplay": executor}),
            worker_id=self._owner_id,
        )

    def clear_active_executions(self) -> None:
        return None


class _ScreenplayTaskDescriptorResolver:
    async def resolve(self, request, plan, decision):
        del request, plan
        metadata = decision.metadata
        return DurableTaskDescriptor(
            namespace=SCREENPLAY_AGENT_DOMAIN_NAMESPACE,
            owner_id=str(metadata["projectId"]),
            idempotency_key=str(metadata["commandId"]),
            metadata={
                key: metadata[key]
                for key in (
                    "operationId",
                    "projectId",
                    "sessionId",
                    "turnId",
                    "targetRole",
                    "manifestId",
                    "manifestDigest",
                    "sourceRevisionRefs",
                )
            },
        )


class _ScreenplayRecipeLongTaskDispatcher(RecipeLongTaskDispatcher):
    def __init__(self, *, db, operations, turns, **kwargs) -> None:
        super().__init__(**kwargs)
        self._db = db
        self._operations = operations
        self._turns = turns

    async def dispatch(self, request, plan, decision, **kwargs):
        async with self._db.transaction(cancellation_linearizable=True):
            receipt = await super().dispatch(request, plan, decision, **kwargs)
            operation_id = str(decision.metadata.get("operationId") or "")
            await self._operations.attach_long_task(
                operation_id,
                long_task_id=receipt.task_id,
                command_id=f"operation:dispatch:{operation_id}:{receipt.task_id}",
            )
            await self._turns.attach_operation(
                str(decision.metadata.get("turnId") or ""),
                operation_id=operation_id,
                task_id=receipt.task_id,
                target_role=str(decision.metadata.get("targetRole") or ""),
                root_run_id=str(kwargs.get("parent_run_id") or ""),
            )
        return receipt

    async def execute(self, task_id, *, parent_run_id, observer, signal=None):
        try:
            result = await super().execute(
                task_id,
                parent_run_id=parent_run_id,
                observer=observer,
                signal=signal,
            )
            await self._settle_execution(task_id, result)
            return result
        except Exception as error:
            await self._settle_exception(task_id, error)
            raise

    async def _settle_execution(self, task_id, result) -> None:
        task = await self._long_tasks.load(task_id)
        if task is None:
            raise RuntimeError("screenplay LongTask disappeared")
        metadata = thaw_json_mapping(task.metadata)
        operation_id = str(metadata.get("operationId") or "")
        turn_id = str(metadata.get("turnId") or "")
        operation = await self._operations.load(operation_id)
        if operation is None:
            raise RuntimeError("screenplay Operation disappeared")
        if result.status is LongTaskExecutionStatus.PAUSED:
            code = result.error or "screenplay_task_paused"
            message = _task_failure_message(code)
            async with self._db.transaction(cancellation_linearizable=True):
                await self._operations.pause(
                    operation_id,
                    code=code,
                    message=message,
                    command_id=(
                        f"operation:pause:{operation_id}:"
                        f"{operation.revision}:{code}"
                    ),
                )
                await self._turns.pause_task(
                    turn_id,
                    code=code,
                    message=message,
                )
            return
        if result.status is LongTaskExecutionStatus.CANCELED:
            receipt = await self._operations.request_cancel(
                turn_id,
                idempotency_key=(
                    f"runtime-cancel:{operation_id}:{operation.revision}"
                ),
            )
            await self._operations.settle_cancel(turn_id, receipt_id=receipt.id)
            return
        if result.status is LongTaskExecutionStatus.FAILED:
            code = result.error or "screenplay_task_failed"
            message = _task_failure_message(code)
            async with self._db.transaction(cancellation_linearizable=True):
                await self._operations.fail(
                    operation_id,
                    code=code,
                    message=message,
                    command_id=(
                        f"operation:fail:{operation_id}:"
                        f"{operation.revision}:{code}"
                    ),
                )
                await self._turns.fail_task(
                    turn_id,
                    code=code,
                    message=message,
                )
            return
        if result.status is not LongTaskExecutionStatus.COMPLETED:
            raise RuntimeError("screenplay LongTask returned an unknown status")

    async def _settle_exception(self, task_id: str, error: Exception) -> None:
        task = await self._long_tasks.load(task_id)
        if task is None:
            return
        metadata = thaw_json_mapping(task.metadata)
        operation_id = str(metadata.get("operationId") or "")
        turn_id = str(metadata.get("turnId") or "")
        operation = await self._operations.load(operation_id)
        if operation is None or operation.status.terminal:
            return
        code = str(getattr(error, "code", "") or "screenplay_task_failed")
        message = str(error) or _task_failure_message(code)
        async with self._db.transaction(cancellation_linearizable=True):
            await self._operations.fail(
                operation_id,
                code=code,
                message=message,
                command_id=(
                    f"operation:fail:{operation_id}:{operation.revision}:{code}"
                ),
            )
            await self._turns.fail_task(turn_id, code=code, message=message)


def _task_failure_message(code: str) -> str:
    return {
        "model_output_truncated": (
            "模型本轮输出额度耗尽，未形成完整候选稿；不完整结果未被保存。"
            "请重试；若重复出现，请更换模型或减少本次生成的内容量。"
        ),
        "model_output_filtered": "模型输出被服务商安全策略中止，请调整要求后重试。",
        "upstream_stream_interrupted": "模型流式响应在完成前中断，请检查网络后重试。",
        "unsupported_model_finish_reason": "模型以不受支持的状态结束，请更换模型后重试。",
    }.get(code, "剧本任务执行失败，请查看诊断信息后重试。")


def build_screenplay_profile_extension(
    *,
    db,
    **_dependencies,
) -> ScreenplayAgentProfileExtension:
    return ScreenplayAgentProfileExtension(db)


__all__ = [
    "ScreenplayAgentProfileExtension",
    "build_screenplay_profile_extension",
]
