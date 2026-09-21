"""Read-only replacement Screenplay conversation without formal Operations."""

from __future__ import annotations

import json
from hashlib import sha256

from agents.screenplay.contracts import SCREENPLAY_REPLACEMENT_DOMAIN_NAMESPACE
from agents.screenplay.conversation_projection import (
    SCREENPLAY_REPLACEMENT_ROOT_BINDING,
)
from agents.screenplay.profile import SCREENPLAY_ORDINARY_INTERACTION
from agents.shared.saved_model_binding import capture_saved_model_binding
from application.agent_run_service import AgentRunService
from application.agent_conversation_input import conversation_messages
from application.model_runtime import (
    model_request_from_runtime,
    reasoning_mode_from_options,
    run_execution_intent,
    runtime_context_window_tokens,
)
from application.run_provenance import digest_model_endpoint
from purra.api import AgentCoreRunOptions
from purra.contracts import (
    AgentMessage,
    AgentRunRequest,
    DomainContext,
    MessageOrigin,
    MessageRole,
    PlanningMode,
    RunBinding,
    RunProvenance,
)
from purra.output import (
    PublicPresentationMode,
    ResponseTransactionMode,
    ResponseTransactionPolicy,
)
from purra.json_values import thaw_json_mapping


class ScreenplayReplacementOrdinaryService:
    def __init__(self, db, composition, *, runs=None) -> None:
        self._db = db
        self._runs = runs or AgentRunService(composition)

    async def build_request(
        self,
        *,
        project_id: str,
        session_id: int,
        turn_id: str,
        command_id: str,
        prompt: str,
        runtime,
    ) -> AgentRunRequest:
        question = str(prompt or "").strip()
        if not question or len(question) > 20_000:
            raise ValueError("Screenplay ordinary prompt is invalid")
        history = await _ordinary_history(
            self._db,
            project_id=project_id,
            session_id=session_id,
            command_id=command_id,
        )
        model = model_request_from_runtime(
            runtime, task_reasoning_preference="economical"
        )
        runtime_binding = await capture_saved_model_binding(self._db, runtime)
        if runtime_binding is None:
            raise ValueError("Screenplay requires an exact saved model binding")
        context_window = runtime_context_window_tokens(runtime)
        messages = conversation_messages(
            (
                AgentMessage(
                    MessageRole.SYSTEM,
                    (
                        "You are the read-only assistant for this screenplay project. "
                        "Use inspectScreenplayProjectV1 before asserting current "
                        "project facts. Never claim that you created, revised, or "
                        "accepted a deliverable."
                    ),
                    origin=MessageOrigin.HOST_CONTEXT,
                ),
                *history,
                AgentMessage(MessageRole.USER, question),
            ),
            context_window=context_window,
        )
        return AgentRunRequest(
            messages=messages,
            model=model,
            session_id=session_id,
            domain_context=DomainContext(
                namespace=SCREENPLAY_REPLACEMENT_DOMAIN_NAMESPACE,
                payload={
                    "schemaVersion": 1,
                    "projectId": project_id,
                    "turnId": turn_id,
                    "commandId": command_id,
                },
            ),
            mode="screenplay_ordinary",
            tools_enabled=True,
            planning_mode=PlanningMode.REACTIVE,
            context_window=context_window,
            metadata={
                "interactionKind": SCREENPLAY_ORDINARY_INTERACTION,
                "runtimeBinding": runtime_binding,
                "agentTreeEnabled": False,
                "locale": str(getattr(runtime, "locale", "zh-CN") or "zh-CN"),
                "progressAudience": "public",
            },
        )

    async def run(
        self,
        *,
        project_id: str,
        session_id: int,
        turn_id: str,
        command_id: str,
        prompt: str,
        runtime,
        signal,
        run_binding_lifecycle,
    ):
        request = await self.build_request(
            project_id=project_id,
            session_id=session_id,
            turn_id=turn_id,
            command_id=command_id,
            prompt=prompt,
            runtime=runtime,
        )
        model = request.model
        profile_digest = sha256(json.dumps({
            "schemaVersion": 1,
            "mode": request.mode,
            "domain": {
                "namespace": request.domain_context.namespace,
                "payload": thaw_json_mapping(request.domain_context.payload),
            },
            "messages": [message.to_mapping() for message in request.messages],
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        async for update in self._runs.run(
            request=request,
            api_key=runtime.apiKey.get_secret_value(),
            options=AgentCoreRunOptions(
                turn_id=turn_id,
                default_context_window_tokens=request.context_window,
                force_planned_tool_choice=False,
                require_tool_call=False,
                reasoning_mode=reasoning_mode_from_options(model.options),
                provenance=RunProvenance(
                    model_provider=model.provider,
                    model_name=model.model,
                    context_window=request.context_window,
                    endpoint_digest=digest_model_endpoint(runtime.baseURL),
                    request_profile_digest=profile_digest,
                    capability_snapshot=model.capability_snapshot.to_mapping(
                        include_digest=True
                    ),
                    execution_intent=run_execution_intent(
                        model,
                        reasoning_mode_from_options(model.options),
                        output_contract="screenplay_ordinary_text_v1",
                        tool_protocol_contract="screenplay_ordinary_read_tools_v1",
                    ),
                ),
                binding=RunBinding(
                    namespace=SCREENPLAY_REPLACEMENT_ROOT_BINDING,
                    aggregate_id=project_id,
                    command_id=command_id,
                    attributes={
                        "interactionKind": SCREENPLAY_ORDINARY_INTERACTION,
                        **run_binding_lifecycle.run_binding_attributes(),
                    },
                ),
                response_transaction_policy=ResponseTransactionPolicy(
                    mode=ResponseTransactionMode.DIRECT_LIVE,
                    public_presentation=PublicPresentationMode.NONE,
                ),
            ),
            signal=signal,
            run_binding_lifecycle=run_binding_lifecycle,
        ):
            yield update


async def _ordinary_history(
    db,
    *,
    project_id: str,
    session_id: int,
    command_id: str,
) -> tuple[AgentMessage, ...]:
    rows = await db.fetch_all(
        "SELECT prompt, final_response FROM ai_agent_runs "
        "WHERE binding_namespace = ? AND binding_aggregate_id = ? "
        "AND session_id = ? AND binding_command_id != ? AND status = 'done' "
        "AND json_extract(binding_attributes_json, '$.interactionKind') = ? "
        "ORDER BY rowid DESC LIMIT 24",
        [
            SCREENPLAY_REPLACEMENT_ROOT_BINDING,
            project_id,
            int(session_id),
            command_id,
            SCREENPLAY_ORDINARY_INTERACTION,
        ],
    )
    messages: list[AgentMessage] = []
    for row in reversed(rows):
        response = str(row.get("final_response") or "").strip()
        if not response:
            continue
        messages.extend((
            AgentMessage(MessageRole.USER, str(row.get("prompt") or "")),
            AgentMessage(MessageRole.ASSISTANT, response),
        ))
    return tuple(messages)


__all__ = ["ScreenplayReplacementOrdinaryService"]
