"""The sole application composition root for complete PurrA runs."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import replace

from purra.contracts import (
    AgentRunRequest,
    ApprovalDecision,
    ApprovalStatus,
    ContextBudgetClaim,
    ToolExecutionLimits,
    ToolExecutionMode,
)
from purra.context_budget import resolve_context_budget_claims
from purra.json_values import thaw_json_mapping
from purra.context_orchestration.compaction import (
    ContextCompressionCoordinator,
)
from purra.context_orchestration.contracts import ContextCompressionSettings
from purra.api import AgentCore, AgentCoreRunOptions
from purra.delegation import AgentCoreSubmitter, ChildRunRequestFactory
from purra.events import AgentEvent, CoreEventType
from purra.ports import (
    ApprovalGateway,
    CheckpointStore,
    ContextCompressionHook,
    ConversationCompactor,
    ContextProvider,
    DelegationRepository,
    ExecutionLeaseStore,
    ResponseJudgePolicy,
    ToolRegistration,
)
from purra.output.processor import AgentOutputProcessor
from purra.output.ports import AgentOutputJournalQuery, AgentOutputRepository
from purra.tools import InMemoryToolCatalog
from application.conversation_compaction import ConversationCompactionService
from application.artifact_continuity import ArtifactContinuityCoordinator
from application.agent_profile_registry import (
    AgentProfileExtension,
    AgentProfileRegistry,
)
from application.planning_constraints import RequiredToolPlanningPolicy
from application.run_execution_control import RunExecutionSession
from infrastructure.models.provider_model_gateway import ProviderModelGateway
from infrastructure.models.model_conversation_summarizer import (
    ModelBackedConversationSummarizer,
)
from infrastructure.models.provider_capabilities import ProviderCapabilityCache
from infrastructure.persistence.sqlite_run_repository import SqliteRunRepository
from infrastructure.persistence.sqlite_agent_output_repository import (
    SqliteAgentOutputRepository,
)
from infrastructure.persistence.agent_output_publisher import (
    InProcessAgentOutputPublisher,
)
from infrastructure.persistence.run_execution_store import (
    SqliteExecutionLeaseStore,
)
from infrastructure.persistence.sqlite_checkpoint_store import (
    SqliteCheckpointStore,
)
from infrastructure.persistence.sqlite_delegation_repository import (
    SqliteDelegationRepository,
)
from infrastructure.persistence.sqlite_conversation_compaction_repository import (
    SqliteConversationCompactionRepository,
)
from infrastructure.persistence.sqlite_tool_idempotency_gateway import (
    SqliteToolIdempotencyGateway,
)
from infrastructure.persistence.sqlite_artifact_claim_repository import (
    SqliteArtifactClaimRepository,
)
from infrastructure.persistence.sqlite_artifact_continuity_query import (
    SqliteArtifactContinuityQuery,
)
from infrastructure.persistence.sqlite_work_item_repository import (
    SqliteWorkItemRepository,
)
from infrastructure.persistence.sqlite_long_task_repository import (
    SqliteLongTaskRepository,
)
from infrastructure.persistence import approval_store
from infrastructure.persistence.sqlite_approval_gateway import SqliteApprovalGateway
from config import AGENT_APPROVAL_TIMEOUT_SECONDS
from domains.agent_roles import AgentRoleRegistry


logger = logging.getLogger(__name__)


class AgentComposition:
    """Own process-scoped adapters and create request-scoped model runtimes."""

    def __init__(
        self,
        db,
        *,
        execution_db=None,
        run_commit_projector=None,
        profile_extension_factories: Sequence[
            Callable[..., AgentProfileExtension]
        ] = (),
        provider_capabilities: ProviderCapabilityCache | None = None,
        approval_gateway: ApprovalGateway | None = None,
        tool_execution_limits: ToolExecutionLimits | None = None,
    ):
        self._db = db
        self._execution_db = execution_db or db
        self._execution_lease_store = SqliteExecutionLeaseStore(
            self._execution_db
        )
        self._delegation_repository = SqliteDelegationRepository(db)
        self._checkpoint_store = SqliteCheckpointStore(db)
        self._conversation_compaction_repository = (
            SqliteConversationCompactionRepository(db)
        )
        self._repository = SqliteRunRepository(
            db,
            delegation_repository=self._delegation_repository,
        )
        self._output_repository = SqliteAgentOutputRepository(
            db,
            run_repository=self._repository,
            run_commit_projector=run_commit_projector,
        )
        self._output_publisher = InProcessAgentOutputPublisher()
        self._output_processor = AgentOutputProcessor(
            self._output_repository,
            self._output_publisher,
        )
        self._tool_idempotency_gateway = SqliteToolIdempotencyGateway(
            db,
            owner_id=self._repository.owner_id,
        )
        self._artifact_claim_repository = SqliteArtifactClaimRepository(db)
        self._work_item_repository = SqliteWorkItemRepository(db)
        self._long_task_repository = SqliteLongTaskRepository(db)
        self._artifact_continuity = ArtifactContinuityCoordinator(
            query=SqliteArtifactContinuityQuery(db),
            work_items=self._work_item_repository,
            claims=self._artifact_claim_repository,
        )
        self._provider_capabilities = (
            provider_capabilities or ProviderCapabilityCache()
        )
        self._profile_extensions = tuple(
            factory(
                db=db,
                artifact_continuity=self._artifact_continuity,
                work_item_repository=self._work_item_repository,
                long_task_repository=self._long_task_repository,
                execution_lease_store=self._execution_lease_store,
            )
            for factory in profile_extension_factories
        )
        extension_registrations = tuple(
            extension.profile_registration()
            for extension in self._profile_extensions
        )
        self._profile_extensions_by_id = {
            registration.id: extension
            for registration, extension in zip(
                extension_registrations,
                self._profile_extensions,
                strict=True,
            )
        }
        self._profile_registry = AgentProfileRegistry(extension_registrations)
        self._approval_gateway = approval_gateway or SqliteApprovalGateway(db)
        self._tool_execution_limits = tool_execution_limits or ToolExecutionLimits(
            approval_timeout_seconds=AGENT_APPROVAL_TIMEOUT_SECONDS,
        )
        self._approval_runs: dict[str, str] = {}
        self._background_run_tasks: set[asyncio.Task[None]] = set()
        self._active_cores: set[AgentCore] = set()
        self._closed = False

    @property
    def database(self):
        return self._db

    @property
    def provider_capabilities(self) -> ProviderCapabilityCache:
        return self._provider_capabilities

    @property
    def agent_profile_ids(self) -> tuple[str, ...]:
        return self._profile_registry.ids

    @property
    def execution_owner_id(self) -> str:
        return self._repository.owner_id

    @property
    def execution_lease_store(self) -> ExecutionLeaseStore:
        return self._execution_lease_store

    @property
    def delegation_repository(self) -> DelegationRepository:
        return self._delegation_repository

    @property
    def checkpoint_store(self) -> CheckpointStore:
        return self._checkpoint_store

    @property
    def output_repository(self) -> AgentOutputRepository:
        return self._output_repository

    @property
    def output_journal(self) -> AgentOutputJournalQuery:
        return self._output_repository

    @property
    def output_processor(self) -> AgentOutputProcessor:
        return self._output_processor

    @property
    def long_task_repository(self) -> SqliteLongTaskRepository:
        return self._long_task_repository

    @property
    def conversation_compaction_repository(
        self,
    ) -> SqliteConversationCompactionRepository:
        return self._conversation_compaction_repository

    async def prepare_request(
        self,
        request: AgentRunRequest,
    ) -> AgentRunRequest:
        """Hydrate renderer-independent, authoritative domain catalogs."""

        registration = self._profile_registry.for_request(request)
        extension = self._profile_extensions_by_id[registration.id]
        prepared = await extension.prepare_request(request)
        return request if prepared is None else prepared

    async def resolve_context_claims(
        self,
        request: AgentRunRequest,
        *,
        fallback: Sequence[ContextBudgetClaim] = (),
        signal=None,
    ) -> tuple[ContextBudgetClaim, ...]:
        """Use Core's demand protocol with the request's domain provider."""

        provider = self._profile_registry.for_request(
            request
        ).adapter.context_provider
        if provider is None:
            return tuple(fallback)
        return await resolve_context_budget_claims(
            provider,
            request,
            fallback,
            signal,
        )

    def create_core(
        self,
        api_key: str,
        *,
        agent_profile: str,
        on_required_tool_choice_unsupported: Callable[[], None] | None = None,
        extra_tool_registrations: Sequence[ToolRegistration] = (),
        agent_role_guidance: Mapping[str, object] | None = None,
        max_parallel_agents: int = 1,
        allowed_tool_modes: Collection[ToolExecutionMode] | None = None,
        required_tool_names: Collection[str] | None = None,
        context_compression_hook: ContextCompressionHook | None = None,
        context_compression_settings: ContextCompressionSettings = (
            ContextCompressionSettings()
        ),
        conversation_compactor: ConversationCompactor | None = None,
        context_provider_override: ContextProvider | None = None,
        long_task_executor=None,
        delegation_repository: DelegationRepository | None = None,
        child_core_submitter: AgentCoreSubmitter | None = None,
        child_request_factory: ChildRunRequestFactory | None = None,
    ) -> AgentCore:
        if self._closed:
            raise RuntimeError("Agent composition has been shut down")
        model_gateway = ProviderModelGateway(
            api_key,
            on_required_tool_choice_unsupported=(
                on_required_tool_choice_unsupported
            ),
        )
        profile_id = str(agent_profile or "").strip()
        registration = self._profile_registry.require(profile_id)
        adapter = registration.adapter
        extension = self._profile_extensions_by_id[profile_id]
        planning_policy = adapter.planning_policy
        if required_tool_names:
            planning_policy = RequiredToolPlanningPolicy(
                planning_policy,
                required_tool_names,
            )
        context_provider = context_provider_override or adapter.context_provider
        context_provider_factory = None
        if context_provider_override is None:
            context_provider_factory = extension.context_provider_factory()
            if context_provider_factory is not None:
                context_provider = None
        if context_provider is None and context_provider_factory is None:
            raise RuntimeError(
                f"{profile_id} ContextProvider is not configured"
            )
        resolved_compactor = conversation_compactor
        conversation_compactor_factory = None
        if resolved_compactor is None and context_compression_hook is not None:
            resolved_compactor = (
                ContextCompressionCoordinator(
                    context_compression_hook,
                    context_compression_settings,
                )
            )
        elif resolved_compactor is None:
            conversation_compactor_factory = lambda model_tasks: (
                ContextCompressionCoordinator(
                    ConversationCompactionService(
                        self._conversation_compaction_repository,
                        ModelBackedConversationSummarizer(model_tasks),
                    ),
                    context_compression_settings,
                )
            )
        resolved_compactor = resolved_compactor or (
            ContextCompressionCoordinator(
                context_compression_hook,
                context_compression_settings,
            )
            if context_compression_hook is not None
            else None
        )
        base_catalog = adapter.tool_catalog
        extras = tuple(extra_tool_registrations)
        tool_catalog = base_catalog
        normalized_modes = (
            None
            if allowed_tool_modes is None
            else frozenset(ToolExecutionMode(mode) for mode in allowed_tool_modes)
        )
        if extras or normalized_modes is not None:
            base_registrations = tuple(base_catalog.registrations())
            registrations = (*base_registrations, *extras)
            registration_by_name = {
                item.schema.name: item for item in registrations
            }
            extra_names = frozenset(item.schema.name for item in extras)

            def enabled_names(request: AgentRunRequest):
                enabled = set(base_catalog.enabled_names(request))
                if normalized_modes is not None:
                    enabled = {
                        name
                        for name in enabled
                        if registration_by_name[name].policy.mode
                        in normalized_modes
                    }
                enabled.update(extra_names)
                return enabled

            tool_catalog = InMemoryToolCatalog(
                registrations,
                enablement=enabled_names,
            )
        core = AgentCore(
            model_gateway=model_gateway,
            run_repository=self._repository,
            planning_policy=planning_policy,
            context_provider=context_provider,
            context_provider_factory=context_provider_factory,
            conversation_compactor=resolved_compactor,
            conversation_compactor_factory=conversation_compactor_factory,
            execution_state_factory=adapter.execution_state_factory,
            tool_catalog=tool_catalog,
            agent_role_guidance=agent_role_guidance,
            max_parallel_agents=max_parallel_agents,
            task_admission_evaluator=extension.task_admission(),
            long_task_dispatcher=(
                extension.create_long_task_dispatcher(
                    work_item_repository=self._work_item_repository,
                    long_task_repository=self._long_task_repository,
                    executor=long_task_executor,
                )
            ),
            approval_gateway=self._approval_gateway,
            tool_idempotency_gateway=self._tool_idempotency_gateway,
            runtime_limits=adapter.runtime_limits,
            recovery_policy=adapter.recovery_policy,
            tool_execution_limits=self._tool_execution_limits,
            output_processor=self._output_processor,
            output_repository=self._output_repository,
            output_publisher=self._output_publisher,
            execution_lease_store=self._execution_lease_store,
            execution_owner_id=self._repository.owner_id,
            execution_lease_duration_ms=self._repository.lease_duration_ms,
            delegation_repository=delegation_repository,
            child_core_submitter=child_core_submitter,
            child_request_factory=child_request_factory,
        )
        self._active_cores.add(core)
        return core

    def release_core(self, core: AgentCore) -> None:
        self._active_cores.discard(core)

    def create_core_for_request(
        self,
        request: AgentRunRequest,
        api_key: str,
        **kwargs,
    ) -> AgentCore:
        registration = self._profile_registry.for_request(request)
        return self.create_core(
            api_key,
            agent_profile=registration.id,
            **kwargs,
        )

    def agent_role_registry_for_request(
        self,
        request: AgentRunRequest,
    ) -> AgentRoleRegistry | None:
        return self._profile_registry.for_request(
            request
        ).adapter.agent_role_registry

    def bind_run_profile(
        self,
        request: AgentRunRequest,
        options: AgentCoreRunOptions,
    ) -> AgentCoreRunOptions:
        """Persist the selected product profile through the existing Run binding."""

        binding = options.binding
        if binding is None:
            return options
        registration = self._profile_registry.for_request(request)
        attributes = thaw_json_mapping(binding.attributes)
        expected = {
            "agentProfile": registration.id,
            "domainNamespace": registration.domain_namespace,
        }
        for name, value in expected.items():
            current = str(attributes.get(name) or "").strip()
            if current and current != value:
                raise ValueError(f"Run binding {name} conflicts with Agent profile")
        return replace(
            options,
            binding=replace(binding, attributes={**attributes, **expected}),
        )

    def agent_role_registry_for_persisted_profile(
        self,
        *,
        profile_id: str | None = None,
        domain_namespace: str | None = None,
    ) -> AgentRoleRegistry | None:
        normalized_profile = str(profile_id or "").strip()
        normalized_namespace = str(domain_namespace or "").strip()
        if normalized_profile:
            registration = self._profile_registry.require(normalized_profile)
            if (
                normalized_namespace
                and registration.domain_namespace != normalized_namespace
            ):
                raise ValueError(
                    "persisted Agent profile conflicts with domain namespace"
                )
        else:
            registration = self._profile_registry.for_domain_namespace(
                normalized_namespace
            )
        return registration.adapter.agent_role_registry

    def create_response_judge_policies(
        self,
        request: AgentRunRequest,
    ) -> tuple[ResponseJudgePolicy, ...]:
        """Register domain semantics; PurrA owns the model invocation."""

        if self._closed:
            raise RuntimeError("Agent composition has been shut down")
        registration = self._profile_registry.for_request(request)
        extension = self._profile_extensions_by_id[registration.id]
        return extension.response_judge_policies(request)

    def create_execution_session(self, signal) -> RunExecutionSession:
        return RunExecutionSession(
            self._execution_lease_store,
            owner_id=self._repository.owner_id,
            lease_duration_ms=self._repository.lease_duration_ms,
            external_signal=signal,
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

    async def resolve_approval(
        self,
        approval_id: str,
        approved: bool,
    ) -> ApprovalStatus | None:
        normalized = str(approval_id or "").strip()
        run_id = self._approval_runs.get(normalized)
        if not run_id:
            persisted = await approval_store.get_approval(
                self._db,
                normalized,
            )
            run_id = str((persisted or {}).get("run_id") or "").strip()
        if not run_id:
            return None
        status = await self._approval_gateway.resolve(
            run_id,
            normalized,
            ApprovalDecision.APPROVE if approved else ApprovalDecision.REJECT,
        )
        if status is not None:
            self._approval_runs.pop(normalized, None)
        return status

    async def release_run(self, run_id: str) -> None:
        """Drop live approval state when an application stream is closed."""

        normalized = str(run_id or "").strip()
        if not normalized:
            return
        await self._approval_gateway.cancel_pending(normalized)
        await self._artifact_claim_repository.release_for_run(normalized)
        stale = [
            approval_id
            for approval_id, pending_run_id in self._approval_runs.items()
            if pending_run_id == normalized
        ]
        for approval_id in stale:
            self._approval_runs.pop(approval_id, None)

    def track_background_run(self, task: asyncio.Task[None]) -> None:
        """Keep a detached Run alive until completion or app shutdown."""

        if self._closed:
            task.cancel()
            return
        self._background_run_tasks.add(task)

        def _discard(completed: asyncio.Task[None]) -> None:
            self._background_run_tasks.discard(completed)
            if completed.cancelled():
                return
            try:
                completed.result()
            except Exception:
                logger.exception("Detached Agent Run failed")

        task.add_done_callback(_discard)

    def profile_extension(self, profile_id: str) -> AgentProfileExtension:
        normalized = str(profile_id or "").strip()
        extension = self._profile_extensions_by_id.get(normalized)
        if extension is None:
            raise ValueError(f"Agent profile has no extension: {normalized}")
        return extension

    async def shutdown(self) -> None:
        """Fail closed and release all lifespan-owned live approval state."""

        self._closed = True
        background_tasks = tuple(self._background_run_tasks)
        for task in background_tasks:
            if not task.done():
                task.cancel()
        if background_tasks:
            await asyncio.gather(*background_tasks, return_exceptions=True)
        self._background_run_tasks.clear()
        active_cores = tuple(self._active_cores)
        self._active_cores.clear()
        if active_cores:
            await asyncio.gather(
                *(core.close() for core in active_cores),
                return_exceptions=True,
            )
        for extension in self._profile_extensions:
            extension.clear_active_executions()
        close = getattr(self._approval_gateway, "close", None)
        if close is not None:
            await close()
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
