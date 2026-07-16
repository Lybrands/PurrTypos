"""The sole application composition root for complete Agent Core runs."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from agent_core.contracts import (
    AgentRunRequest,
    ApprovalDecision,
    ApprovalStatus,
    ToolExecutionLimits,
)
from agent_core.engine import AgentCore
from agent_core.events import AgentEvent, CoreEventType
from agent_core.tools import InMemoryApprovalGateway
from application.response_judging import ModelBackedResponseJudge
from domains.writing.adapter import WritingDomainAdapter
from domains.writing.context import WritingContextProvider
from domains.writing.context_source import RepositoryWritingContextSource
from domains.writing.response import writing_atomic_continuity_judge_policy
from infrastructure.models.provider_model_gateway import ProviderModelGateway
from infrastructure.models.provider_capabilities import ProviderCapabilityCache
from infrastructure.persistence.sqlite_run_repository import SqliteRunRepository
from infrastructure.persistence.writing import (
    SqliteAssociatedContextRepository,
    SqliteMemoryRecallRepository,
    SqliteWritingToolMemoryRepository,
)
from infrastructure.writing import (
    WritingSkillCatalog,
    WritingToolDependencies,
    build_writing_tool_catalog,
)
from config import AGENT_APPROVAL_TIMEOUT_SECONDS


class AgentComposition:
    """Own process-scoped adapters and create request-scoped model runtimes."""

    def __init__(
        self,
        db,
        *,
        skills_dir: Path | None = None,
        writing: WritingDomainAdapter | None = None,
        provider_capabilities: ProviderCapabilityCache | None = None,
        approval_gateway: InMemoryApprovalGateway | None = None,
        tool_execution_limits: ToolExecutionLimits | None = None,
    ):
        self._repository = SqliteRunRepository(db)
        self._provider_capabilities = (
            provider_capabilities or ProviderCapabilityCache()
        )
        self._skill_catalog: WritingSkillCatalog | None = None
        if writing is not None:
            self._writing = writing
        else:
            resolved_skills_dir = skills_dir or (
                Path(__file__).resolve().parent.parent / "skills"
            )
            self._skill_catalog = WritingSkillCatalog(resolved_skills_dir)
            self._writing = WritingDomainAdapter.build(
                tool_catalog=build_writing_tool_catalog(
                    dependencies=WritingToolDependencies(
                        db,
                        SqliteWritingToolMemoryRepository(db),
                    ),
                    skill_items=tuple(self._skill_catalog.skill_items()),
                ),
                context_provider=WritingContextProvider(
                    RepositoryWritingContextSource(
                        SqliteAssociatedContextRepository(db),
                        SqliteMemoryRecallRepository(db),
                    )
                ),
            )
        self._approval_gateway = approval_gateway or InMemoryApprovalGateway()
        self._tool_execution_limits = tool_execution_limits or ToolExecutionLimits(
            approval_timeout_seconds=AGENT_APPROVAL_TIMEOUT_SECONDS,
        )
        self._approval_runs: dict[str, str] = {}
        self._closed = False

    @property
    def writing(self) -> WritingDomainAdapter:
        return self._writing

    @property
    def skill_catalog(self) -> WritingSkillCatalog | None:
        return self._skill_catalog

    @property
    def provider_capabilities(self) -> ProviderCapabilityCache:
        return self._provider_capabilities

    def create_core(
        self,
        api_key: str,
        *,
        on_required_tool_choice_unsupported: Callable[[], None] | None = None,
    ) -> AgentCore:
        if self._closed:
            raise RuntimeError("Agent composition has been shut down")
        context_provider = self._writing.context_provider
        if context_provider is None:
            raise RuntimeError("Writing ContextProvider is not configured")
        model_gateway = ProviderModelGateway(
            api_key,
            on_required_tool_choice_unsupported=(
                on_required_tool_choice_unsupported
            ),
        )
        return AgentCore(
            model_gateway=model_gateway,
            run_repository=self._repository,
            planning_policy=self._writing.planning_policy,
            context_provider=context_provider,
            execution_state_factory=self._writing.execution_state_factory,
            tool_catalog=self._writing.tool_catalog,
            approval_gateway=self._approval_gateway,
            tool_execution_limits=self._tool_execution_limits,
        )

    def create_response_judges(
        self,
        api_key: str,
        request: AgentRunRequest,
    ) -> tuple[ModelBackedResponseJudge, ...]:
        """Create request-scoped semantic judges with an isolated model call."""

        if self._closed:
            raise RuntimeError("Agent composition has been shut down")
        judge_policy = writing_atomic_continuity_judge_policy(request)
        if judge_policy is None:
            return ()
        return (
            ModelBackedResponseJudge(
                model_gateway=ProviderModelGateway(api_key),
                model_request=request.model,
                policy=judge_policy,
            ),
        )

    def observe_event(self, event: AgentEvent) -> None:
        if self._closed:
            return
        if not event.run_id:
            return
        if event.type == CoreEventType.APPROVAL_REQUESTED:
            approval_id = str(event.payload.get("approvalId") or "").strip()
            if approval_id:
                self._approval_runs[approval_id] = event.run_id
            return
        if event.type == CoreEventType.APPROVAL_RESOLVED:
            approval_id = str(event.payload.get("approvalId") or "").strip()
            if approval_id:
                self._approval_runs.pop(approval_id, None)
            return
        if event.type in {
            CoreEventType.RUN_COMPLETED,
            CoreEventType.RUN_BLOCKED,
            CoreEventType.RUN_FAILED,
            CoreEventType.RUN_CANCELED,
        }:
            stale = [
                approval_id
                for approval_id, run_id in self._approval_runs.items()
                if run_id == event.run_id
            ]
            for approval_id in stale:
                self._approval_runs.pop(approval_id, None)

    def resolve_approval(
        self,
        approval_id: str,
        approved: bool,
    ) -> ApprovalStatus | None:
        normalized = str(approval_id or "").strip()
        run_id = self._approval_runs.get(normalized)
        if not run_id:
            return None
        status = self._approval_gateway.resolve(
            run_id,
            normalized,
            ApprovalDecision.APPROVE if approved else ApprovalDecision.REJECT,
        )
        if status is not None:
            self._approval_runs.pop(normalized, None)
        return status

    def release_run(self, run_id: str) -> None:
        """Drop live approval state when an application stream is closed."""

        normalized = str(run_id or "").strip()
        if not normalized:
            return
        self._approval_gateway.cancel_pending(normalized)
        stale = [
            approval_id
            for approval_id, pending_run_id in self._approval_runs.items()
            if pending_run_id == normalized
        ]
        for approval_id in stale:
            self._approval_runs.pop(approval_id, None)

    def shutdown(self) -> None:
        """Fail closed and release all lifespan-owned live approval state."""

        self._closed = True
        self._approval_gateway.close()
        self._approval_runs.clear()


_composition: AgentComposition | None = None


def set_agent_composition(composition: AgentComposition | None) -> None:
    global _composition
    _composition = composition


def clear_agent_composition(
    composition: AgentComposition | None = None,
) -> None:
    """Clear the process composition only when ``composition`` still owns it.

    The optional expected instance mirrors ``dependencies.clear_db``.  It
    prevents an older lifespan from erasing a newer composition during test
    reloads or overlapping application shutdown.
    """

    global _composition
    if composition is None or _composition is composition:
        _composition = None


def get_agent_composition() -> AgentComposition:
    if _composition is None:
        raise RuntimeError("Agent composition has not been initialized")
    return _composition
