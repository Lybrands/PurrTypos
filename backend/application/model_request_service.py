"""Public model request facade for conversations and bounded background calls."""

from dataclasses import dataclass

from purra.contracts import AgentMessage, ModelInvocation, ModelFinishReason, ReasoningMode
from purra.context_budget import max_generation_tokens_for_context
from purra.model_protocol import (
    TaskCapabilityRequirements, FeatureRequirement, ResultCapacitySource,
    resolve_invocation_output_budget, constrain_output_budget_to_context,
)
from purra.errors import ContractViolationError, ModelGatewayError
from purra.cancellation import raise_if_stopped
from application.model_runtime import model_request_from_runtime, reasoning_mode_from_options, runtime_from_settings
from infrastructure.models.provider_model_gateway import ProviderModelGateway
from infrastructure.persistence.model_request_diagnostics import model_request_observer


@dataclass(frozen=True)
class BackgroundModelContext:
    owner: str
    result_capacity_target_tokens: int

    def __post_init__(self):
        if not self.owner.strip() or type(self.result_capacity_target_tokens) is not int or self.result_capacity_target_tokens <= 0:
            raise ValueError("background model call requires an owner and positive result capacity")


class ModelRequestService:
    resolve = staticmethod(model_request_from_runtime)
    runtime_from_settings = staticmethod(runtime_from_settings)

    async def complete(self, *, api_key, runtime, context: BackgroundModelContext, messages, signal=None, db=None):
        if not isinstance(context, BackgroundModelContext):
            raise TypeError("background model execution context is required")
        raise_if_stopped(signal)
        request = self.resolve(runtime, requirements=TaskCapabilityRequirements(
            reasoning_mode=ReasoningMode.DEFAULT,
            tool_calling=FeatureRequirement.OPTIONAL, streaming_required=False, cancellation_required=True,
        ))
        budget = resolve_invocation_output_budget(
            request.capability_snapshot, max_generation_tokens=request.max_generation_tokens,
            result_capacity_target_tokens=context.result_capacity_target_tokens,
            result_capacity_source=ResultCapacitySource.WORKFLOW_POLICY,
        )
        budget = constrain_output_budget_to_context(budget, max_generation_tokens=max_generation_tokens_for_context(
            window_tokens=request.capability_snapshot.context_window_tokens))
        invocation = ModelInvocation(request=request, output_budget=budget,
                                     reasoning_mode=reasoning_mode_from_options(request.options))
        gateway = ProviderModelGateway(api_key, request_observer=model_request_observer(db, owner=context.owner) if db is not None else None)
        result = await gateway.complete(tuple(m if isinstance(m, AgentMessage) else AgentMessage.from_mapping(m) for m in messages),
                                        invocation, signal)
        raise_if_stopped(signal)
        if result.applied_generation_limit != budget.max_generation_tokens:
            raise ContractViolationError("model gateway did not apply the requested generation limit", code="model_gateway_contract_violation")
        if result.finish_reason is ModelFinishReason.LENGTH:
            raise ModelGatewayError("model output is incomplete", code="model_output_truncated", retryable=False)
        if result.finish_reason is None:
            raise ModelGatewayError("model completion ended without a finish reason", code="upstream_stream_interrupted", retryable=True)
        if result.finish_reason is not ModelFinishReason.STOP:
            raise ModelGatewayError("model completion ended unsuccessfully", code="model_terminal_failure", retryable=False)
        return result
