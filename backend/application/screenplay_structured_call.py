"""Run screenplay structured calls and public final-response child Runs."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Callable

from purra.api import AgentCoreRunOptions, PlanningMode
from purra.contracts import (
    AgentMessage,
    AgentRunRequest,
    MessageOrigin,
    MessageRole,
    RunBinding,
    RunStatus,
)
from purra.errors import ModelGatewayError
from purra.model_protocol import InvocationOutputLimit
from purra.output import (
    PublicPresentationMode,
    ResponseTransactionMode,
    ResponseTransactionPolicy,
)
from purra.structured_output import parse_json_object

from application.agent_run_service import AgentRunService
from application.model_runtime import (
    fit_output_limit_to_context,
    model_request_from_runtime,
    reasoning_mode_from_options,
)
from application.request_mapping import context_window_tokens
from application.screenplay_model_policy import screenplay_output_limit
from application.screenplay_tool_calling import (
    BindRun,
    run_screenplay_child,
    screenplay_run_provenance,
)
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


class ScreenplayStructuredCallService:
    def __init__(self, db, *, composition) -> None:
        self._db = db
        self._runs = AgentRunService(composition)

    async def run_json(
        self,
        *,
        runtime,
        run_id: str,
        turn_id: str,
        system_instruction: str,
        user_payload: Mapping[str, Any],
        phase: str,
        repair_instruction: str,
        validate: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        signal=None,
    ) -> StructuredModelResult:
        messages = _messages(system_instruction, user_payload)
        model_request = model_request_from_runtime(
            runtime,
            json_object_output=True,
        )
        output_limit = _output_limit(runtime, model_request)
        last_error: Exception | None = None
        for attempt in range(2):
            result = await self._runs.run_model_text(
                run_id=run_id,
                turn_id=f"{turn_id}:{phase}:{attempt + 1}",
                api_key=runtime.apiKey.get_secret_value(),
                messages=messages,
                model_request=model_request,
                output_limit=output_limit,
                reasoning_mode=reasoning_mode_from_options(runtime.options),
                signal=signal,
            )
            try:
                value = dict(parse_json_object(result.content))
                normalized = validate(value) if validate else value
                return StructuredModelResult(dict(normalized), run_id)
            except ScreenplayIntentCommandMismatchError:
                raise
            except Exception as error:
                last_error = error
                if attempt == 0:
                    messages = (
                        *messages,
                        AgentMessage(role=MessageRole.ASSISTANT, content=result.content),
                        AgentMessage(
                            role=MessageRole.DEVELOPER,
                            content=(
                                str(repair_instruction or "").strip()
                                or "Return one complete JSON object matching the protocol."
                            ),
                        ),
                    )
        raise ModelGatewayError(
            str(last_error or "structured output invalid"),
            code="structured_output_invalid",
            retryable=False,
        )

    async def run_public_text(
        self,
        *,
        runtime,
        session_id: int,
        system_instruction: str,
        user_payload: Mapping[str, Any],
        binding_namespace: str,
        binding_aggregate_id: str,
        binding_command_id: str,
        task_id: str,
        unit_id: str,
        expected_part_key: str,
        conversation_turn_id: str,
        bind_run: BindRun | None = None,
        signal=None,
    ) -> PublicModelResult:
        model_request = model_request_from_runtime(runtime)
        window = context_window_tokens(
            runtime.contextWindow or runtime.options.get("context_window")
        )
        request = AgentRunRequest(
            messages=_messages(system_instruction, user_payload),
            model=model_request,
            domain_context=ScreenplayAgentDomainContext(
                project_id=binding_aggregate_id,
                task_id=task_id,
                unit_id=unit_id,
                target_role="final_response",
                expected_part_type="public_response",
                expected_part_key=expected_part_key,
                tool_access="final_response",
                locale=str(getattr(runtime, "locale", "zh-CN") or "zh-CN"),
            ).to_core_context(),
            session_id=session_id,
            mode="screenplay_final_response",
            context_window=window,
            tools_enabled=False,
            planning_mode=PlanningMode.REACTIVE,
            metadata={"locale": str(getattr(runtime, "locale", "zh-CN"))},
        )
        options = AgentCoreRunOptions(
            turn_id=conversation_turn_id,
            output_limit=_output_limit(
                runtime,
                model_request,
            ),
            default_context_window_tokens=window,
            model_supports_tools=False,
            force_planned_tool_choice=False,
            require_tool_call=False,
            reasoning_mode=reasoning_mode_from_options(runtime.options),
            provenance=screenplay_run_provenance(
                runtime,
                user_payload,
                model_request=model_request,
                output_contract="assistant_text",
                tool_protocol_contract="none",
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
        result = await run_screenplay_child(
            db=self._db,
            runs=self._runs,
            request=request,
            options=options,
            api_key=runtime.apiKey.get_secret_value(),
            signal=signal,
            bind_run=bind_run,
        )
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
        return PublicModelResult(text=text, run_id=result.run_id)


def _messages(
    system_instruction: str,
    user_payload: Mapping[str, Any],
) -> tuple[AgentMessage, ...]:
    return (
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
    )


def _output_limit(
    runtime,
    model_request,
) -> InvocationOutputLimit:
    limit = screenplay_output_limit(
        model_request.capability_snapshot,
        model_request.options.get("max_tokens"),
    )
    return fit_output_limit_to_context(
        limit,
        context_window_tokens(
            runtime.contextWindow or runtime.options.get("context_window")
        ),
    )


__all__ = [
    "PublicModelResult",
    "ScreenplayStructuredCallService",
    "StructuredModelResult",
]
