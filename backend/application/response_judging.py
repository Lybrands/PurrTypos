"""Application-owned adapter for model-backed semantic response judges."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from agent_core.contracts import (
    AgentMessage,
    MessageRole,
    ModelCompletion,
    ModelInvocation,
    ModelRequest,
    ReasoningMode,
    ResponseValidationResult,
    ToolChoiceMode,
)
from agent_core.errors import (
    ResponseJudgeContractError,
    UnsupportedModelFeatureError,
)
from agent_core.json_values import thaw_json_mapping
from agent_core.ports import CancellationSignal, ModelGateway


_JUDGE_CONNECTION_OPTION_KEYS = frozenset({"baseURL"})


class ModelJudgePolicy(Protocol):
    """Domain-owned prompt and strict verdict parser used by the adapter."""

    def build_messages(
        self,
        *,
        content: str,
        messages: Sequence[AgentMessage],
    ) -> tuple[AgentMessage, ...]: ...

    def evaluate(
        self,
        *,
        judgment_content: str,
        candidate_content: str,
    ) -> ResponseValidationResult: ...


@dataclass(frozen=True, slots=True)
class ModelBackedResponseJudge:
    """Use a no-tool, deterministic model completion for one judge policy."""

    model_gateway: ModelGateway
    model_request: ModelRequest
    policy: ModelJudgePolicy
    max_output_tokens: int = 1_200

    def __post_init__(self) -> None:
        if not isinstance(self.model_gateway, ModelGateway):
            raise TypeError("model-backed judge requires a ModelGateway")
        if not isinstance(self.model_request, ModelRequest):
            raise TypeError("model-backed judge requires a ModelRequest")
        maximum = self.max_output_tokens
        if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum <= 0:
            raise ValueError("judge max output tokens must be a positive integer")

    async def judge(
        self,
        *,
        content: str,
        messages: Sequence[AgentMessage],
        signal: CancellationSignal | None = None,
    ) -> ResponseValidationResult:
        judge_messages = self.policy.build_messages(
            content=content,
            messages=messages,
        )
        completion = await self._complete(
            judge_messages,
            reasoning_mode=ReasoningMode.DISABLED,
            signal=signal,
        )
        if (
            completion.message.role is not MessageRole.ASSISTANT
            or completion.message.tool_calls
            or not isinstance(completion.message.content, str)
        ):
            raise ResponseJudgeContractError(
                "semantic judge returned an unsupported message"
            )
        return self.policy.evaluate(
            judgment_content=completion.message.content,
            candidate_content=content,
        )

    async def _complete(
        self,
        messages: Sequence[AgentMessage],
        *,
        reasoning_mode: ReasoningMode,
        signal: CancellationSignal | None,
    ) -> ModelCompletion:
        invocation = ModelInvocation(
            request=_deterministic_request(self.model_request),
            tools=(),
            tool_choice=ToolChoiceMode.NONE,
            max_output_tokens=self.max_output_tokens,
            reasoning_mode=reasoning_mode,
        )
        try:
            return await self.model_gateway.complete(messages, invocation, signal)
        except UnsupportedModelFeatureError:
            if reasoning_mode is not ReasoningMode.DISABLED:
                raise
            return await self._complete(
                messages,
                reasoning_mode=ReasoningMode.DEFAULT,
                signal=signal,
            )


def _deterministic_request(request: ModelRequest) -> ModelRequest:
    caller_options = thaw_json_mapping(request.options)
    # A judge call inherits only provider connection routing. Sampling,
    # response-format and caller-owned function/tool fields are intentionally not
    # propagated, including unknown future fields.
    options = {
        key: caller_options[key]
        for key in _JUDGE_CONNECTION_OPTION_KEYS
        if key in caller_options
    }
    options["temperature"] = 0
    return ModelRequest(
        provider=request.provider,
        model=request.model,
        options=options,
    )
