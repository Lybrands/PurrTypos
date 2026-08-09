"""Product application facade for screenplay Agent Run execution."""

from __future__ import annotations

from collections.abc import AsyncIterator

from agent_core.contracts import (
    AgentRunResult,
    RunBinding,
    RunLineage,
    RunProvenance,
)
from agent_core.ports import CancellationSignal
from application.agent_run_service import AgentRunService, AgentRunUpdate
from application.screenplay_agent_request_mapping import (
    screenplay_run_options,
    to_screenplay_agent_request,
)
from application.screenplay_v2_service import ScreenplayV2ProjectService
from schemas.screenplay_agent_run import ScreenplayAgentRunRequest


class ScreenplayOperationRunLifecycle:
    """Project generic Run lifecycle facts into one screenplay Operation."""

    def __init__(self, service, *, project_id: str, operation_id: str) -> None:
        self._service = service
        self._project_id = str(project_id or "").strip()
        self._operation_id = str(operation_id or "").strip()

    async def validate(self) -> None:
        await self._service.require_executable_operation(
            operation_id=self._operation_id,
            project_id=self._project_id,
        )

    async def on_run_started(self, run_id: str) -> None:
        await self._service.activate_bound_run(
            operation_id=self._operation_id,
            project_id=self._project_id,
            run_id=run_id,
        )

    async def on_run_finished(self, result: AgentRunResult) -> None:
        await self._service.settle_operation_from_root_run(
            operation_id=self._operation_id,
            project_id=self._project_id,
            run_id=result.run_id,
            run_status=result.status.value,
            error=result.error,
        )


class ScreenplayAgentRunService:
    """Own screenplay mapping, Operation binding, and durable execution hooks."""

    def __init__(self, composition) -> None:
        self._composition = composition
        self._operations = ScreenplayV2ProjectService(composition.database)

    async def run(
        self,
        *,
        body: ScreenplayAgentRunRequest,
        api_key: str,
        provider_options: dict,
        signal: CancellationSignal,
        provenance: RunProvenance | None = None,
        lineage: RunLineage | None = None,
        conversation_turn_id: str | None = None,
    ) -> AsyncIterator[AgentRunUpdate]:
        request = to_screenplay_agent_request(body, provider_options)
        project_id = body.screenplayProjectId
        operation_id = str(body.screenplayOperationId or "").strip()
        normalized_turn_id = str(conversation_turn_id or "").strip() or None
        binding = (
            RunBinding(
                namespace="screenplay.operation",
                aggregate_id=project_id,
                command_id=operation_id,
                attributes=(
                    {"conversationTurnId": normalized_turn_id}
                    if normalized_turn_id is not None
                    else {}
                ),
            )
            if operation_id
            else (
                RunBinding(
                    namespace="screenplay.conversation_turn",
                    aggregate_id=project_id,
                    command_id=normalized_turn_id,
                )
                if normalized_turn_id
                else None
            )
        )
        lifecycle = (
            ScreenplayOperationRunLifecycle(
                self._operations,
                project_id=project_id,
                operation_id=operation_id,
            )
            if operation_id
            else None
        )

        async def execute_long_task(
            task_id,
            parent_run_id,
            observer,
            long_task_signal,
        ):
            runtime = self._composition.profile_extension("screenplay")
            return await runtime.execute_long_task(
                task_id,
                composition=self._composition,
                parent_run_id=parent_run_id,
                observer=observer,
                body=body,
                api_key=api_key,
                provider_options=provider_options,
                signal=long_task_signal,
            )

        base_options = screenplay_run_options(
            request,
            provenance=provenance,
            lineage=lineage,
            binding=binding,
        )
        stream = AgentRunService(self._composition).run(
            body=body,
            api_key=api_key,
            provider_options=provider_options,
            signal=signal,
            provenance=provenance,
            lineage=lineage,
            mapped_request=request,
            base_options=base_options,
            run_binding_lifecycle=lifecycle,
            long_task_executor=execute_long_task,
        )
        try:
            async for update in stream:
                yield update
        finally:
            await stream.aclose()


__all__ = [
    "ScreenplayAgentRunService",
    "ScreenplayOperationRunLifecycle",
]
