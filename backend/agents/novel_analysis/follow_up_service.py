"""Replacement-owned conversational follow-up entry for Novel Analysis."""

from __future__ import annotations

import json
from hashlib import sha256

from agents.novel_analysis.attempt_artifact import (
    NOVEL_ANALYSIS_ATTEMPT_ARTIFACT_NAMESPACE,
)
from agents.novel_analysis.publication_service import (
    NovelAnalysisReplacementPublicationService,
)
from agents.novel_analysis.request_compiler import compile_novel_analysis_request_scope
from agents.novel_analysis.review_artifact import (
    NOVEL_ANALYSIS_REVIEWED_ARTIFACT_NAMESPACE,
)
from agents.novel_analysis.review_projection import NovelAnalysisReviewProjection
from agents.shared.composition_routing import (
    IMPLEMENTATION_OWNER_RUN_METADATA_KEY,
)
from agents.shared.saved_model_binding import capture_saved_model_binding
from application.agent_conversation_input import (
    conversation_input_metadata,
    conversation_messages,
)
from application.agent_run_service import AgentRunService
from application.model_runtime import (
    model_request_from_runtime,
    reasoning_mode_from_options,
    run_execution_intent,
    runtime_context_window_tokens,
)
from application.run_provenance import digest_model_endpoint
from infrastructure.persistence.sqlite_artifact_repository import (
    SqliteArtifactRepository,
)
from purra.api import AgentCoreRunOptions
from purra.contracts import (
    AgentMessage,
    AgentRunRequest,
    DomainContext,
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


class NovelAnalysisReplacementFollowUpService:
    def __init__(self, db, composition, *, runs=None) -> None:
        self._db = db
        self._composition = composition
        self._runs = runs or AgentRunService(composition)
        self._artifacts = SqliteArtifactRepository(db)

    async def build_request(
        self,
        *,
        source_revision_id: str,
        artifact_id: str | None,
        command_id: str,
        prompt: str,
        runtime,
        history_before_run_id: str | None = None,
    ) -> tuple[AgentRunRequest, str | None]:
        question = str(prompt or "").strip()
        if not question or len(question) > 20_000:
            raise ValueError("novel analysis follow-up prompt is invalid")
        artifact_id = _optional_artifact_id(artifact_id)
        review = None
        if artifact_id:
            artifact = await self._artifacts.load(artifact_id)
            if artifact is None:
                raise ValueError("replacement analysis Artifact is unavailable")
            if artifact.namespace == NOVEL_ANALYSIS_ATTEMPT_ARTIFACT_NAMESPACE:
                review = await NovelAnalysisReviewProjection(self._db).load(artifact_id)
            elif artifact.namespace == NOVEL_ANALYSIS_REVIEWED_ARTIFACT_NAMESPACE:
                review = await NovelAnalysisReplacementPublicationService(
                    self._db
                ).load_reviewed(artifact_id)
            else:
                raise ValueError("analysis Artifact belongs to frozen legacy")
            if review.get("sourceRevisionId") != source_revision_id:
                raise ValueError("analysis Artifact belongs to another source revision")
        context_window = runtime_context_window_tokens(runtime)
        scope = await compile_novel_analysis_request_scope(
            self._db,
            source_revision_id=source_revision_id,
            command_id=command_id,
            segment_token_budget=min(16_000, max(256, (context_window - 8_192) // 4)),
        )
        runtime_binding = await capture_saved_model_binding(self._db, runtime)
        if runtime_binding is None:
            raise ValueError("Novel Analysis requires an exact saved model binding")
        model = model_request_from_runtime(runtime, task_reasoning_preference="economical")
        history = await _replacement_history(
            self._db,
            source_revision_id,
            command_id,
            before_run_id=history_before_run_id,
        )
        request = AgentRunRequest(
            messages=conversation_messages(
                (*history, AgentMessage(MessageRole.USER, question)),
                context_window=context_window,
            ),
            model=model,
            domain_context=DomainContext(
                namespace="purrtypos.novel_analysis",
                payload=scope.to_mapping(),
            ),
            mode="novel_analysis_follow_up",
            tools_enabled=True,
            planning_mode=PlanningMode.REACTIVE,
            context_window=context_window,
            metadata={
                "interactionKind": "follow_up",
                "agentTreeEnabled": False,
                "runtimeBinding": runtime_binding,
                **(
                    {
                        "analysisArtifactId": artifact_id,
                        IMPLEMENTATION_OWNER_RUN_METADATA_KEY: str(
                            review["createdByRunId"]
                        ),
                    }
                    if review is not None
                    else {}
                ),
                **conversation_input_metadata(
                    source="persisted_public_turns",
                    scope=f"analysis:{source_revision_id}",
                ),
            },
        )
        return request, (
            str(review["createdByRunId"])
            if review is not None
            else None
        )

    async def run(
        self,
        *,
        source_revision_id: str,
        artifact_id: str | None,
        command_id: str,
        prompt: str,
        runtime,
        signal,
        run_binding_lifecycle=None,
        history_before_run_id: str | None = None,
    ):
        request, _ = await self.build_request(
            source_revision_id=source_revision_id,
            artifact_id=artifact_id,
            command_id=command_id,
            prompt=prompt,
            runtime=runtime,
            history_before_run_id=history_before_run_id,
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
                turn_id=f"novel-analysis-follow-up:{command_id}",
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
                        output_contract="novel_analysis_follow_up_text_v1",
                        tool_protocol_contract="novel_analysis_follow_up_tools_v1",
                    ),
                ),
                binding=RunBinding(
                    namespace="purrtypos.novel_analysis",
                    aggregate_id=source_revision_id,
                    command_id=command_id,
                    attributes={
                        "interactionKind": "follow_up",
                        **(
                            {
                                "analysisArtifactId": request.metadata[
                                    "analysisArtifactId"
                                ],
                            }
                            if request.metadata.get("analysisArtifactId")
                            else {}
                        ),
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


async def _replacement_history(
    db,
    revision_id: str,
    command_id: str,
    *,
    before_run_id: str | None = None,
):
    before_position = None
    if before_run_id:
        row = await db.fetch_one(
            "SELECT rowid AS position FROM ai_agent_runs WHERE id = ? "
            "AND binding_namespace = 'purrtypos.novel_analysis' "
            "AND binding_aggregate_id = ?",
            [before_run_id, revision_id],
        )
        if row is None:
            raise ValueError("analysis edit history target is unavailable")
        before_position = int(row["position"])
    rows = await db.fetch_all(
        "SELECT prompt, final_response FROM ai_agent_runs "
        "WHERE binding_namespace = 'purrtypos.novel_analysis' "
        "AND binding_aggregate_id = ? AND binding_command_id != ? "
        "AND COALESCE((SELECT session_id FROM novel_analysis_session_commands "
        "WHERE command_id = ai_agent_runs.binding_command_id), '') = "
        "COALESCE((SELECT session_id FROM novel_analysis_session_commands "
        "WHERE command_id = ?), '') "
        "AND status NOT IN ('failed', 'canceled') "
        "AND NOT EXISTS (SELECT 1 FROM novel_analysis_superseded_runs "
        "WHERE run_id = ai_agent_runs.id) "
        "AND (? IS NULL OR rowid < ?) "
        "ORDER BY rowid DESC LIMIT 32",
        [revision_id, command_id, command_id, before_position, before_position],
    )
    messages = []
    for row in reversed(rows):
        response = str(row.get("final_response") or "").strip()
        if response:
            messages.extend((
                AgentMessage(MessageRole.USER, str(row.get("prompt") or "")),
                AgentMessage(MessageRole.ASSISTANT, response),
            ))
    return tuple(messages)


def _optional_artifact_id(value: str | None) -> str | None:
    normalized = str(value or "").strip()
    if not normalized:
        return None
    for prefix in ("novel-analysis-v1://", "novel-analysis-artifact://"):
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix):]
            break
    if "://" in normalized:
        raise ValueError("analysis Artifact reference is invalid")
    return normalized


__all__ = ["NovelAnalysisReplacementFollowUpService"]
