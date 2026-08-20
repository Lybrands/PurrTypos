"""Produce validated screenplay candidates inside an active Run."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from purra.contracts import AgentMessage, MessageOrigin, MessageRole, ReasoningMode
from purra.errors import ModelGatewayError
from purra.structured_output import parse_json_object

from application.agent_run_service import AgentRunService
from application.model_runtime import model_request_from_runtime
from application.screenplay_model_policy import screenplay_output_limit
from domains.screenplay_agent.agent_context import ScreenplayAgentDomainContext


@dataclass(frozen=True, slots=True)
class ScreenplayCandidateRunResult:
    run_id: str
    candidate: Mapping[str, Any]


class ScreenplayCandidateModelService:
    """Execute one bounded candidate model task inside the current Run."""

    def __init__(self, db, *, composition) -> None:
        del db
        self._runs = AgentRunService(composition)

    async def run_candidate(
        self,
        *,
        runtime,
        run_id: str,
        turn_id: str,
        system_instruction: str,
        user_payload: Mapping[str, Any],
        domain_context: ScreenplayAgentDomainContext,
        reasoning_mode: ReasoningMode = ReasoningMode.DEFAULT,
        host_candidate_template: Mapping[str, Any] | None = None,
        normalize: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
        signal=None,
    ) -> ScreenplayCandidateRunResult:
        model_request = model_request_from_runtime(
            runtime,
            json_object_output=host_candidate_template is None,
        )
        output_limit = screenplay_output_limit(
            model_request.capability_snapshot,
            model_request.options.get("max_tokens"),
        )
        messages = (
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
        last_error: Exception | None = None
        for attempt in range(2):
            result = await self._runs.run_model_text(
                run_id=run_id,
                turn_id=(
                    f"{turn_id}:{domain_context.unit_id}:candidate:{attempt + 1}"
                ),
                api_key=runtime.apiKey.get_secret_value(),
                messages=messages,
                model_request=model_request,
                output_limit=output_limit,
                reasoning_mode=reasoning_mode,
                signal=signal,
            )
            try:
                candidate = _candidate(result.content, host_candidate_template)
                if normalize is not None:
                    candidate = dict(normalize(candidate))
                return ScreenplayCandidateRunResult(run_id=run_id, candidate=candidate)
            except Exception as error:
                last_error = error
                if attempt == 0:
                    messages = (
                        *messages,
                        AgentMessage(role=MessageRole.ASSISTANT, content=result.content),
                        AgentMessage(
                            role=MessageRole.DEVELOPER,
                            content=(
                                "The candidate failed validation. Return only one "
                                "complete value matching the requested candidate schema."
                            ),
                        ),
                    )
        raise ModelGatewayError(
            str(last_error or "screenplay candidate invalid"),
            code="candidate_validation_failed",
            retryable=False,
        )


def _candidate(
    content: str,
    template: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if template is not None:
        text = str(content or "").strip()
        if not text:
            raise ValueError("screenplay scene candidate is empty")
        return {
            "payload": {**dict(template), "sceneText": text},
            "contentText": text,
        }
    raw = dict(parse_json_object(content))
    text = str(raw.pop("contentText", "") or "")
    return {"payload": raw, "contentText": text}


__all__ = ["ScreenplayCandidateModelService", "ScreenplayCandidateRunResult"]
