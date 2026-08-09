"""Product composition owned by the screenplay Agent application boundary."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import replace

from application.agent_profile_registry import AgentProfileRegistration
from application.screenplay_long_task_execution import ScreenplayLongTaskExecution
from application.screenplay_long_tasks import ScreenplayLongTaskDispatcher
from application.screenplay_v2_proposal_projector import (
    ScreenplayV2ProposalProjector,
)
from agent_core.work_items import WorkItemLifecycle
from domains.screenplay.adapter import ScreenplayDomainAdapter
from domains.screenplay.contracts import (
    SCREENPLAY_DOMAIN_NAMESPACE,
    ScreenplayDomainContext,
)
from domains.screenplay.source_scope import is_restricted_source_scope
from domains.screenplay.task_admission import ScreenplayTaskAdmissionEvaluator
from infrastructure.screenplay import build_screenplay_tool_catalog
from infrastructure.screenplay.agent_query import SqliteScreenplayAgentQuery


class ScreenplayAgentComposition:
    profile_id = "screenplay"
    namespace = SCREENPLAY_DOMAIN_NAMESPACE

    def __init__(
        self,
        db,
        *,
        artifact_continuity,
        work_item_repository,
        long_task_repository,
        execution_lease_store,
        adapter: ScreenplayDomainAdapter | None = None,
    ) -> None:
        self._query = SqliteScreenplayAgentQuery(db)
        self.adapter = adapter or ScreenplayDomainAdapter.build(
            context_query=self._query,
            tool_catalog=build_screenplay_tool_catalog(db),
            artifact_continuity=artifact_continuity,
        )
        self.task_admission = ScreenplayTaskAdmissionEvaluator(self._query)
        self._work_item_repository = work_item_repository
        self._long_task_repository = long_task_repository
        self._execution_lease_store = execution_lease_store
        self._active_parent_runs: dict[str, str] = {}
        self._active_lock = asyncio.Lock()

    @staticmethod
    def create_event_projector(db):
        return ScreenplayV2ProposalProjector(db)

    def profile_registration(self) -> AgentProfileRegistration:
        return AgentProfileRegistration(
            id=self.profile_id,
            domain_namespace=self.namespace,
            adapter=self.adapter,
        )

    async def prepare_request(self, request):
        if request.domain_context.namespace != self.namespace:
            return None
        context = ScreenplayDomainContext.from_core_context(
            request.domain_context
        )
        project = await self._query.get_project(context.project_id)
        if project is None:
            return request
        hydrated = replace(
            context,
            source_scope_restricted=is_restricted_source_scope(project),
        )
        return replace(request, domain_context=hydrated.to_core_context())

    def create_long_task_dispatcher(
        self,
        *,
        work_item_repository,
        long_task_repository,
        executor,
    ) -> ScreenplayLongTaskDispatcher:
        return ScreenplayLongTaskDispatcher(
            work_items=WorkItemLifecycle(work_item_repository),
            long_tasks=long_task_repository,
            executor=executor,
        )

    async def execute_long_task(
        self,
        task_id: str,
        *,
        composition,
        parent_run_id: str,
        observer,
        body,
        api_key: str,
        provider_options: dict,
        signal,
    ):
        """Execute product-owned durable units under one active root Run."""

        normalized = str(task_id or "").strip()
        normalized_parent = str(parent_run_id or "").strip()
        async with self._active_lock:
            active_parent = self._active_parent_runs.get(normalized)
            if active_parent and active_parent != normalized_parent:
                raise RuntimeError("screenplay_long_task_already_has_active_root")
            self._active_parent_runs[normalized] = normalized_parent
        try:
            execution = ScreenplayLongTaskExecution(
                composition=composition,
                repository=self._long_task_repository,
                work_items=WorkItemLifecycle(self._work_item_repository),
                body=body,
                api_key=api_key,
                provider_options=provider_options,
                signal=signal,
                observer=observer,
                parent_run_id=normalized_parent,
            )
            return await execution.run(normalized)
        finally:
            async with self._active_lock:
                if self._active_parent_runs.get(normalized) == normalized_parent:
                    self._active_parent_runs.pop(normalized, None)

    async def pause_long_task(self, task_id: str):
        normalized = str(task_id or "").strip()
        task = await self._long_task_repository.pause(normalized)
        await self._request_execution_stop(normalized)
        return task

    async def cancel_long_task(self, task_id: str):
        normalized = str(task_id or "").strip()
        units = await self._long_task_repository.list_units(normalized)
        active_run_ids = tuple(dict.fromkeys(
            str(unit.run_id or "").strip()
            for unit in units
            if str(unit.status.value) in {"claimed", "running"}
            and str(unit.run_id or "").strip()
        ))
        task = await self._long_task_repository.cancel(normalized)
        await self._request_execution_stop(
            normalized,
            child_run_ids=active_run_ids,
        )
        return task

    async def _request_execution_stop(
        self,
        task_id: str,
        *,
        child_run_ids: Sequence[str] = (),
    ) -> None:
        parent_run_id = self._active_parent_runs.get(task_id)
        run_ids = tuple(dict.fromkeys((
            *(str(item or "").strip() for item in child_run_ids),
            str(parent_run_id or "").strip(),
        )))
        await asyncio.gather(*(
            self._execution_lease_store.request_cancellation(run_id)
            for run_id in run_ids
            if run_id
        ))

    def clear_active_executions(self) -> None:
        self._active_parent_runs.clear()


__all__ = ["ScreenplayAgentComposition"]
