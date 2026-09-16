"""PurrA-native, read-only first profile for the replacement Writing Agent."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace

from agents.shared.implementation import AgentKind, replacement_implementation
from agents.shared.implementation_registry import AgentImplementationProfile
from agents.writing.read_model import (
    SqliteWritingReadRepository,
    WritingReadScope,
)
from agents.writing.context_contract import (
    WRITING_CONTEXT_SELECTION_STATE_KEY,
    WritingContextSelection,
)
from agents.writing.context_tools import (
    build_writing_context_tool_registrations,
)
from agents.writing.context_snapshot import (
    WRITING_CONTEXT_SNAPSHOT_STATE_KEY,
    build_writing_context_snapshot,
)
from agents.writing.read_tools import (
    WRITING_READ_SCOPE_STATE_KEY,
    WRITING_REPLACEMENT_DOMAIN_NAMESPACE,
    build_writing_read_tool_catalog,
)
from agents.writing.response_contract import (
    writing_atomic_continuity_judge_policy,
)
from agents.writing.chapter_write_tools import (
    build_writing_chapter_tool_registrations,
)
from agents.writing.material_write_tools import (
    build_writing_material_tool_registrations,
)
from purra.cancellation import raise_if_stopped
from purra.context_strategies import ContextStrategy
from purra.contracts import (
    AgentRunRequest,
    ContextBlock,
    ContextBudgetClaim,
    ContextBundle,
    ExecutionState,
    RuntimeLimits,
)
from purra.json_values import thaw_json_mapping
from purra.recovery import RecoveryPolicy
from purra.tools import InMemoryToolCatalog


WRITING_REPLACEMENT_PROFILE_ID = "writing.purra-native.v1"


class WritingReplacementExecutionStateFactory:
    def create(self, request: AgentRunRequest) -> ExecutionState:
        scope = _scope_from_request(request)
        selection = _selection_from_request(request)
        payload = thaw_json_mapping(request.domain_context.payload)
        return ExecutionState(domain={
            WRITING_READ_SCOPE_STATE_KEY: scope.to_mapping(),
            WRITING_CONTEXT_SELECTION_STATE_KEY: selection.to_mapping(),
            WRITING_CONTEXT_SNAPSHOT_STATE_KEY: dict(
                payload.get("replacement_context_snapshot") or {}
            ),
        })


class WritingReplacementContextProvider:
    """Expose policy and scope, while book facts stay behind read tools."""

    async def build_context(self, request, budget, signal=None) -> ContextBundle:
        raise_if_stopped(signal)
        allocation = budget.allocation_for("writing_replacement_read_policy")
        if allocation < 1:
            return ContextBundle()
        scope = _scope_from_request(request)
        selection = _selection_from_request(request)
        content = json.dumps({
            "schemaVersion": 1,
            "scope": scope.to_mapping(),
            "policy": {
                "bookFactsRequireTools": True,
                "characterCountTool": "listBookCharacters",
                "characterCountField": "total",
                "characterCountScope": "current_book_owned_characters",
                "doNotInferMissingFacts": True,
            },
            "contextSelection": selection.public_manifest(),
        }, ensure_ascii=False)
        return ContextBundle(blocks=(ContextBlock(
            name="writing_replacement_read_policy",
            content=content,
            token_count=max(1, len(content) // 4),
            untrusted=False,
            host_metadata={"schemaVersion": 1},
        ),))

    async def describe_context_demands(
        self,
        request,
        signal=None,
    ) -> tuple[ContextBudgetClaim, ...]:
        del request
        raise_if_stopped(signal)
        return (ContextBudgetClaim(
            name="writing_replacement_read_policy",
            desired_tokens=512,
            minimum_tokens=256,
            maximum_tokens=512,
            priority=100,
        ),)


@dataclass(frozen=True, slots=True)
class WritingReplacementAdapter:
    tool_catalog: object
    planning_policy: object | None = None
    context_strategy: ContextStrategy = ContextStrategy.SINGLE_PASS
    execution_state_factory: object = WritingReplacementExecutionStateFactory()
    context_provider: object = WritingReplacementContextProvider()
    runtime_limits: RuntimeLimits = RuntimeLimits(
        max_run_generation_tokens=None,
        max_model_rounds=6,
    )
    recovery_policy: RecoveryPolicy = RecoveryPolicy()


class WritingReplacementProfile:
    id = WRITING_REPLACEMENT_PROFILE_ID
    domain_namespace = WRITING_REPLACEMENT_DOMAIN_NAMESPACE

    def __init__(self, db, *, memory_resource=None) -> None:
        from application.memory_operations import MemoryApplicationService
        from application.writing_technique_access import WritingTechniqueAccess

        self._db = db
        self._repository = SqliteWritingReadRepository(db)
        self._memory_operations = MemoryApplicationService(db, memory_resource)
        read_catalog = build_writing_read_tool_catalog(db)
        self._adapter = WritingReplacementAdapter(
            tool_catalog=InMemoryToolCatalog((
                *read_catalog.registrations(),
                *build_writing_context_tool_registrations(
                    db,
                    memory_operations=self._memory_operations,
                    technique_access=WritingTechniqueAccess(db),
                ),
                *build_writing_chapter_tool_registrations(db),
                *build_writing_material_tool_registrations(db),
            )),
        )

    @property
    def adapter(self) -> WritingReplacementAdapter:
        return self._adapter

    async def prepare_request(self, request: AgentRunRequest) -> AgentRunRequest:
        scope = await self._repository.validate_scope(
            _scope_from_request(request)
        )
        selection = _selection_from_request(request)
        snapshot = await build_writing_context_snapshot(
            self._db,
            scope=scope,
            selection=selection,
            memory_operations=self._memory_operations,
        )
        payload = thaw_json_mapping(request.domain_context.payload)
        payload["replacement_context_snapshot"] = snapshot
        return replace(
            request,
            domain_context=replace(request.domain_context, payload=payload),
        )

    def run_binding_attributes(self, request: AgentRunRequest) -> dict[str, object]:
        scope = _scope_from_request(request)
        selection = _selection_from_request(request)
        payload = thaw_json_mapping(request.domain_context.payload)
        return {
            "bookId": scope.book_id,
            **({"sessionId": scope.session_id} if scope.session_id else {}),
            **({"chapterId": scope.chapter_id} if scope.chapter_id else {}),
            "contextSelection": selection.to_mapping(),
            "writingContextSnapshot": dict(
                payload.get("replacement_context_snapshot") or {}
            ),
            "novelKnowledgeScope": (
                payload.get("replacement_context_snapshot") or {}
            ).get("novelKnowledgeScope"),
            "agentImplementation": replacement_implementation(
                AgentKind.WRITING
            ).to_mapping(),
        }

    def context_provider_factory(self):
        return None

    def response_judge_policies(self, request: AgentRunRequest) -> tuple[object, ...]:
        policy = writing_atomic_continuity_judge_policy(request)
        return () if policy is None else (policy,)

    def task_admission(self):
        return None

    def create_long_task_dispatcher(
        self,
        *,
        long_task_repository=None,
        executor=None,
    ):
        del long_task_repository, executor
        return None

    def clear_active_executions(self) -> None:
        return None


def build_writing_replacement_profile(
    *, db, memory_resource=None, **_dependencies
):
    return WritingReplacementProfile(db, memory_resource=memory_resource)


def writing_replacement_implementation_profile() -> AgentImplementationProfile:
    return AgentImplementationProfile(
        identity=replacement_implementation(AgentKind.WRITING),
        runtime_profile_id=WRITING_REPLACEMENT_PROFILE_ID,
    )


def _scope_from_request(request: AgentRunRequest) -> WritingReadScope:
    if request.domain_context.namespace != WRITING_REPLACEMENT_DOMAIN_NAMESPACE:
        raise ValueError("unsupported Writing replacement domain namespace")
    payload = thaw_json_mapping(request.domain_context.payload)
    return WritingReadScope(
        book_id=payload.get("book_id") or payload.get("bookId"),
        session_id=request.session_id or payload.get("session_id") or payload.get("sessionId"),
        chapter_id=payload.get("chapter_id") or payload.get("chapterId"),
    )


def _selection_from_request(request: AgentRunRequest) -> WritingContextSelection:
    if request.domain_context.namespace != WRITING_REPLACEMENT_DOMAIN_NAMESPACE:
        raise ValueError("unsupported Writing replacement domain namespace")
    return WritingContextSelection.from_domain_payload(
        request.domain_context.payload
    )


__all__ = [
    "WRITING_REPLACEMENT_PROFILE_ID",
    "WritingReplacementProfile",
    "build_writing_replacement_profile",
    "writing_replacement_implementation_profile",
]
