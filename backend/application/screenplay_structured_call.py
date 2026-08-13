"""Screenplay structured calls submitted through the complete PurrA pipeline.

The product owns the JSON protocol and validation only.  PurrA owns the Run,
Provider invocation, private candidate transaction, recovery, timing and
canonical journal.  Structured candidates are never projected into Assistant
text.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Callable

from purra.api import AgentCoreRunOptions
from purra.contracts import (
    AgentMessage,
    AgentRunRequest,
    MessageOrigin,
    MessageRole,
    ResponseValidationResult,
    RunBinding,
    RunProvenance,
    RunStatus,
)
from purra.errors import ModelGatewayError
from purra.output import (
    PublicPresentationMode,
    ResponseTransactionMode,
    ResponseTransactionPolicy,
)
from purra.model_protocol import (
    InvocationOutputLimit,
    resolve_invocation_output_limit,
)
from purra.structured_output import parse_json_object
from application.model_runtime import (
    model_request_from_runtime,
    reasoning_mode_from_options,
    run_execution_intent,
)
from application.request_mapping import context_window_tokens
from application.run_provenance import digest_model_endpoint
from domains.screenplay_agent import ScreenplayIntentCommandMismatchError
from domains.screenplay_agent.agent_context import ScreenplayAgentDomainContext


@dataclass(frozen=True, slots=True)
class StructuredModelResult:
    value: dict[str, Any]
    run_id: str


@dataclass(frozen=True, slots=True)
class PublicModelResult:
    text: str
    run_id: str


class _StructuredResultValidator:
    """Capture only a candidate that passed the product JSON contract."""

    def __init__(
        self,
        *,
        repair_instruction: str,
        validate: Callable[[dict[str, Any]], dict[str, Any]] | None,
    ) -> None:
        self._repair_instruction = str(repair_instruction or "").strip()
        self._validate = validate
        self.value: dict[str, Any] | None = None
        self.error: Exception | None = None

    def validate(
        self,
        *,
        content: str,
        messages: Sequence[AgentMessage],
    ) -> ResponseValidationResult:
        del messages
        try:
            value = dict(parse_json_object(content))
            normalized = self._validate(value) if self._validate else value
        except Exception as error:
            if isinstance(error, Exception):
                self.error = error
            return ResponseValidationResult(
                violation_code="screenplay.structured_output_invalid",
                repair_guidance=(
                    self._repair_instruction
                    or "Return one complete JSON object matching the required protocol."
                ),
            )
        self.value = dict(normalized)
        self.error = None
        return ResponseValidationResult()


class ScreenplayStructuredCallService:
    def __init__(self, db, *, composition) -> None:
        self._db = db
        self._composition = composition

    async def run_json(
        self,
        *,
        runtime,
        session_id: int,
        prompt: str,
        system_instruction: str,
        user_payload: Mapping[str, Any],
        binding_namespace: str,
        binding_aggregate_id: str,
        binding_command_id: str,
        conversation_turn_id: str | None = None,
        task_id: str | None = None,
        phase: str,
        repair_instruction: str,
        validate: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        execution_progress_fields: Mapping[str, str] | None = None,
        project_execution: (
            Callable[[dict[str, Any]], Sequence[str]] | None
        ) = None,
        signal=None,
    ) -> StructuredModelResult:
        # These former projection inputs remain accepted during call-site
        # migration, but structured content can no longer create public text.
        del execution_progress_fields, project_execution

        model_request = model_request_from_runtime(
            runtime,
            json_object_output=True,
        )
        window = context_window_tokens(
            runtime.contextWindow or runtime.options.get("context_window")
        )
        resolved_output_limit = resolve_invocation_output_limit(
            model_request.capability_snapshot,
            model_request.options.get("max_tokens"),
        )
        output_limit = resolved_output_limit
        if output_limit.max_tokens >= window:
            output_limit = InvocationOutputLimit(
                max_tokens=max(1_024, window // 4),
                source=output_limit.source,
                profile_max_tokens=output_limit.profile_max_tokens,
            )
        validator = _StructuredResultValidator(
            repair_instruction=repair_instruction,
            validate=validate,
        )
        request = AgentRunRequest(
            messages=(
                AgentMessage(
                    role=MessageRole.SYSTEM,
                    content=system_instruction,
                    origin=MessageOrigin.HOST_CONTEXT,
                ),
                AgentMessage(
                    role=MessageRole.USER,
                    content=json.dumps(
                        user_payload,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                ),
            ),
            model=model_request,
            domain_context=ScreenplayAgentDomainContext(
                project_id=binding_aggregate_id,
                task_id=str(task_id or binding_command_id),
                unit_id=str(binding_command_id or phase),
                target_role=phase,
                expected_part_type="structured_private",
                expected_part_key=phase,
                tool_access="evidence_read",
                locale=str(getattr(runtime, "locale", "zh-CN") or "zh-CN"),
            ).to_core_context(),
            session_id=session_id,
            mode=phase,
            context_window=window,
            tools_enabled=False,
            metadata={"locale": str(getattr(runtime, "locale", "zh-CN"))},
        )
        options = AgentCoreRunOptions(
            turn_id=conversation_turn_id,
            output_limit=output_limit,
            default_context_window_tokens=window,
            model_supports_tools=False,
            force_planned_tool_choice=False,
            require_tool_call=False,
            reasoning_mode=reasoning_mode_from_options(runtime.options),
            provenance=_provenance(runtime, user_payload),
            binding=RunBinding(
                namespace=binding_namespace,
                aggregate_id=binding_aggregate_id,
                command_id=binding_command_id,
            ),
            response_validators=(validator,),
            response_transaction_policy=ResponseTransactionPolicy(
                mode=ResponseTransactionMode.VALIDATED_RESULT,
                public_presentation=PublicPresentationMode.NONE,
            ),
        )
        options = self._composition.bind_run_profile(request, options)
        core = self._composition.create_core_for_request(
            request,
            runtime.apiKey.get_secret_value(),
        )
        handle = None
        cancel_watcher: asyncio.Task[None] | None = None
        try:
            handle = await core.submit(request, options=options)
            if signal is not None and hasattr(signal, "wait"):
                cancel_watcher = asyncio.create_task(
                    _cancel_on_signal(signal, handle)
                )
            result = await handle.wait()
        finally:
            if cancel_watcher is not None:
                cancel_watcher.cancel()
                await asyncio.gather(cancel_watcher, return_exceptions=True)
            self._composition.release_core(core)

        if result.status is RunStatus.CANCELED:
            raise asyncio.CancelledError
        if result.status is not RunStatus.DONE or validator.value is None:
            if isinstance(
                validator.error,
                ScreenplayIntentCommandMismatchError,
            ):
                raise validator.error
            error = validator.error
            raise ModelGatewayError(
                str(error or result.error or "structured output invalid"),
                code=(
                    "structured_output_invalid"
                    if error is not None
                    else str(result.error or "structured_output_invalid")
                ),
                retryable=False,
            )
        return StructuredModelResult(validator.value, handle.run_id)

    async def run_public_text(
        self,
        *,
        runtime,
        session_id: int,
        prompt: str,
        system_instruction: str,
        user_payload: Mapping[str, Any],
        binding_namespace: str,
        binding_aggregate_id: str,
        binding_command_id: str,
        phase: str,
        task_id: str | None = None,
        conversation_turn_id: str | None = None,
        signal=None,
    ) -> PublicModelResult:
        model_request = model_request_from_runtime(runtime)
        window = context_window_tokens(
            runtime.contextWindow or runtime.options.get("context_window")
        )
        resolved_output_limit = resolve_invocation_output_limit(
            model_request.capability_snapshot,
            model_request.options.get("max_tokens"),
        )
        output_limit = resolved_output_limit
        if output_limit.max_tokens >= window:
            output_limit = InvocationOutputLimit(
                max_tokens=max(1_024, window // 4),
                source=output_limit.source,
                profile_max_tokens=output_limit.profile_max_tokens,
            )
        request = AgentRunRequest(
            messages=(
                AgentMessage(
                    role=MessageRole.SYSTEM,
                    content=system_instruction,
                    origin=MessageOrigin.HOST_CONTEXT,
                ),
                AgentMessage(
                    role=MessageRole.USER,
                    content=json.dumps(
                        user_payload,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                ),
            ),
            model=model_request,
            domain_context=ScreenplayAgentDomainContext(
                project_id=binding_aggregate_id,
                task_id=str(task_id or binding_command_id),
                unit_id=str(binding_command_id or phase),
                target_role=phase,
                expected_part_type="public_response",
                expected_part_key=phase,
                tool_access="evidence_read",
                locale=str(getattr(runtime, "locale", "zh-CN") or "zh-CN"),
            ).to_core_context(),
            session_id=session_id,
            mode=phase,
            context_window=window,
            tools_enabled=False,
            metadata={"locale": str(getattr(runtime, "locale", "zh-CN"))},
        )
        options = AgentCoreRunOptions(
            turn_id=conversation_turn_id,
            output_limit=output_limit,
            default_context_window_tokens=window,
            model_supports_tools=False,
            force_planned_tool_choice=False,
            require_tool_call=False,
            reasoning_mode=reasoning_mode_from_options(runtime.options),
            provenance=_provenance(
                runtime,
                user_payload,
                output_contract="assistant_text",
            ),
            binding=RunBinding(
                namespace=binding_namespace,
                aggregate_id=binding_aggregate_id,
                command_id=binding_command_id,
            ),
            response_transaction_policy=ResponseTransactionPolicy(
                mode=ResponseTransactionMode.DIRECT_LIVE,
                public_presentation=PublicPresentationMode.NONE,
            ),
        )
        options = self._composition.bind_run_profile(request, options)
        core = self._composition.create_core_for_request(
            request,
            runtime.apiKey.get_secret_value(),
        )
        cancel_watcher: asyncio.Task[None] | None = None
        try:
            handle = await core.submit(request, options=options)
            if signal is not None and hasattr(signal, "wait"):
                cancel_watcher = asyncio.create_task(
                    _cancel_on_signal(signal, handle)
                )
            result = await handle.wait()
        finally:
            if cancel_watcher is not None:
                cancel_watcher.cancel()
                await asyncio.gather(cancel_watcher, return_exceptions=True)
            self._composition.release_core(core)
        if result.status is RunStatus.CANCELED:
            raise asyncio.CancelledError
        if result.status is not RunStatus.DONE:
            raise ModelGatewayError(
                str(result.error or "screenplay public response failed"),
                code=str(result.error or "screenplay_public_response_failed"),
                retryable=False,
            )
        text = str(result.final_response or "")
        if not text.strip():
            raise ModelGatewayError(
                "screenplay public response was empty",
                code="empty_model_response",
                retryable=False,
            )
        return PublicModelResult(text=text, run_id=handle.run_id)

async def _cancel_on_signal(signal, handle) -> None:
    await signal.wait()
    await handle.cancel("screenplay_agent_canceled")


def _provenance(
    runtime,
    payload: Mapping[str, Any],
    *,
    output_contract: str = "json_object",
) -> RunProvenance:
    model = str(runtime.options.get("model") or "").strip()
    profile = json.dumps(
        {
            "provider": runtime.apiProvider,
            "model": model,
            "contextWindow": runtime.contextWindow,
            "payload": payload,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    request = model_request_from_runtime(runtime, json_object_output=True)
    return RunProvenance(
        model_provider=str(runtime.apiProvider or "openai").strip().lower(),
        model_name=model,
        context_window=context_window_tokens(
            runtime.contextWindow or runtime.options.get("context_window")
        ),
        endpoint_digest=digest_model_endpoint(runtime.baseURL),
        request_profile_digest=hashlib.sha256(profile.encode("utf-8")).hexdigest(),
        capability_snapshot=request.capability_snapshot.to_mapping(
            include_digest=True
        ),
        execution_intent=run_execution_intent(
            request,
            reasoning_mode_from_options(runtime.options),
            output_contract=output_contract,
            tool_protocol_contract="no_tools",
        ),
    )


__all__ = [
    "PublicModelResult",
    "ScreenplayStructuredCallService",
    "StructuredModelResult",
]
