"""Replacement-only application entry for formal Screenplay execution."""

from __future__ import annotations

import json
from hashlib import sha256

from agents.screenplay.contracts import (
    SCREENPLAY_REPLACEMENT_DOMAIN_NAMESPACE,
    ScreenplayStageCommand,
)
from agents.screenplay.conversation_projection import (
    SCREENPLAY_REPLACEMENT_ROOT_BINDING,
)
from agents.screenplay.executor import ScreenplayReplacementUnitExecutor
from agents.screenplay.model_runner import PurrAScreenplayModelPartRunner
from agents.screenplay.profile import SCREENPLAY_REPLACEMENT_PROFILE_ID
from agents.screenplay.request_compiler import compile_screenplay_host_recipe
from agents.shared.implementation import AgentKind, replacement_implementation
from agents.shared.composition_routing import (
    IMPLEMENTATION_OWNER_RUN_METADATA_KEY,
)
from agents.shared.saved_model_binding import capture_saved_model_binding
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
from purra.json_values import thaw_json_mapping
from purra.output import (
    PublicPresentationMode,
    ResponseTransactionMode,
    ResponseTransactionPolicy,
)


class ScreenplayReplacementUnavailable(RuntimeError):
    code = "screenplay_replacement_unavailable"


class ScreenplayRuntimeBindingUnavailable(RuntimeError):
    code = "screenplay_runtime_binding_unavailable"


class ScreenplayReplacementExecutionService:
    """Compile and execute only the new Screenplay contract."""

    def __init__(self, db, composition, *, runs=None) -> None:
        self._db = db
        self._composition = composition
        self._runs = runs or AgentRunService(composition)
        route = composition.agent_implementation_router.for_create(
            AgentKind.SCREENPLAY,
            recipe_version=1,
        )
        if (
            route.runtime_profile_id != SCREENPLAY_REPLACEMENT_PROFILE_ID
            or route.identity != replacement_implementation(
                AgentKind.SCREENPLAY,
                recipe_version=1,
            )
        ):
            raise ScreenplayReplacementUnavailable(
                "Screenplay replacement rollout is not enabled"
            )

    async def build_request(
        self,
        *,
        project_id: str,
        session_id: int,
        turn_id: str,
        command_id: str,
        prompt: str,
        stage_command: ScreenplayStageCommand,
        runtime,
        host_recipe=None,
        expected_runtime_binding=None,
        implementation_owner_run_id: str | None = None,
        failed_resume_attempts: int = 0,
    ) -> AgentRunRequest:
        question = str(prompt or "").strip()
        turn = str(turn_id or "").strip()
        command = str(command_id or "").strip()
        if not question or len(question) > 20_000 or not turn or not command:
            raise ValueError("Screenplay replacement request identity is invalid")
        if type(session_id) is not int or session_id < 1:
            raise ValueError("Screenplay replacement session is invalid")
        model = model_request_from_runtime(
            runtime,
            task_reasoning_preference="economical",
        )
        runtime_binding = await capture_saved_model_binding(self._db, runtime)
        if runtime_binding is None:
            raise ScreenplayRuntimeBindingUnavailable(
                "Screenplay requires an exact saved model binding"
            )
        if (
            expected_runtime_binding is not None
            and dict(expected_runtime_binding) != runtime_binding
        ):
            raise ScreenplayRuntimeBindingUnavailable(
                "Screenplay saved model binding changed after Turn admission"
            )
        recipe = host_recipe or await compile_screenplay_host_recipe(
            self._db, project_id=project_id, stage_command=stage_command,
        )
        if not hasattr(recipe, "to_mapping"):
            raise TypeError("Screenplay replacement Host recipe is invalid")
        return AgentRunRequest(
            messages=(AgentMessage(MessageRole.USER, question),),
            model=model,
            session_id=session_id,
            domain_context=DomainContext(
                namespace=SCREENPLAY_REPLACEMENT_DOMAIN_NAMESPACE,
                payload={
                    "schemaVersion": 1,
                    "projectId": str(project_id or "").strip(),
                    "turnId": turn,
                    "commandId": command,
                },
            ),
            mode="screenplay",
            tools_enabled=False,
            planning_mode=PlanningMode.PLANNED,
            context_window=runtime_context_window_tokens(runtime),
            metadata={
                "locale": str(getattr(runtime, "locale", "zh-CN") or "zh-CN"),
                "progressAudience": "public",
                "screenplayRecipe": recipe.to_mapping(),
                "runtimeBinding": runtime_binding,
                "failedResumeAttempts": int(failed_resume_attempts),
                **(
                    {IMPLEMENTATION_OWNER_RUN_METADATA_KEY: implementation_owner_run_id}
                    if implementation_owner_run_id
                    else {}
                ),
            },
        )

    async def run(self, *, signal, run_binding_lifecycle=None, **kwargs):
        async for update in self._run(
            signal=signal,
            run_binding_lifecycle=run_binding_lifecycle,
            **kwargs,
        ):
            yield update

    async def continue_task(
        self,
        *,
        durable_continuation,
        task_command_id: str,
        run_command_id: str,
        signal,
        run_binding_lifecycle,
        **kwargs,
    ):
        async for update in self._run(
            command_id=task_command_id,
            binding_command_id=run_command_id,
            durable_continuation=durable_continuation,
            implementation_owner_run_id=durable_continuation.source.run_id,
            signal=signal,
            run_binding_lifecycle=run_binding_lifecycle,
            **kwargs,
        ):
            yield update

    async def _run(
        self,
        *,
        signal,
        run_binding_lifecycle=None,
        durable_continuation=None,
        binding_command_id: str | None = None,
        **kwargs,
    ):
        request = await self.build_request(**kwargs)
        runtime = kwargs["runtime"]
        model = request.model
        root_command_id = str(binding_command_id or kwargs["command_id"])
        executor = ScreenplayReplacementUnitExecutor(
            self._db,
            model_runner=PurrAScreenplayModelPartRunner(
                self._db,
                self._composition,
                runtime,
            ),
            long_task_repository=self._composition.long_task_repository,
        )
        async for update in self._runs.run(
            request=request,
            api_key=runtime.apiKey.get_secret_value(),
            options=AgentCoreRunOptions(
                turn_id=kwargs["turn_id"],
                default_context_window_tokens=request.context_window,
                force_planned_tool_choice=False,
                require_tool_call=False,
                reasoning_mode=reasoning_mode_from_options(model.options),
                provenance=RunProvenance(
                    model_provider=model.provider,
                    model_name=model.model,
                    context_window=request.context_window,
                    endpoint_digest=digest_model_endpoint(runtime.baseURL),
                    request_profile_digest=_request_profile_digest(request),
                    capability_snapshot=model.capability_snapshot.to_mapping(
                        include_digest=True
                    ),
                    execution_intent=run_execution_intent(
                        model,
                        reasoning_mode_from_options(model.options),
                        output_contract="screenplay_revision_v1",
                        tool_protocol_contract="screenplay_replacement_tools_v1",
                    ),
                ),
                binding=RunBinding(
                    namespace=SCREENPLAY_REPLACEMENT_ROOT_BINDING,
                    aggregate_id=kwargs["project_id"],
                    command_id=root_command_id,
                    attributes={
                        "turnId": kwargs["turn_id"],
                        **(
                            {
                                "continuationOf": durable_continuation.source.run_id,
                            }
                            if durable_continuation is not None
                            else {}
                        ),
                        **(
                            run_binding_lifecycle.run_binding_attributes()
                            if run_binding_lifecycle is not None
                            and callable(getattr(
                                run_binding_lifecycle,
                                "run_binding_attributes",
                                None,
                            ))
                            else {}
                        ),
                    },
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
        "recipe": thaw_json_mapping(request.metadata).get("screenplayRecipe"),
        "messages": [message.to_mapping() for message in request.messages],
    }
    return sha256(json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")).hexdigest()


__all__ = [
    "ScreenplayReplacementExecutionService",
    "ScreenplayReplacementUnavailable",
    "ScreenplayRuntimeBindingUnavailable",
]
