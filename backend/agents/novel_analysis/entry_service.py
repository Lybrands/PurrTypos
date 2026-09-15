"""Replacement-only application entry for Novel Analysis execution."""

from __future__ import annotations

import json
from hashlib import sha256

from agents.novel_analysis.domain import NOVEL_ANALYSIS_REPLACEMENT_DOMAIN_NAMESPACE
from agents.novel_analysis.planner_contract import SCALABLE_ANALYSIS_RECIPE_VERSION
from agents.novel_analysis.scalable_executor import ScalableNovelAnalysisUnitExecutor
from agents.novel_analysis.stage_output import NovelAnalysisStageOutput
from agents.novel_analysis.scalable_profile import (
    NOVEL_ANALYSIS_SCALABLE_PROFILE_ID,
    scalable_novel_analysis_implementation,
)
from agents.shared.implementation import AgentKind
from agents.shared.saved_model_binding import capture_saved_model_binding
from agents.shared.composition_routing import (
    IMPLEMENTATION_OWNER_RUN_METADATA_KEY,
)
from application.agent_run_service import AgentRunService
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


class NovelAnalysisReplacementUnavailable(RuntimeError):
    code = "novel_analysis_replacement_unavailable"


class NovelAnalysisRuntimeBindingUnavailable(RuntimeError):
    code = "novel_analysis_runtime_binding_unavailable"


class NovelAnalysisReplacementExecutionService:
    """Compile and execute the scalable novel-analysis contract."""

    def __init__(
        self,
        db,
        composition,
        *,
        runs=None,
        enforce_create_policy: bool = True,
    ) -> None:
        self._db = db
        self._composition = composition
        self._runs = runs or AgentRunService(composition)
        if not enforce_create_policy:
            # The caller already routed an existing Run by its persisted
            # implementation. Rollout policy controls only new roots and must
            # not strand replacement-owned continuations after a rollback.
            return
        route = composition.agent_implementation_router.for_create(
            AgentKind.NOVEL_ANALYSIS,
            recipe_version=SCALABLE_ANALYSIS_RECIPE_VERSION,
        )
        if (
            route.runtime_profile_id != NOVEL_ANALYSIS_SCALABLE_PROFILE_ID
            or route.identity
            != scalable_novel_analysis_implementation(
                recipe_version=SCALABLE_ANALYSIS_RECIPE_VERSION
            )
        ):
            raise NovelAnalysisReplacementUnavailable(
                "Novel Analysis replacement rollout is not enabled"
            )

    async def build_request(
        self,
        *,
        source_revision_id: str,
        command_id: str,
        prompt: str,
        runtime,
        implementation_owner_run_id: str | None = None,
        recovery_source: str | None = None,
    ) -> AgentRunRequest:
        question = str(prompt or "").strip()
        if not question or len(question) > 20_000:
            raise ValueError("novel analysis prompt is invalid")
        model = model_request_from_runtime(
            runtime,
            task_reasoning_preference="economical",
        )
        context_window = runtime_context_window_tokens(runtime)
        revision = await self._db.fetch_one(
            "SELECT id FROM novel_source_revisions WHERE id = ?",
            [source_revision_id],
        )
        if revision is None:
            raise ValueError("novel analysis source revision does not exist")
        runtime_binding = await capture_saved_model_binding(self._db, runtime)
        if runtime_binding is None:
            raise NovelAnalysisRuntimeBindingUnavailable(
                "Novel Analysis requires an exact saved model binding"
            )
        return AgentRunRequest(
            messages=(AgentMessage(MessageRole.USER, question),),
            model=model,
            domain_context=DomainContext(
                namespace=NOVEL_ANALYSIS_REPLACEMENT_DOMAIN_NAMESPACE,
                payload={
                    "sourceRevisionId": source_revision_id,
                    "commandId": command_id,
                },
            ),
            mode="novel_analysis",
            tools_enabled=False,
            planning_mode=PlanningMode.PLANNED,
            context_window=context_window,
            metadata={
                "locale": str(getattr(runtime, "locale", "zh-CN") or "zh-CN"),
                "progressAudience": "public",
                **(
                    {"recoverySource": recovery_source}
                    if recovery_source else {}
                ),
                **(
                    {"runtimeBinding": runtime_binding}
                    if runtime_binding is not None
                    else {}
                ),
                **(
                    {IMPLEMENTATION_OWNER_RUN_METADATA_KEY: implementation_owner_run_id}
                    if implementation_owner_run_id else {}
                ),
            },
        )

    async def run(
        self,
        *,
        source_revision_id: str,
        command_id: str,
        prompt: str,
        runtime,
        signal,
        run_binding_lifecycle=None,
        implementation_owner_run_id: str | None = None,
    ):
        async for update in self._run(
            source_revision_id=source_revision_id,
            task_command_id=command_id,
            run_command_id=command_id,
            prompt=prompt,
            runtime=runtime,
            signal=signal,
            run_binding_lifecycle=run_binding_lifecycle,
            implementation_owner_run_id=implementation_owner_run_id,
        ):
            yield update

    async def continue_task(
        self,
        *,
        source_revision_id: str,
        task_command_id: str,
        run_command_id: str,
        prompt: str,
        runtime,
        signal,
        durable_continuation,
        run_binding_lifecycle,
        recovery_source: str = "user",
    ):
        async for update in self._run(
            source_revision_id=source_revision_id,
            task_command_id=task_command_id,
            run_command_id=run_command_id,
            prompt=prompt,
            runtime=runtime,
            signal=signal,
            durable_continuation=durable_continuation,
            run_binding_lifecycle=run_binding_lifecycle,
            implementation_owner_run_id=durable_continuation.source.run_id,
            recovery_source=recovery_source,
        ):
            yield update

    async def _run(
        self,
        *,
        source_revision_id: str,
        task_command_id: str,
        run_command_id: str,
        prompt: str,
        runtime,
        signal,
        durable_continuation=None,
        run_binding_lifecycle=None,
        implementation_owner_run_id=None,
        recovery_source: str | None = None,
    ):
        request = await self.build_request(
            source_revision_id=source_revision_id,
            command_id=task_command_id,
            prompt=prompt,
            runtime=runtime,
            implementation_owner_run_id=implementation_owner_run_id,
            recovery_source=recovery_source,
        )
        model = request.model
        context_window = request.context_window
        profile_digest = _request_profile_digest(request)
        executor = ScalableNovelAnalysisUnitExecutor(
            self._db,
            model_name=model.model,
            stage_output=NovelAnalysisStageOutput(
                self._db,
                output_repository=self._composition.output_repository,
                publisher=self._composition.output_notifications,
            ),
        )
        async for update in self._runs.run(
            request=request,
            api_key=runtime.apiKey.get_secret_value(),
            options=AgentCoreRunOptions(
                turn_id=f"novel-analysis:{run_command_id}",
                default_context_window_tokens=context_window,
                force_planned_tool_choice=False,
                require_tool_call=False,
                reasoning_mode=reasoning_mode_from_options(model.options),
                provenance=RunProvenance(
                    model_provider=model.provider,
                    model_name=model.model,
                    context_window=context_window,
                    endpoint_digest=digest_model_endpoint(runtime.baseURL),
                    request_profile_digest=profile_digest,
                    capability_snapshot=model.capability_snapshot.to_mapping(
                        include_digest=True
                    ),
                    execution_intent=run_execution_intent(
                        model,
                        reasoning_mode_from_options(model.options),
                        output_contract="novel_analysis_scalable_review_v1",
                        tool_protocol_contract="novel_analysis_scalable_tools_v2",
                    ),
                ),
                binding=RunBinding(
                    namespace="purrtypos.novel_analysis",
                    aggregate_id=source_revision_id,
                    command_id=run_command_id,
                    attributes={"taskIdempotencyKey": task_command_id},
                ),
                response_transaction_policy=ResponseTransactionPolicy(
                    mode=ResponseTransactionMode.VALIDATED_RESULT,
                    public_presentation=PublicPresentationMode.NONE,
                ),
                durable_continuation=durable_continuation,
            ),
            signal=signal,
            run_binding_lifecycle=run_binding_lifecycle,
            long_task_executor=executor,
        ):
            yield update


def _request_profile_digest(request: AgentRunRequest) -> str:
    payload = {
        "schemaVersion": 1,
        "mode": request.mode,
        "model": request.model.model,
        "provider": request.model.provider,
        "contextWindow": request.context_window,
        "domain": {
            "namespace": request.domain_context.namespace,
            "payload": thaw_json_mapping(request.domain_context.payload),
        },
        "messages": [message.to_mapping() for message in request.messages],
    }
    return sha256(json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


__all__ = [
    "NovelAnalysisReplacementExecutionService",
    "NovelAnalysisRuntimeBindingUnavailable",
    "NovelAnalysisReplacementUnavailable",
]
