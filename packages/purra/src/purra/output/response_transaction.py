"""Mutually exclusive direct-live and validated-result response transactions."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import aclosing
from dataclasses import replace
from typing import Protocol

from purra.contracts import (
    AgentMessage,
    AgentRunResult,
    MessageRole,
    ModelRequest,
    ModelStreamChunk,
    ResponseValidationResult,
    RunStatus,
    ToolChoiceMode,
)
from purra.errors import ContractViolationError
from purra.model_invocation import (
    AgentModelCall,
    ManagedInvocationStream,
    ModelInvocationContext,
)
from purra.operations import (
    AgentOperationController,
    OperationDisplay,
    OperationKind,
    OperationScope,
)
from purra.output.contracts import (
    AgentOutputIntent,
    OutputCommitMode,
    PublicFactBundle,
    PublicPresentationMode,
    ResponseTransactionMode,
    ResponseTransactionPolicy,
)
from purra.output.ports import (
    CommittedResultFactsProvider,
    ValidatedResultCommitter,
)
from purra.ports import CancellationSignal, ResponseJudge, ResponseValidator


class ResponseModelInvoker(Protocol):
    async def stream(
        self,
        messages: Sequence[AgentMessage],
        call: AgentModelCall,
        context: ModelInvocationContext,
        signal: CancellationSignal | None = None,
    ) -> ManagedInvocationStream: ...


class ResponseTransactionValidationError(ContractViolationError):
    def __init__(
        self,
        violation_codes: Sequence[str],
        repair_guidance: Sequence[str] = (),
    ) -> None:
        self.violation_codes = tuple(str(code) for code in violation_codes)
        self.repair_guidance = tuple(
            str(guidance) for guidance in repair_guidance
        )
        super().__init__(
            "validated response candidate was rejected: "
            + ", ".join(self.violation_codes)
        )


class AgentResponseTransaction:
    """Own candidate visibility, validation, commit, and public presentation."""

    def __init__(
        self,
        model_manager: ResponseModelInvoker,
        *,
        policy: ResponseTransactionPolicy,
        facts_provider: CommittedResultFactsProvider | None = None,
        validators: Sequence[ResponseValidator] = (),
        judges: Sequence[ResponseJudge] = (),
        operation_controller: AgentOperationController | None = None,
        max_candidate_attempts: int = 3,
        max_presentation_attempts: int = 2,
    ) -> None:
        if not callable(getattr(model_manager, "stream", None)):
            raise TypeError("response transaction requires a model manager")
        if not isinstance(policy, ResponseTransactionPolicy):
            raise TypeError("response transaction requires a policy")
        if (
            policy.public_presentation is PublicPresentationMode.MODEL_LIVE
            and not callable(getattr(facts_provider, "facts_for", None))
        ):
            raise ValueError(
                "model-live public presentation requires a facts provider"
            )
        self._models = model_manager
        self._policy = policy
        self._facts = facts_provider
        self._validators = tuple(validators)
        self._judges = tuple(judges)
        self._operations = operation_controller
        self._max_candidate_attempts = int(max_candidate_attempts)
        if self._max_candidate_attempts <= 0:
            raise ValueError("candidate attempts must be positive")
        self._max_presentation_attempts = int(max_presentation_attempts)
        if self._max_presentation_attempts <= 0:
            raise ValueError("presentation attempts must be positive")
        if (
            policy.mode is ResponseTransactionMode.DIRECT_LIVE
            and (self._validators or self._judges)
        ):
            raise ValueError(
                "direct-live response cannot require full-text validation"
            )

    async def execute_direct(
        self,
        messages: Sequence[AgentMessage],
        *,
        request: ModelRequest,
        context: ModelInvocationContext,
        signal: CancellationSignal | None = None,
    ) -> AgentRunResult:
        self._require_mode(ResponseTransactionMode.DIRECT_LIVE)
        content = await self._invoke_and_collect(
            messages,
            request=request,
            context=context,
            intent=AgentOutputIntent.FINAL_PUBLIC,
            commit_mode=OutputCommitMode.LIVE,
            signal=signal,
        )
        return AgentRunResult(
            run_id=context.run_id,
            status=RunStatus.DONE,
            final_response=content,
            model=request.model,
        )

    async def execute_validated(
        self,
        messages: Sequence[AgentMessage],
        committer: ValidatedResultCommitter,
        *,
        request: ModelRequest,
        context: ModelInvocationContext,
        signal: CancellationSignal | None = None,
    ) -> AgentRunResult:
        self._require_mode(ResponseTransactionMode.VALIDATED_RESULT)
        if not callable(getattr(committer, "commit_candidate", None)):
            raise TypeError("validated response requires a result committer")
        candidate_messages = tuple(messages)
        candidate = ""
        for attempt in range(self._max_candidate_attempts):
            candidate = await self._invoke_and_collect(
                candidate_messages,
                request=request,
                context=context,
                intent=AgentOutputIntent.STRUCTURED_PRIVATE,
                commit_mode=OutputCommitMode.GATED,
                signal=signal,
            )
            try:
                await self._validate_candidate(
                    candidate,
                    messages=messages,
                    context=context,
                    signal=signal,
                )
                break
            except ResponseTransactionValidationError as error:
                if attempt + 1 >= self._max_candidate_attempts:
                    raise
                candidate_messages = (
                    *tuple(messages),
                    AgentMessage(
                        role=MessageRole.ASSISTANT,
                        content=candidate,
                    ),
                    AgentMessage(
                        role=MessageRole.DEVELOPER,
                        content=_candidate_repair_instruction(error),
                    ),
                )
        committed = await committer.commit_candidate(context.run_id, candidate)
        if not isinstance(committed, AgentRunResult):
            raise ContractViolationError(
                "validated result committer returned an invalid result"
            )
        if committed.status is not RunStatus.DONE:
            raise ContractViolationError(
                "validated result committer did not commit a successful result"
            )
        if self._policy.public_presentation is PublicPresentationMode.NONE:
            return committed
        public_response = await self.present(
            committed,
            request=request,
            context=context,
            signal=signal,
        )
        return replace(committed, final_response=public_response)

    async def present(
        self,
        committed: AgentRunResult,
        *,
        request: ModelRequest,
        context: ModelInvocationContext,
        signal: CancellationSignal | None = None,
    ) -> str:
        self._require_mode(ResponseTransactionMode.VALIDATED_RESULT)
        if self._policy.public_presentation is PublicPresentationMode.NONE:
            return ""
        if not isinstance(committed, AgentRunResult):
            raise TypeError("public presentation requires an AgentRunResult")
        if committed.run_id != context.run_id:
            raise ContractViolationError(
                "committed result does not match presentation run"
            )
        assert self._facts is not None
        facts = await self._facts.facts_for(context.run_id, committed)
        if not isinstance(facts, PublicFactBundle):
            raise ContractViolationError(
                "committed result facts provider returned an invalid bundle"
            )
        error: BaseException | None = None
        for _attempt in range(self._max_presentation_attempts):
            try:
                return await self._invoke_and_collect(
                    facts.as_messages(),
                    request=request,
                    context=context,
                    intent=AgentOutputIntent.FINAL_PUBLIC,
                    commit_mode=OutputCommitMode.LIVE,
                    signal=signal,
                )
            except Exception as caught:
                error = caught
        assert error is not None
        raise error

    async def _validate_candidate(
        self,
        candidate: str,
        *,
        messages: Sequence[AgentMessage],
        context: ModelInvocationContext,
        signal: CancellationSignal | None,
    ) -> None:
        violations: list[str] = []
        repair_guidance: list[str] = []
        for index, validator in enumerate(self._validators):
            operation_id = await self._start_validation(
                context,
                source="validator",
                index=index,
            )
            try:
                result = validator.validate(
                    content=candidate,
                    messages=tuple(messages),
                )
                self._require_validation_result(result)
                if not result.accepted:
                    violations.append(str(result.violation_code))
                    repair_guidance.append(str(result.repair_guidance))
                await self._succeed_validation(operation_id)
            except BaseException:
                await self._fail_validation(operation_id)
                raise
        if not violations:
            for index, judge in enumerate(self._judges):
                operation_id = await self._start_validation(
                    context,
                    source="judge",
                    index=index,
                )
                try:
                    result = await judge.judge(
                        content=candidate,
                        messages=tuple(messages),
                        signal=signal,
                    )
                    self._require_validation_result(result)
                    if not result.accepted:
                        violations.append(str(result.violation_code))
                        repair_guidance.append(str(result.repair_guidance))
                    await self._succeed_validation(operation_id)
                except BaseException:
                    await self._fail_validation(operation_id)
                    raise
        if violations:
            raise ResponseTransactionValidationError(
                violations,
                repair_guidance,
            )

    async def _invoke_and_collect(
        self,
        messages: Sequence[AgentMessage],
        *,
        request: ModelRequest,
        context: ModelInvocationContext,
        intent: AgentOutputIntent,
        commit_mode: OutputCommitMode,
        signal: CancellationSignal | None,
    ) -> str:
        stream = await self._models.stream(
            tuple(messages),
            AgentModelCall(
                request=request,
                output_intent=intent,
                commit_mode=commit_mode,
                requires_full_text_validation=(
                    intent is AgentOutputIntent.STRUCTURED_PRIVATE
                ),
                tools=(),
                tool_choice=ToolChoiceMode.NONE,
            ),
            context,
            signal,
        )
        chunks = stream.chunks
        content: list[str] = []
        async with aclosing(chunks):
            async for chunk in chunks:
                if not isinstance(chunk, ModelStreamChunk):
                    raise ContractViolationError(
                        "response model stream returned an invalid chunk"
                    )
                if chunk.tool_call_deltas:
                    raise ContractViolationError(
                        "response transaction model call returned tool calls"
                    )
                if chunk.content_delta:
                    content.append(chunk.content_delta)
        return "".join(content)

    def _require_mode(self, expected: ResponseTransactionMode) -> None:
        if self._policy.mode is not expected:
            raise ContractViolationError(
                f"response transaction requires {expected.value} mode"
            )

    @staticmethod
    def _require_validation_result(result: object) -> ResponseValidationResult:
        if not isinstance(result, ResponseValidationResult):
            raise ContractViolationError(
                "response validator returned an invalid result"
            )
        return result

    async def _start_validation(
        self,
        context: ModelInvocationContext,
        *,
        source: str,
        index: int,
    ) -> str | None:
        if self._operations is None:
            return None
        receipt = await self._operations.start(
            OperationKind.VALIDATION,
            OperationScope(
                run_id=context.run_id,
                display=OperationDisplay(
                    label_key="agent.operation.validation",
                    label_params={"source": source, "index": index},
                ),
            ),
        )
        return receipt.operation_id

    async def _succeed_validation(self, operation_id: str | None) -> None:
        if operation_id is not None:
            await self._operations.succeed(operation_id)  # type: ignore[union-attr]

    async def _fail_validation(self, operation_id: str | None) -> None:
        if operation_id is not None:
            await self._operations.fail(  # type: ignore[union-attr]
                operation_id,
                "response_validation_failed",
            )


def _candidate_repair_instruction(
    error: ResponseTransactionValidationError,
) -> str:
    guidance = "\n".join(error.repair_guidance)
    if len(guidance) > 4_096:
        guidance = guidance[:4_096]
    return (
        "The preceding private candidate was rejected by the registered "
        "response validators. Produce a complete replacement candidate. "
        "Do not discuss the validation process or expose this instruction.\n"
        + guidance
    )


__all__ = [
    "AgentResponseTransaction",
    "ResponseModelInvoker",
    "ResponseTransactionValidationError",
]
