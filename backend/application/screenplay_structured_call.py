"""Run screenplay-private model tasks inside an existing Agent Run."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Callable

from purra.contracts import AgentMessage, MessageOrigin, MessageRole
from purra.errors import ModelGatewayError
from purra.model_protocol import InvocationOutputLimit
from purra.structured_output import parse_json_object

from application.agent_run_service import AgentRunService
from application.model_runtime import (
    model_request_from_runtime,
    reasoning_mode_from_options,
)
from application.request_mapping import context_window_tokens
from application.screenplay_model_policy import screenplay_output_limit
from domains.screenplay_agent import ScreenplayIntentCommandMismatchError


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
        del db
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
        run_id: str,
        turn_id: str,
        system_instruction: str,
        user_payload: Mapping[str, Any],
        phase: str,
        signal=None,
    ) -> PublicModelResult:
        model_request = model_request_from_runtime(runtime)
        result = await self._runs.run_model_text(
            run_id=run_id,
            turn_id=f"{turn_id}:{phase}",
            api_key=runtime.apiKey.get_secret_value(),
            messages=_messages(system_instruction, user_payload),
            model_request=model_request,
            output_limit=_output_limit(runtime, model_request),
            reasoning_mode=reasoning_mode_from_options(runtime.options),
            signal=signal,
        )
        text = str(result.content or "")
        if not text.strip():
            raise ModelGatewayError(
                "screenplay public response was empty",
                code="empty_model_response",
                retryable=False,
            )
        return PublicModelResult(text=text, run_id=run_id)


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


def _output_limit(runtime, model_request) -> InvocationOutputLimit:
    window = context_window_tokens(
        runtime.contextWindow or runtime.options.get("context_window")
    )
    limit = screenplay_output_limit(
        model_request.capability_snapshot,
        model_request.options.get("max_tokens"),
    )
    if limit.max_tokens < window:
        return limit
    return InvocationOutputLimit(
        max_tokens=max(1_024, window // 4),
        source=limit.source,
        profile_max_tokens=limit.profile_max_tokens,
    )


__all__ = [
    "PublicModelResult",
    "ScreenplayStructuredCallService",
    "StructuredModelResult",
]
