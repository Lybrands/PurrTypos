"""The sole application composition root for complete PurrA runs."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import replace

from application.agent_delegation_policy import DELEGATION_GUIDANCE
from purra.contracts import (
    AgentMessage, MessageRole, MessageOrigin,
    AgentRunRequest,
    ApprovalDecision,
    ApprovalStatus,
    ContextBudgetClaim,
    ModelRequest,
    PlannerLimits,
    ReasoningMode,
    ToolExecutionLimits,
    ToolExecutionMode,
)
from purra.context_budget import resolve_context_budget_claims
from purra.errors import ContractViolationError
from purra.json_values import thaw_json_mapping
from purra.context_orchestration import (
    ContextCompressionCoordinator,
    ContextCompressionSettings,
)
from purra.api import (
    AgentComponentBinding,
    AgentCore,
    AgentCoreRunOptions,
    AgentPlanner,
    AgentModelTaskRunner,
    ContextStrategy,
    ExecutionProfile,
    AgentPreset,
    AgentTreePolicy,
)
from purra.model_invocation import AgentModelInvocationManager, ModelInvocationContext
from purra.operations import AgentOperationController
from purra.long_tasks import LongTaskRepository
from purra.events import AgentEvent, CoreEventType
from purra.ports import (
    ApprovalGateway,
    ContextCompressionHook,
    ConversationCompactor,
    ContextProvider,
    ExecutionLeaseStore,
    RunControlStore,
    ResponseJudgePolicy,
    RunCancellationProjector,
    ToolCatalog,
    ToolRegistration,
)
from purra.output import (
    AgentOutputJournalQuery,
    AgentOutputProcessor,
    AgentOutputRepository,
)
from infrastructure.persistence.sqlite_run_tree_repository import SqliteRunTreeRepository
from purra.tools import InMemoryToolCatalog
from application.conversation_compaction import ConversationCompactionService
from application.artifact_continuity import ArtifactContinuityCoordinator
from application.agent_profile_registry import (
    AgentProfile,
    AgentProfileRegistry,
)
from application.agent_tool_presentation import (
    enforce_agent_tool_catalog_presentation,
)
from application.shared_agent_context import with_shared_agent_context
from application.public_commentary_output import PublicCommentaryOutputProcessor
from application.assistant_text_facts import AssistantTextFactsProvider
from application.model_runtime import with_adapter_public_progress
from application.run_provenance import (
    _normalized_model_request_profile,
    digest_model_endpoint,
)
from infrastructure.persistence.model_request_diagnostics import model_request_observer
from infrastructure.models.provider_model_gateway import ProviderModelGateway
from infrastructure.persistence.run_store import runtime_limits_from_mapping
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
    SqliteRunControlStore,
)
from infrastructure.persistence.sqlite_run_snapshot_reader import (
    SqliteRunSnapshotReader,
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
    SqliteSessionArtifactAuthorizer,
)
from infrastructure.persistence.sqlite_long_task_repository import (
    LongTaskClaimGuard,
    SqliteLongTaskRepository,
)
from infrastructure.persistence import approval_store
from infrastructure.persistence.sqlite_approval_gateway import SqliteApprovalGateway
from config import AGENT_APPROVAL_TIMEOUT_SECONDS


logger = logging.getLogger(__name__)


def _uses_adapter_public_progress(request: AgentRunRequest) -> bool:
    return bool(
        request.tools_enabled
        and (
            request.metadata.get("responseAudience") != "internal"
            or request.metadata.get("progressAudience") == "public"
        )
    )


def _with_internal_child_audience(request: AgentRunRequest) -> AgentRunRequest:
    if not str(request.metadata.get("parentRunId") or "").strip():
        return request
    return replace(request, metadata={
        **request.metadata,
        "responseAudience": "internal",
        "progressAudience": "internal",
    })


def _model_task_identity(model_request: ModelRequest, reasoning_mode) -> dict:
    # Output shape is local to a structured task. Public progress is declared
    # by the host adapter; both leave the user's model configuration unchanged.
    options = thaw_json_mapping(model_request.options)
    options.pop("response_format", None)
    request = with_adapter_public_progress(replace(model_request, options=options))
    profile = _normalized_model_request_profile(
        request,
        endpoint_digest=digest_model_endpoint(str(options.get("baseURL") or "")),
        result_capacity_target_tokens=None,
    )
    encoded = json.dumps(
        profile, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return {
        "schemaVersion": 1,
        "modelRequestDigest": hashlib.sha256(encoded).hexdigest(),
        "requestedReasoningMode": ReasoningMode(reasoning_mode).value,
    }


def _host_component_bindings(profile_id: str):
    prefix = f"purrtypos.{str(profile_id or '').strip()}"
    return {
        role: AgentComponentBinding(
            f"{prefix}.{role}",
            "2" if role == "contextProvider" else "1",
        )
        for role in (
            "contextProvider",
            "conversationCompactor",
            "executionStateFactory",
            "planner",
            "planningPolicy",
            "taskAdmissionEvaluator",
            "longTaskDispatcher",
        )
    }


def _compose_tool_catalog(
    base: ToolCatalog,
    *,
    profile_id: str,
    extras: Sequence[ToolRegistration] = (),
    allowed_modes: frozenset[ToolExecutionMode] | None = None,
) -> ToolCatalog:
    additions = tuple(extras)
    base_registrations = tuple(base.registrations())
    if allowed_modes is not None:
        base_registrations = tuple(
            item
            for item in base_registrations
            if item.policy.mode in allowed_modes
        )
    registrations = (*base_registrations, *additions)
    by_name = {item.schema.name: item for item in registrations}
    available_names = frozenset(by_name)
    addition_names = frozenset(item.schema.name for item in additions)

    def enabled_names(request: AgentRunRequest) -> set[str]:
        enabled = set(base.enabled_names(request)) & available_names
        enabled.update(addition_names)
        return enabled

    catalog = InMemoryToolCatalog(registrations, enablement=enabled_names)
    return enforce_agent_tool_catalog_presentation(profile_id, catalog)


class AgentComposition:
    """Own process-scoped adapters and create request-scoped model runtimes."""

    def __init__(
        self,
        db,
        *,
        execution_db=None,
        run_begin_projector=None,
        run_commit_projector=None,
        run_cancellation_projectors: Sequence[RunCancellationProjector] = (),
        profile_factories: Sequence[
            Callable[..., AgentProfile]
        ] = (),
        provider_capabilities: ProviderCapabilityCache | None = None,
        approval_gateway: ApprovalGateway | None = None,
        tool_execution_limits: ToolExecutionLimits | None = None,
        long_task_claim_guard: LongTaskClaimGuard | None = None,
        memory_resource=None,
        profile_registry_factory=None,
        request_profile_router=None,
    ):
        self._db = db
        self._execution_db = execution_db or db
        self._execution_lease_store = SqliteRunControlStore(
            self._execution_db
        )
        self._run_control_store = SqliteRunControlStore(
            db,
            cancellation_projectors=run_cancellation_projectors,
        )
        self._run_snapshot_reader = SqliteRunSnapshotReader(db)
        self._conversation_compaction_repository = (
            SqliteConversationCompactionRepository(db)
        )
        self._repository = SqliteRunRepository(
            db,
        )
        self._output_repository = SqliteAgentOutputRepository(
            db,
            run_repository=self._repository,
            run_begin_projector=run_begin_projector,
            run_commit_projector=run_commit_projector,
        )
        self._run_tree_repository = SqliteRunTreeRepository(db)
        self._output_publisher = InProcessAgentOutputPublisher()
        self._output_processor = PublicCommentaryOutputProcessor(
            self._output_repository,
            self._output_publisher,
            is_child_run=self._is_child_agent_run,
        )
        self._tool_idempotency_gateway = SqliteToolIdempotencyGateway(
            db,
            owner_id=self._repository.owner_id,
        )
        self._artifact_claim_repository = SqliteArtifactClaimRepository(db)
        self._long_task_repository = SqliteLongTaskRepository(
            db, claim_guard=long_task_claim_guard,
        )
        self._artifact_continuity = ArtifactContinuityCoordinator(
            query=SqliteArtifactContinuityQuery(db),
            claims=self._artifact_claim_repository,
            authorizer=SqliteSessionArtifactAuthorizer(db),
        )
        self._provider_capabilities = (
            provider_capabilities or ProviderCapabilityCache()
        )
        self._memory_resource = memory_resource
        self._profiles = tuple(
            factory(
                db=db,
                artifact_continuity=self._artifact_continuity,
                long_task_repository=self._long_task_repository,
                execution_lease_store=self._execution_lease_store,
                memory_resource=self._memory_resource,
            )
            for factory in profile_factories
        )
        self._profile_registry = (
            AgentProfileRegistry(self._profiles)
            if profile_registry_factory is None
            else profile_registry_factory(self._profiles)
        )
        self._request_profile_router = request_profile_router
        self._approval_gateway = approval_gateway or SqliteApprovalGateway(db)
        self._tool_execution_limits = tool_execution_limits or ToolExecutionLimits(
            approval_timeout_seconds=AGENT_APPROVAL_TIMEOUT_SECONDS,
        )
        self._approval_runs: dict[str, str] = {}
        self._background_run_tasks: set[asyncio.Task[None]] = set()
        self._active_cores: set[AgentCore] = set()
        self._closed = False

    async def _is_child_agent_run(self, run_id: str) -> bool:
        row = await self._db.fetch_one(
            "SELECT parent_run_id FROM ai_agent_runs WHERE id = ?",
            [run_id],
        )
        return bool(row is not None and row.get("parent_run_id"))

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
    def run_control_store(self) -> RunControlStore:
        return self._run_control_store

    @property
    def run_tree_repository(self):
        return self._run_tree_repository

    @property
    def memory_resource(self):
        return self._memory_resource

    @property
    def run_snapshot_reader(self):
        return self._run_snapshot_reader

    @property
    def output_repository(self) -> AgentOutputRepository:
        return self._output_repository

    @property
    def output_journal(self) -> AgentOutputJournalQuery:
        return self._output_repository

    @property
    def output_notifications(self):
        return self._output_publisher

    @property
    def output_processor(self) -> AgentOutputProcessor:
        return self._output_processor

    async def report_operation_result(self, *, context, facts, runtime, signal=None):
        from application.operation_stage_output import OperationStageOutput
        reporter = getattr(self, "_operation_stage_output", None)
        if reporter is None:
            reporter = self._operation_stage_output = OperationStageOutput(
                self._db, self._output_processor, self._output_repository,
                self._create_stage_invocation,
            )
        await reporter.report(context=context, facts=facts, runtime=runtime, signal=signal)

    async def _create_stage_invocation(self, run_id, runtime):
        from application.model_runtime import model_request_from_runtime, reasoning_mode_from_options
        run = await self._db.fetch_one(
            "SELECT runtime_limits_json,deadline_at_ms,selected_context_window_tokens FROM ai_agent_runs WHERE id=?", [run_id])
        limits = runtime_limits_from_mapping(json.loads(run["runtime_limits_json"]))
        model = model_request_from_runtime(runtime)
        model = replace(
            model,
            max_generation_tokens=min(model.max_generation_tokens or 512, 512),
        )
        reasoning = reasoning_mode_from_options(model.options)
        manager = AgentModelInvocationManager(
            ProviderModelGateway(runtime.apiKey.get_secret_value(), request_observer=model_request_observer(self._db)),
            output_observer=self._output_processor,
            operation_controller=AgentOperationController(self._output_processor),
            invocation_timeout_ms=limits.provider_invocation_timeout_ms,
            runtime_limits=limits, budget_repository=self._repository,
        )
        context = ModelInvocationContext(
            run_id=run_id, turn_id=run_id, requested_reasoning_mode=reasoning,
            deadline_at_ms=run["deadline_at_ms"], deadline_code="run_deadline_exceeded",
            context_window_tokens=run["selected_context_window_tokens"],
        )
        return manager, context, model, reasoning

    async def create_model_task_runner(
        self,
        *,
        api_key: str,
        run_id: str,
        turn_id: str,
        reasoning_mode,
        model_request,
    ) -> AgentModelTaskRunner:
        """Compose a private model-task runner for an existing Run."""

        run = await self._db.fetch_one(
            "SELECT runtime_limits_json, deadline_at_ms, binding_attributes_json, "
            "requested_user_max_generation_tokens, selected_context_window_tokens "
            "FROM ai_agent_runs WHERE id = ?",
            [run_id],
        )
        if run is None:
            raise ValueError("model task requires an existing Run")
        attributes = json.loads(run["binding_attributes_json"] or "{}")
        identity = attributes.get("modelTaskIdentity")
        if not isinstance(identity, dict) or identity.get("schemaVersion") != 1:
            raise ContractViolationError(
                "model task requires the persisted Root model identity",
                code="model_request_identity_missing",
            )
        if (
            identity != _model_task_identity(model_request, reasoning_mode)
            or run["requested_user_max_generation_tokens"]
            != model_request.max_generation_tokens
        ):
            raise ContractViolationError(
                "model task differs from the persisted Root model request",
                code="model_request_identity_conflict",
            )
        limits = runtime_limits_from_mapping(json.loads(run["runtime_limits_json"]))
        return AgentModelTaskRunner(
            AgentModelInvocationManager(
                ProviderModelGateway(api_key, request_observer=model_request_observer(self._db)),
                output_observer=self._output_processor,
                operation_controller=AgentOperationController(self._output_processor),
                invocation_timeout_ms=limits.provider_invocation_timeout_ms,
                runtime_limits=limits,
                budget_repository=self._repository,
            ),
            ModelInvocationContext(
                run_id=run_id,
                turn_id=turn_id,
                requested_reasoning_mode=identity["requestedReasoningMode"],
                deadline_at_ms=run["deadline_at_ms"],
                deadline_code="run_deadline_exceeded",
                context_window_tokens=run["selected_context_window_tokens"],
            ),
            model_request,
        )

    @property
    def long_task_repository(self) -> LongTaskRepository:
        return self._long_task_repository

    @property
    def conversation_compaction_repository(
        self,
    ) -> SqliteConversationCompactionRepository:
        return self._conversation_compaction_repository

    async def prepare_request(
        self,
        request: AgentRunRequest,
        *,
        run_id: str | None = None,
    ) -> AgentRunRequest:
        """Hydrate renderer-independent, authoritative domain catalogs."""

        if self._request_profile_router is not None:
            route_for_request = getattr(
                self._request_profile_router,
                "route_for_request",
                None,
            )
            if callable(route_for_request):
                request = await route_for_request(request, run_id=run_id)
            elif run_id is None:
                request = self._request_profile_router.route_for_create(request)
            else:
                request = await self._request_profile_router.route_for_run(
                    request, run_id,
                )

        # Child requests must be internal before a profile is allowed to build
        # or inspect their context. Re-apply the boundary afterwards so a
        # profile cannot accidentally restore the Root's public audience.
        is_child_request = bool(
            str(request.metadata.get("parentRunId") or "").strip()
        )
        request = _with_internal_child_audience(request)
        profile = self._profile_registry.for_request(request)
        prepared = await profile.prepare_request(request)
        resolved = request if prepared is None else prepared
        if is_child_request:
            resolved = replace(resolved, metadata={
                **resolved.metadata,
                "responseAudience": "internal",
                "progressAudience": "internal",
            })
        if (
            resolved.tools_enabled
            and resolved.metadata.get("responseAudience") != "internal"
            and resolved.metadata.get("agentTreeEnabled", True) is not False
        ):
            if not any(message.content == DELEGATION_GUIDANCE for message in resolved.messages):
                resolved = replace(resolved, messages=(AgentMessage(
                    role=MessageRole.DEVELOPER, origin=MessageOrigin.HOST_CONTEXT,
                    content=DELEGATION_GUIDANCE), *resolved.messages))
        if _uses_adapter_public_progress(resolved):
            resolved = replace(
                resolved,
                model=with_adapter_public_progress(resolved.model),
            )
        return resolved

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
        agent_tree_enabled: bool = True,
        on_required_tool_choice_unsupported: Callable[[], None] | None = None,
        extra_tool_registrations: Sequence[ToolRegistration] = (),
        allowed_tool_modes: Collection[ToolExecutionMode] | None = None,
        context_compression_hook: ContextCompressionHook | None = None,
        context_compression_settings: ContextCompressionSettings = (
            ContextCompressionSettings()
        ),
        conversation_compactor: ConversationCompactor | None = None,
        context_provider_override: ContextProvider | None = None,
        long_task_executor=None,
        evidence_validator=None,
        public_progress_from_content: bool = False,
        public_progress_requirement=None,
        runtime_limits_override=None,
    ) -> AgentCore:
        if self._closed:
            raise RuntimeError("Agent composition has been shut down")
        model_gateway = ProviderModelGateway(
            api_key,
            request_observer=model_request_observer(self._db),
            on_required_tool_choice_unsupported=(
                on_required_tool_choice_unsupported
            ),
            public_progress_from_content=public_progress_from_content,
        )
        profile_id = str(agent_profile or "").strip()
        profile = self._profile_registry.require(profile_id)
        adapter = profile.adapter
        runtime_limits = runtime_limits_override or adapter.runtime_limits
        planning_policy = adapter.planning_policy
        context_provider = context_provider_override or adapter.context_provider
        context_provider_factory = None
        if context_provider_override is None:
            context_provider_factory = profile.context_provider_factory()
            if context_provider_factory is not None:
                context_provider = None
        if context_provider is None and context_provider_factory is None:
            raise RuntimeError(
                f"{profile_id} ContextProvider is not configured"
            )
        if context_provider is not None:
            context_provider = with_shared_agent_context(context_provider)
        if context_provider_factory is not None:
            domain_context_provider_factory = context_provider_factory
            context_provider_factory = lambda model_tasks: (
                with_shared_agent_context(
                    domain_context_provider_factory(model_tasks)
                )
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
        extras = tuple(extra_tool_registrations)
        normalized_modes = (
            None
            if allowed_tool_modes is None
            else frozenset(ToolExecutionMode(mode) for mode in allowed_tool_modes)
        )
        tool_catalog = _compose_tool_catalog(
            adapter.tool_catalog,
            profile_id=profile.id,
            extras=extras,
            allowed_modes=normalized_modes,
        )
        if public_progress_requirement is not None:
            tool_catalog = public_progress_requirement.wrap_tools(tool_catalog)
        resolved_context_strategy = getattr(
            adapter,
            "context_strategy",
            None,
        )
        if resolved_context_strategy is None:
            resolved_context_strategy = ContextStrategy.SINGLE_PASS
        configured_planner = getattr(adapter, "planner", None)
        planner = configured_planner
        if planner is None and planning_policy is not None:
            planner = AgentPlanner(
                model_gateway,
                limits=getattr(
                    adapter,
                    "planner_limits",
                    PlannerLimits(),
                ),
                output_observer=self._output_processor,
                result_validator=getattr(
                    adapter,
                    "planning_result_validator",
                    None,
                ),
            )
        operations = AgentOperationController(self._output_processor)
        execution_profile = ExecutionProfile(
            planner=planner,
            planning_policy=planning_policy,
            context_strategy=resolved_context_strategy,
            task_admission_evaluator=profile.task_admission(),
            long_task_dispatcher=self.create_long_task_dispatcher(
                profile_id,
                executor=long_task_executor,
            ),
        )
        preset = AgentPreset(
            id=profile.id,
            revision="1",
            tool_catalog=tool_catalog,
            execution_profile=execution_profile,
            context_provider=context_provider,
            context_provider_factory=context_provider_factory,
            conversation_compactor=resolved_compactor,
            conversation_compactor_factory=conversation_compactor_factory,
            execution_state_factory=adapter.execution_state_factory,
            runtime_limits=runtime_limits,
            recovery_policy=adapter.recovery_policy,
            component_bindings=_host_component_bindings(profile.id),
            agent_tree_policy=(
                getattr(adapter, "agent_tree_policy", None)
                or AgentTreePolicy(
                    max_agents_per_root=16,
                    result_presentation_instruction=None,
                )
            ) if agent_tree_enabled else None,
        )
        core = AgentCore(
            model_gateway=model_gateway,
            run_repository=self._repository,
            run_tree_repository=self._run_tree_repository if agent_tree_enabled else None,
            preset=preset,
            approval_gateway=self._approval_gateway,
            tool_idempotency_gateway=self._tool_idempotency_gateway,
            tool_execution_limits=(
                getattr(adapter, "tool_execution_limits", None)
                or self._tool_execution_limits
            ),
            output_processor=self._output_processor,
            output_repository=self._output_repository,
            output_publisher=self._output_publisher,
            operation_controller=operations,
            execution_lease_store=self._execution_lease_store,
            execution_owner_id=self._repository.owner_id,
            execution_lease_duration_ms=self._repository.lease_duration_ms,
            evidence_validator=evidence_validator,
        )
        if long_task_executor is not None:
            bind_core = getattr(long_task_executor, "bind_agent_core", None)
            if callable(bind_core):
                bind_core(core)
        self._active_cores.add(core)
        return core

    async def report_agent_results(self, run_id, results, signal=None):
        owners = [core for core in self._active_cores if run_id in core.active_agent_root_ids]
        if len(owners) != 1:
            from purra.errors import ContractViolationError
            raise ContractViolationError("Agent feedback requires its active owning Core", code="agent_feedback_unavailable")
        await owners[0].report_agent_results(run_id, results, signal)

    def release_core(self, core: AgentCore) -> None:
        self._active_cores.discard(core)

    def create_core_for_request(
        self,
        request: AgentRunRequest,
        api_key: str,
        **kwargs,
    ) -> AgentCore:
        profile = self._profile_registry.for_request(request)
        validator_hook = getattr(
            profile,
            "model_input_evidence_validator",
            None,
        )
        if callable(validator_hook) and "evidence_validator" not in kwargs:
            kwargs["evidence_validator"] = validator_hook(request)
        runtime_limits_hook = getattr(profile, "runtime_limits_for_request", None)
        if callable(runtime_limits_hook) and "runtime_limits_override" not in kwargs:
            kwargs["runtime_limits_override"] = runtime_limits_hook(request)
        kwargs.setdefault(
            "public_progress_from_content",
            _uses_adapter_public_progress(request),
        )
        kwargs.setdefault("agent_tree_enabled",
            request.tools_enabled
            and request.metadata.get("responseAudience") != "internal"
            and request.metadata.get("agentTreeEnabled", True) is not False)
        return self.create_core(
            api_key,
            agent_profile=profile.id,
            **kwargs,
        )

    def bind_run_profile(
        self,
        request: AgentRunRequest,
        options: AgentCoreRunOptions,
    ) -> AgentCoreRunOptions:
        """Persist the selected product profile through the existing Run binding."""

        if isinstance(options.committed_result_facts_provider, AssistantTextFactsProvider):
            options = replace(
                options,
                committed_result_facts_provider=AssistantTextFactsProvider(self._db),
            )

        binding = options.binding
        profile = self._profile_registry.for_request(request)
        claims = tuple(options.context_claims)
        claims_hook = getattr(profile, "context_budget_claims", None)
        if callable(claims_hook):
            replacements = tuple(claims_hook(request))
            replacement_names = {claim.name for claim in replacements}
            claims = tuple(
                claim for claim in claims if claim.name not in replacement_names
            ) + replacements
        if binding is None:
            return replace(options, context_claims=claims)
        attributes = thaw_json_mapping(binding.attributes)
        expected = {
            "agentProfile": profile.id,
            "domainNamespace": profile.domain_namespace,
        }
        attribute_hook = getattr(profile, "run_binding_attributes", None)
        if callable(attribute_hook):
            expected.update(dict(attribute_hook(request)))
        expected["modelTaskIdentity"] = _model_task_identity(
            request.model, options.reasoning_mode,
        )
        for name, value in expected.items():
            current = attributes.get(name)
            if current not in (None, "") and current != value:
                raise ValueError(f"Run binding {name} conflicts with Agent profile")
        return replace(
            options,
            context_claims=claims,
            binding=replace(binding, attributes={**attributes, **expected}),
        )

    def create_response_judge_policies(
        self,
        request: AgentRunRequest,
    ) -> tuple[ResponseJudgePolicy, ...]:
        """Register domain semantics; PurrA owns the model invocation."""

        if self._closed:
            raise RuntimeError("Agent composition has been shut down")
        profile = self._profile_registry.for_request(request)
        return profile.response_judge_policies(request)

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

    def profile(self, profile_id: str) -> AgentProfile:
        return self._profile_registry.require(profile_id)

    def create_long_task_dispatcher(self, profile_id: str, *, executor=None):
        profile = self.profile(profile_id)
        dispatcher = profile.create_long_task_dispatcher(
            long_task_repository=self._long_task_repository,
            executor=executor,
        )
        configure = getattr(dispatcher, "set_execution_failure_handler", None)
        if callable(configure):
            configure(self._settle_dispatch_failure)
        return dispatcher

    async def _settle_dispatch_failure(self, run_id, error):
        from datetime import datetime, timezone
        from purra.run_state import RunStateMachine
        from purra.ports import RunCommit
        from purra.output import RunLifecycleOutputDraft

        snapshot = await self._repository.get(run_id)
        if snapshot.terminal:
            return
        transition = RunStateMachine.fail(
            snapshot, "novel_analysis_dispatch_failed: " + str(error)[:500])
        event = AgentEvent(type=CoreEventType.RUN_FAILED, run_id=run_id,
                           payload={"status": "failed", "error": transition.after.error})
        identity = await self._db.fetch_one(
            "SELECT turn_id FROM ai_agent_run_events WHERE run_id=? "
            "AND source_event_key=? AND kind='run.lifecycle' ORDER BY sequence LIMIT 1",
            [run_id, f"run:{run_id}:running"])
        await self._output_repository.commit_run_lifecycle(
            run_id,
            RunCommit(step_updates=transition.step_updates,
                      terminal_status=transition.after.status,
                      error=transition.after.error, events=(event,)),
            RunLifecycleOutputDraft(source_event_key=f"run:{run_id}:failed",
                status=transition.after.status,
                turn_id=identity.get("turn_id") if identity else None,
                payload=event.payload, occurred_at=datetime.now(timezone.utc)))
        self.observe_event(event)

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
        for profile in self._profiles:
            profile.clear_active_executions()
        if self._memory_resource is not None:
            await self._memory_resource.close()
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
