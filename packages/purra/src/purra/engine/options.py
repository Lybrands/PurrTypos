"""Per-run options for the high-level PurrA pipeline."""

from __future__ import annotations

from dataclasses import dataclass

from purra.contracts import (
    ContextBudgetClaim,
    ReasoningMode,
    ResponseConstraints,
    RunBinding,
    RunLineage,
    RunProvenance,
    TaskPlan,
)
from purra.normalization import (
    optional_non_negative_int,
    positive_int,
)
from purra.model_protocol import InvocationOutputLimit
from purra.output.contracts import (
    PublicPresentationMode,
    ResponseTransactionMode,
    ResponseTransactionPolicy,
)
from purra.output.ports import CommittedResultFactsProvider
from purra.ports import ResponseJudge, ResponseJudgePolicy, ResponseValidator
from purra.task_admission import (
    ExecutionMode,
    LongTaskDispatchReceipt,
    TaskAdmissionDecision,
)


@dataclass(frozen=True, slots=True)
class DurableTaskContinuation:
    """Trusted host receipt for resuming one existing durable task in a new Run."""

    source_root_run_id: str
    continuation_command: str
    plan: TaskPlan
    admission: TaskAdmissionDecision
    receipt: LongTaskDispatchReceipt

    def __post_init__(self) -> None:
        for name in ("source_root_run_id", "continuation_command"):
            value = str(getattr(self, name) or "").strip()
            if not value:
                raise ValueError(f"durable continuation {name} is required")
            object.__setattr__(self, name, value)
        if not isinstance(self.plan, TaskPlan):
            raise TypeError("durable continuation plan must be TaskPlan")
        if (
            not isinstance(self.admission, TaskAdmissionDecision)
            or self.admission.mode is not ExecutionMode.DURABLE
        ):
            raise ValueError("durable continuation requires durable admission")
        if not isinstance(self.receipt, LongTaskDispatchReceipt):
            raise TypeError("durable continuation receipt is invalid")


@dataclass(frozen=True, slots=True)
class AgentCoreRunOptions:
    """Per-run generic limits; domain content remains in injected adapters."""

    context_claims: tuple[ContextBudgetClaim, ...] = ()
    turn_id: str | None = None
    output_limit: InvocationOutputLimit | None = None
    default_context_window_tokens: int = 128_000
    safety_reserve_tokens: int | None = None
    runtime_reserve_tokens: int | None = None
    minimum_message_tokens: int | None = None
    model_supports_tools: bool = True
    force_planned_tool_choice: bool = True
    require_tool_call: bool | None = None
    reasoning_mode: ReasoningMode = ReasoningMode.DEFAULT
    provenance: RunProvenance | None = None
    lineage: RunLineage | None = None
    binding: RunBinding | None = None
    response_constraints: ResponseConstraints = ResponseConstraints()
    response_validators: tuple[ResponseValidator, ...] = ()
    response_judges: tuple[ResponseJudge, ...] = ()
    response_judge_policies: tuple[ResponseJudgePolicy, ...] = ()
    response_transaction_policy: ResponseTransactionPolicy | None = None
    committed_result_facts_provider: CommittedResultFactsProvider | None = None
    durable_continuation: DurableTaskContinuation | None = None

    def __post_init__(self) -> None:
        claims = tuple(self.context_claims)
        names = [claim.name for claim in claims]
        if len(names) != len(set(names)):
            raise ValueError("context claim names must be unique")
        object.__setattr__(self, "context_claims", claims)
        normalized_turn_id = str(self.turn_id or "").strip() or None
        object.__setattr__(self, "turn_id", normalized_turn_id)
        object.__setattr__(
            self,
            "default_context_window_tokens",
            positive_int(
                self.default_context_window_tokens,
                "default_context_window_tokens",
            ),
        )
        if self.output_limit is not None and not isinstance(
            self.output_limit,
            InvocationOutputLimit,
        ):
            raise TypeError("output limit must be InvocationOutputLimit")
        for name in (
            "safety_reserve_tokens",
            "runtime_reserve_tokens",
            "minimum_message_tokens",
        ):
            object.__setattr__(self, name, optional_non_negative_int(
                getattr(self, name), name
            ))
        object.__setattr__(self, "model_supports_tools", bool(self.model_supports_tools))
        object.__setattr__(
            self,
            "force_planned_tool_choice",
            bool(self.force_planned_tool_choice),
        )
        if self.require_tool_call is not None:
            object.__setattr__(
                self,
                "require_tool_call",
                bool(self.require_tool_call),
            )
        object.__setattr__(self, "reasoning_mode", ReasoningMode(self.reasoning_mode))
        if self.provenance is not None and not isinstance(
            self.provenance,
            RunProvenance,
        ):
            raise TypeError("run provenance must be a RunProvenance value")
        if self.lineage is not None and not isinstance(self.lineage, RunLineage):
            raise TypeError("run lineage must be a RunLineage value")
        if self.binding is not None and not isinstance(self.binding, RunBinding):
            raise TypeError("run binding must be a RunBinding value")
        if not isinstance(self.response_constraints, ResponseConstraints):
            raise TypeError("response constraints must be ResponseConstraints")
        validators = tuple(self.response_validators)
        if any(not isinstance(item, ResponseValidator) for item in validators):
            raise TypeError("response validators must implement ResponseValidator")
        object.__setattr__(self, "response_validators", validators)
        judges = tuple(self.response_judges)
        if any(not isinstance(item, ResponseJudge) for item in judges):
            raise TypeError("response judges must implement ResponseJudge")
        object.__setattr__(self, "response_judges", judges)
        judge_policies = tuple(self.response_judge_policies)
        if any(
            not isinstance(item, ResponseJudgePolicy)
            for item in judge_policies
        ):
            raise TypeError(
                "response judge policies must implement ResponseJudgePolicy"
            )
        object.__setattr__(self, "response_judge_policies", judge_policies)
        policy = self.response_transaction_policy
        if policy is not None and not isinstance(
            policy,
            ResponseTransactionPolicy,
        ):
            raise TypeError(
                "response transaction policy must be ResponseTransactionPolicy"
            )
        facts_provider = self.committed_result_facts_provider
        if facts_provider is not None and not isinstance(
            facts_provider,
            CommittedResultFactsProvider,
        ):
            raise TypeError(
                "committed result facts provider must implement facts_for"
            )
        if self.durable_continuation is not None and not isinstance(
            self.durable_continuation,
            DurableTaskContinuation,
        ):
            raise TypeError("durable continuation must be DurableTaskContinuation")
        requires_full_text = bool(
            self.response_constraints.exact_top_level_item_count is not None
            or validators
            or judges
            or judge_policies
        )
        if (
            policy is not None
            and policy.mode is ResponseTransactionMode.DIRECT_LIVE
            and requires_full_text
        ):
            raise ValueError(
                "direct-live response cannot require full-text validation"
            )
        if (
            policy is not None
            and policy.public_presentation
            is PublicPresentationMode.MODEL_LIVE
            and facts_provider is None
        ):
            raise ValueError(
                "model-live public presentation requires a facts provider"
            )

    @property
    def resolved_response_transaction_policy(self) -> ResponseTransactionPolicy:
        if self.response_transaction_policy is not None:
            return self.response_transaction_policy
        requires_full_text = bool(
            self.response_constraints.exact_top_level_item_count is not None
            or self.response_validators
            or self.response_judges
            or self.response_judge_policies
        )
        return ResponseTransactionPolicy(
            mode=(
                ResponseTransactionMode.VALIDATED_RESULT
                if requires_full_text
                else ResponseTransactionMode.DIRECT_LIVE
            )
        )
