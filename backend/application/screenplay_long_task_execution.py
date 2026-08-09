"""Execute checkpointed screenplay batches as independent Agent Runs."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from agent_core.contracts import AgentRunResult, RunStatus
from agent_core.events import AgentEvent, CoreEventType
from agent_core.json_values import thaw_json_mapping, thaw_json_value
from agent_core.long_tasks import (
    LongTaskCoordinator,
    LongTaskStatus,
    LongTaskUnitResult,
)
from agent_core.task_admission import (
    LongTaskExecutionResult,
    LongTaskExecutionStatus,
    LongTaskExecutionUpdate,
)
from agent_core.structured_output import parse_json_object
from agent_core.work_items import (
    WorkItemLifecycle,
    WorkItemTransitionCommand,
)
from application.agent_delegation_service import AgentDelegationService
from application.screenplay_agent_request_mapping import (
    screenplay_run_options,
    to_screenplay_agent_request,
)
from database.crud.screenplay_drafts import (
    latest_accepted_draft_document,
    list_episode_rows,
)
from database.crud.screenplay_head_projection import get_current_document
from database.crud.screenplay_episode_documents import (
    list_episode_rows as list_structured_episode_rows,
)
from domains.screenplay.payload_limits import SCENE_DRAFT_PAYLOAD_LIMITS
from domains.screenplay.agent_roles import build_screenplay_agent_role_registry
from domains.screenplay.long_task_response import (
    ScreenplayDraftBatchResponseValidator,
    ScreenplayReviewReportValidator,
)
from domains.screenplay.scene_execution import (
    normalize_scene_execution,
    validate_scene_execution_history,
)
from domains.screenplay.draft_episodes import build_episode_draft_batches
from domains.screenplay.scene_order import ordered_scene_mappings
from domains.screenplay.task_admission import (
    SCREENPLAY_DRAFT_LONG_TASK_KIND,
)
from schemas.screenplay_agent_run import ScreenplayAgentRunRequest


SCREENPLAY_LONG_TASK_RESPONSE_EVENT = "screenplay.long_task.response"
SCREENPLAY_LONG_TASK_THINKING_SNAPSHOT_EVENT = (
    "screenplay.long_task.thinking_snapshot"
)
SCREENPLAY_LONG_TASK_VALIDATION_FAILED_EVENT = (
    "screenplay.long_task.validation_failed"
)


class ScreenplayLongTaskExecution:
    def __init__(
        self,
        *,
        composition,
        repository,
        work_items: WorkItemLifecycle,
        body,
        api_key: str,
        provider_options: dict,
        signal,
        observer=None,
        parent_run_id: str | None = None,
        delegation_service: AgentDelegationService | None = None,
    ) -> None:
        self._composition = composition
        self._repository = repository
        self._work_items = work_items
        self._body = body
        self._api_key = api_key
        self._provider_options = provider_options
        self._signal = signal
        self._observer = observer
        self._parent_run_id = str(parent_run_id or "").strip() or None
        delegation_repository = getattr(
            composition,
            "delegation_repository",
            None,
        )
        self._delegation_service = delegation_service or (
            AgentDelegationService(delegation_repository)
            if delegation_repository is not None
            else None
        )

    async def run(self, task_id: str):
        coordinator = LongTaskCoordinator(
            self._repository,
            worker_id=self._composition.execution_owner_id,
            retry_backoff_ms=(5_000, 20_000),
        )
        task = await coordinator.run(task_id, self, self._signal)
        if (
            task.status in {LongTaskStatus.FAILED, LongTaskStatus.CANCELED}
            and self._delegation_service is not None
        ):
            await self._delegation_service.cancel_children(
                self._parent_run_id or task.created_by_run_id
            )
        units = await self._repository.list_units(task.id)
        finalize = next((
            unit for unit in units
            if str(unit.metadata.get("unitKind") or "") == "finalize"
        ), None)
        final_metadata = (
            thaw_json_mapping(finalize.metadata)
            if finalize is not None
            else {}
        )
        if task.status is LongTaskStatus.COMPLETED:
            proposal = final_metadata.get("proposal")
            if isinstance(proposal, Mapping):
                await self._emit(AgentEvent(
                    type="screenplay.document_proposal",
                    run_id=self._parent_run_id or task.created_by_run_id,
                    payload=proposal,
                ))
            return LongTaskExecutionResult(
                task_id=task.id,
                status=LongTaskExecutionStatus.COMPLETED,
                final_response=str(final_metadata.get("finalResponse") or ""),
                metadata={"completedUnits": task.completed_units},
            )
        if task.status is LongTaskStatus.PAUSED:
            status = LongTaskExecutionStatus.PAUSED
        elif task.status is LongTaskStatus.CANCELED:
            status = LongTaskExecutionStatus.CANCELED
        else:
            status = LongTaskExecutionStatus.FAILED
        error = next((
            str(unit.error_code)
            for unit in units
            if unit.error_code
        ), None)
        return LongTaskExecutionResult(
            task_id=task.id,
            status=status,
            error=error,
            metadata={"completedUnits": task.completed_units},
        )

    async def run_unit(self, task, unit, signal=None):
        if task.kind != SCREENPLAY_DRAFT_LONG_TASK_KIND:
            raise RuntimeError("unsupported_screenplay_long_task_kind")
        await self._emit_progress(task.id, task=task)
        unit_kind = str(unit.metadata.get("unitKind") or "scene_generation")
        if unit_kind == "finalize":
            return await self._finalize(task)
        if unit_kind == "scene_revision":
            passthrough = await self._passthrough_revision_if_clean(task, unit)
            if passthrough is not None:
                return passthrough
        agent_role = _agent_role_for_unit(
            unit_kind,
            declared_role=str(unit.metadata.get("agentRole") or ""),
        )
        prepared = await self._prepare_batch(task, unit)
        if self._delegation_service is None:
            raise RuntimeError("screenplay child delegation service is unavailable")
        parent_run_id = self._parent_run_id or task.created_by_run_id
        created = await self._delegation_service.delegate(
            parent_run_id=parent_run_id,
            agent_role=agent_role,
            objective=_unit_stream_title(unit),
            input_payload={
                "taskId": task.id,
                "unitId": unit.id,
                "attempt": max(1, int(unit.attempt or 1)),
                "sceneIds": list(unit.metadata.get("sceneIds", [])),
            },
            required=True,
        )
        claimed = await self._delegation_service.claim_delegation(
            delegation_id=str(created["delegationId"]),
            parent_run_id=parent_run_id,
            worker_id=self._composition.execution_owner_id,
            max_parallel_children=task.max_parallelism,
        )
        if claimed is None:
            raise RuntimeError("screenplay_child_delegation_claim_failed")
        delegation_view, child_lineage = claimed
        if delegation_view["delegationId"] != created["delegationId"]:
            raise RuntimeError("screenplay_child_delegation_scope_mismatch")
        delegation_view = {
            **delegation_view,
            "agentTitle": _agent_title_for_unit(agent_role, unit),
            "unitId": unit.id,
            "attempt": max(1, int(unit.attempt or 1)),
        }
        delegation_id = str(delegation_view["delegationId"])
        await self._emit(AgentEvent(
            type=CoreEventType.DELEGATION_CREATED,
            run_id=parent_run_id,
            payload=delegation_view,
        ), persist=False)
        await self._emit(AgentEvent(
            type=CoreEventType.DELEGATION_CLAIMED,
            run_id=parent_run_id,
            payload={**delegation_view, "status": "claimed"},
        ), persist=False)
        child_body = self._body.model_copy(update={
            "messages": [{"role": "user", "content": prepared.prompt}],
            "sessionId": None,
            "enableAgentTools": False,
            "chatAgentMode": "ask",
            "screenplayTaskIntent": "chat",
            # The durable parent owns the project operation. Child Runs are
            # evidence-producing units and must not bind or settle that root
            # operation independently.
            "screenplayOperationId": None,
            "screenplayDraftSceneCount": 1,
            "screenplayDraftScope": "planner",
        })
        from application.agent_run_service import AgentRunService

        child_request = None
        child_options = None
        if isinstance(child_body, ScreenplayAgentRunRequest):
            child_request = to_screenplay_agent_request(
                child_body,
                self._provider_options,
            )
            child_options = screenplay_run_options(
                child_request,
                lineage=child_lineage,
                agent_role=agent_role,
                output_work_units=max(
                    1,
                    len(tuple(unit.metadata.get("sceneIds", ()))),
                ),
            )

        result: AgentRunResult | None = None
        bound = False
        child_run_id: str | None = None
        thinking_parts: list[str] = []
        cancellation_signal = signal or self._signal
        stream = AgentRunService(self._composition).run(
            body=child_body,
            api_key=self._api_key,
            provider_options=self._provider_options,
            signal=cancellation_signal,
            lineage=child_lineage,
            enable_delegation=False,
            # The host prompt is complete. An explicit empty mode set keeps
            # the child tool catalog empty even if a future caller regresses
            # the request-level tools flag.
            allowed_tool_modes=frozenset(),
            domain_context_overrides={
                "bound_draft_scene_ids": list(
                    unit.metadata.get("sceneIds", [])
                ),
            },
            host_system_instruction=(
                build_screenplay_agent_role_registry()
                .require(agent_role)
                .instruction
            ),
            response_validators=(
                (
                    ScreenplayReviewReportValidator(tuple(
                        str(scene.get("id") or "")
                        for scene in prepared.scenes
                    ))
                    if unit_kind == "continuity_review"
                    else ScreenplayDraftBatchResponseValidator(prepared.scenes)
                ),
            ),
            agent_role=agent_role,
            output_work_units=max(
                1,
                len(tuple(unit.metadata.get("sceneIds", ()))),
            ),
            host_context_only=True,
            mapped_request=child_request,
            base_options=child_options,
        )
        try:
            async for update in stream:
                if isinstance(update, AgentEvent):
                    await self._emit_child_event(
                        task,
                        unit,
                        delegation_view,
                        update,
                    )
                if (
                    isinstance(update, AgentEvent)
                    and update.type == "run.started"
                    and update.run_id
                    and not bound
                ):
                    child_run_id = update.run_id
                    await self._repository.bind_unit_run(
                        task.id,
                        unit.id,
                        worker_id=self._composition.execution_owner_id,
                        run_id=update.run_id,
                    )
                    bound = True
                    delegation_view["childRunId"] = update.run_id
                    await self._emit(AgentEvent(
                        type=CoreEventType.DELEGATION_CLAIMED,
                        run_id=parent_run_id,
                        payload={
                            **delegation_view,
                            "childRunId": update.run_id,
                            "status": "running",
                        },
                    ), persist=False)
                if (
                    isinstance(update, AgentEvent)
                    and update.type == "model.thinking_delta"
                ):
                    delta = str(update.payload.get("delta") or "")
                    if delta:
                        thinking_parts.append(delta)
                if isinstance(update, AgentRunResult):
                    result = update
        finally:
            await stream.aclose()
            await self._record_thinking_snapshot(
                task,
                unit,
                delegation_view=delegation_view,
                run_id=(result.run_id if result is not None else child_run_id),
                content="".join(thinking_parts),
            )
        if cancellation_signal.is_set():
            await self._delegation_service.fail_claim(
                delegation_id=delegation_id,
                worker_id=self._composition.execution_owner_id,
                error="long_task_execution_canceled",
            )
            await self._emit(AgentEvent(
                type=CoreEventType.DELEGATION_CANCELED,
                run_id=parent_run_id,
                payload={**delegation_view, "status": "canceled"},
            ), persist=False)
            raise RuntimeError("long_task_execution_canceled")
        if result is None or result.status is not RunStatus.DONE:
            error_message = (
                result.error if result is not None
                else "long_task_child_run_failed"
            )
            await self._delegation_service.fail_claim(
                delegation_id=delegation_id,
                worker_id=self._composition.execution_owner_id,
                error=error_message,
            )
            await self._emit(AgentEvent(
                type=CoreEventType.DELEGATION_FAILED,
                run_id=parent_run_id,
                payload={
                    **delegation_view,
                    "status": "failed",
                    "error": (
                        error_message
                    ),
                },
            ), persist=False)
            raise RuntimeError(
                (result.error if result is not None else None)
                or "long_task_child_run_failed"
            )
        try:
            if unit_kind == "continuity_review":
                review_report, assistant_response = (
                    await self._validate_review_result(
                        unit,
                        result.final_response,
                    )
                )
                result_metadata = {
                    "sceneIds": list(unit.metadata.get("sceneIds", [])),
                    "reviewReport": review_report,
                    "assistantResponse": assistant_response,
                }
            else:
                scenes, assistant_response = await self._validate_batch_result(
                    task,
                    unit,
                    result.final_response,
                )
                result_metadata = {
                    "sceneIds": [scene["sceneId"] for scene in scenes],
                    "scenes": scenes,
                    "continuitySummary": _continuity_summary_from_scenes(scenes),
                    "assistantResponse": assistant_response,
                }
        except Exception as error:
            if result.run_id:
                await self._composition.append_run_event(
                    result.run_id,
                    SCREENPLAY_LONG_TASK_VALIDATION_FAILED_EVENT,
                    {
                        "taskId": task.id,
                        "unitId": unit.id,
                        "attempt": max(1, int(unit.attempt or 1)),
                        "errorCode": str(error),
                    },
                )
            await self._delegation_service.fail_claim(
                delegation_id=delegation_id,
                worker_id=self._composition.execution_owner_id,
                error=str(error),
            )
            await self._emit(AgentEvent(
                type=CoreEventType.DELEGATION_FAILED,
                run_id=parent_run_id,
                payload={
                    **delegation_view,
                    "status": "failed",
                    "error": str(error),
                },
            ), persist=False)
            raise
        await self._composition.append_run_event(
            result.run_id,
            SCREENPLAY_LONG_TASK_RESPONSE_EVENT,
            {
                "taskId": task.id,
                "unitId": unit.id,
                "attempt": max(1, int(unit.attempt or 1)),
                "content": assistant_response,
            },
        )
        await self._emit_child_event(
            task,
            unit,
            delegation_view,
            AgentEvent(
                type=SCREENPLAY_LONG_TASK_RESPONSE_EVENT,
                run_id=result.run_id,
                payload={
                    "taskId": task.id,
                    "unitId": unit.id,
                    "attempt": max(1, int(unit.attempt or 1)),
                    "content": assistant_response,
                },
            ),
            persist=False,
        )
        recorded = await self._delegation_service.record_result(
            delegation_id=delegation_id,
            child_run_id=result.run_id,
            result=result,
        )
        if not recorded:
            raise RuntimeError("screenplay_child_delegation_result_mismatch")
        await self._emit(AgentEvent(
            type=CoreEventType.DELEGATION_COMPLETED,
            run_id=parent_run_id,
            payload={
                **delegation_view,
                "childRunId": result.run_id,
                "status": "done",
                "resultSummary": assistant_response,
            },
        ), persist=False)
        return LongTaskUnitResult(
            output_ref=f"longtask://{task.id}/{unit.id}",
            run_id=result.run_id,
            metadata=result_metadata,
        )

    async def on_unit_settled(self, task_id: str) -> None:
        await self._emit_progress(task_id)

    @staticmethod
    def is_retryable_unit_error(error: Exception) -> bool:
        """Retry only failures that can plausibly succeed unchanged."""

        code = str(error or "").strip().lower()
        return any(marker in code for marker in (
            "model_stream_interrupted",
            "upstream_stream_interrupted",
            "provider_timeout",
            "provider_rate_limited",
            "provider_unavailable",
            "temporarily_unavailable",
            "connection_reset",
            "connection_closed",
        ))

    async def _emit_progress(self, task_id: str, *, task=None) -> None:
        if task is None:
            task = await self._repository.load(task_id)
        if task is None:
            return
        units = await self._repository.list_units(task_id)
        await self._emit(AgentEvent(
            type=CoreEventType.LONG_TASK_PROGRESS,
            run_id=self._parent_run_id or task.created_by_run_id,
            payload={
                "taskId": task.id,
                "status": task.status.value,
                "revision": task.revision,
                "totalUnits": task.total_units,
                "completedUnits": task.completed_units,
                "failedUnits": task.failed_units,
                "updateTime": task.update_time,
                "units": [
                    {
                        "id": item.id,
                        "plannerStepId": str(
                            item.metadata.get("plannerStepId") or item.id
                        ),
                        "position": item.position,
                        "status": item.status.value,
                        "attempt": item.attempt,
                        "maxAttempts": item.max_attempts,
                        "runId": item.run_id,
                        "outputRef": item.output_ref,
                        "errorCode": item.error_code,
                        "updateTime": item.update_time,
                    }
                    for item in units
                ],
            },
        ))

    async def _emit(
        self,
        event: AgentEvent,
        *,
        persist: bool = True,
    ) -> None:
        if self._observer is None:
            return
        await self._observer(LongTaskExecutionUpdate(
            event=event,
            persist=persist,
        ))

    async def _emit_child_event(
        self,
        task,
        unit,
        delegation_view: Mapping,
        event: AgentEvent,
        *,
        persist: bool | None = None,
    ) -> None:
        # Writer/Reviewer candidates are structured host payloads.  Streaming
        # their JSON tokens into chat exposes protocol data and makes the UI
        # look like the assistant is replying with JSON.  Thinking, tools,
        # lifecycle and the validated natural-language response still use the
        # ordinary child-Run event path.
        if event.type in {
            CoreEventType.MODEL_DELTA,
            CoreEventType.RUN_COMPLETED,
        }:
            return
        await self._emit(
            AgentEvent(
                type=CoreEventType.DELEGATION_EVENT,
                run_id=self._parent_run_id or task.created_by_run_id,
                payload={
                    **dict(delegation_view),
                    "childRunId": (
                        event.run_id
                        or delegation_view.get("childRunId")
                    ),
                    "unitId": unit.id,
                    "attempt": max(1, int(unit.attempt or 1)),
                    "event": {
                        "type": str(event.type),
                        "runId": str(event.run_id or ""),
                        "payload": dict(event.payload),
                    },
                },
            ),
            persist=(
                event.type not in {
                    CoreEventType.MODEL_DELTA,
                    CoreEventType.MODEL_THINKING_DELTA,
                }
                if persist is None
                else persist
            ),
        )

    async def _record_thinking_snapshot(
        self,
        task,
        unit,
        *,
        delegation_view: Mapping,
        run_id: str | None,
        content: str,
    ) -> None:
        normalized_run_id = str(run_id or "").strip()
        if not normalized_run_id or not content:
            return
        await self._composition.append_run_event(
            normalized_run_id,
            SCREENPLAY_LONG_TASK_THINKING_SNAPSHOT_EVENT,
            {
                "taskId": task.id,
                "unitId": unit.id,
                "attempt": max(1, int(unit.attempt or 1)),
                "content": content,
            },
        )
        await self._emit_child_event(
            task,
            unit,
            {**dict(delegation_view), "childRunId": normalized_run_id},
            AgentEvent(
                type=SCREENPLAY_LONG_TASK_THINKING_SNAPSHOT_EVENT,
                run_id=normalized_run_id,
                payload={
                    "taskId": task.id,
                    "unitId": unit.id,
                    "attempt": max(1, int(unit.attempt or 1)),
                    "content": content,
                },
            ),
            persist=False,
        )

    async def _build_batch_prompt(self, task, unit) -> str:
        return (await self._prepare_batch(task, unit)).prompt

    async def _passthrough_revision_if_clean(self, task, unit):
        """Complete a revision unit without a model call when review is clean."""

        requested_ids = tuple(
            str(item or "").strip()
            for item in unit.metadata.get("sceneIds", [])
            if str(item or "").strip()
        )
        if len(requested_ids) != 1:
            raise RuntimeError("long_task_revision_scope_invalid")
        all_units = tuple(await self._repository.list_units(task.id))
        units_by_id = {item.id: item for item in all_units}
        dependency_ids = _dependency_ancestor_ids(unit, units_by_id)
        dependencies = [
            units_by_id[item_id]
            for item_id in dependency_ids
            if item_id in units_by_id
            and units_by_id[item_id].status.value == "completed"
        ]
        reports = [
            thaw_json_mapping(item.metadata.get("reviewReport"))
            for item in dependencies
            if item.metadata.get("reviewReport") is not None
        ]
        if not reports:
            raise RuntimeError("long_task_revision_review_missing")
        relevant_issues = [
            thaw_json_mapping(issue)
            for report in reports
            for issue in report.get("issues", [])
            if isinstance(issue, Mapping)
            and any(
                str(scene_id or "").strip() in requested_ids
                for scene_id in issue.get("sceneIds", [])
            )
        ]
        if relevant_issues:
            return None
        original = next((
            thaw_json_value(scene)
            for dependency in reversed(dependencies)
            for scene in reversed(tuple(dependency.metadata.get("scenes", [])))
            if isinstance(scene, Mapping)
            and str(scene.get("sceneId") or "").strip() == requested_ids[0]
        ), None)
        if not isinstance(original, Mapping):
            raise RuntimeError("long_task_revision_source_missing")
        normalized = dict(original)
        return LongTaskUnitResult(
            output_ref=f"longtask://{task.id}/{unit.id}",
            metadata={
                "sceneIds": list(requested_ids),
                "scenes": [normalized],
                "continuitySummary": _continuity_summary_from_scenes(
                    [normalized]
                ),
                "assistantResponse": "连续性审阅未发现需要修改的问题，已沿用原场景。",
                "skippedModelCall": True,
            },
        )

    async def _prepare_batch(self, task, unit) -> "_PreparedBatch":
        task_metadata = thaw_json_mapping(task.metadata)
        scene_list = await self._load_scene_list(task_metadata)
        requested_ids = [str(item) for item in unit.metadata.get("sceneIds", [])]
        ordered_scenes = ordered_scene_mappings(scene_list)
        scenes_by_id = {
            str(scene.get("id")): scene
            for scene in ordered_scenes
            if isinstance(scene, Mapping)
        }
        selected = [
            scenes_by_id[item]
            for item in requested_ids
            if item in scenes_by_id
        ]
        if len(selected) != len(requested_ids):
            raise RuntimeError("long_task_scene_scope_changed")
        all_units = tuple(await self._repository.list_units(task.id))
        units_by_id = {item.id: item for item in all_units}
        dependency_ids = _dependency_ancestor_ids(unit, units_by_id)
        previous_units = sorted(
            (
                units_by_id[item_id]
                for item_id in dependency_ids
                if item_id in units_by_id
                and units_by_id[item_id].status.value == "completed"
            ),
            key=lambda item: item.position,
        )
        ancestor_generated = [
            thaw_json_value(scene)
            for previous in previous_units
            for scene in previous.metadata.get("scenes", [])
            if isinstance(scene, Mapping)
        ]
        latest_ancestor_by_scene: dict[str, Mapping] = {}
        for scene in ancestor_generated:
            scene_id = str(scene.get("sceneId") or "").strip()
            if scene_id:
                latest_ancestor_by_scene[scene_id] = scene
        recent_generated = ancestor_generated[-2:]
        latest_summary = next((
            str(previous.metadata.get("continuitySummary") or "").strip()
            for previous in reversed(previous_units)
            if str(previous.metadata.get("continuitySummary") or "").strip()
        ), "")
        accepted = await self._load_accepted_draft(task.owner_id)
        accepted_json = _mapping((accepted or {}).get("content_json"))
        accepted_tail = await self._accepted_tail(task.owner_id)
        scene_output_contract = {
            "assistantResponse": (
                "本批完成后直接展示给用户的自然语言回答；说明实际完成的场景、"
                "关键连续性变化和仍需注意的问题，不得说已转后台或仅报告任务状态"
            ),
            "scenes": [{
                "sceneId": "must equal requiredSceneIds in order",
                "sceneText": "Fountain screenplay text",
                "execution": {
                    "objectiveResult": "string",
                    "conflictResult": "string",
                    "turnResult": "string",
                    "continuityState": "string",
                    "unresolvedNotes": ["string"],
                },
                "continuitySummary": "compact cumulative state after this scene",
            }],
        }
        unit_kind = str(unit.metadata.get("unitKind") or "scene_generation")
        task_name = {
            "continuity_review": "review_screenplay_continuity",
            "scene_revision": "revise_screenplay_scene",
            "scene_generation": "write_screenplay_scene",
        }.get(unit_kind)
        if task_name is None:
            raise RuntimeError("unsupported_screenplay_execution_unit_kind")
        payload = {
            "task": task_name,
            "requiredSceneIds": requested_ids,
            "scenes": selected,
            "draftPosition": _draft_position_context(
                ordered_scenes=ordered_scenes,
                completed_scene_ids=[
                    str(item)
                    for item in accepted_json.get("completedSceneIds", [])
                    if str(item).strip()
                ],
                requested_scene_ids=requested_ids,
            ),
            "acceptedDraftTail": accepted_tail,
            # Only workflow-declared ancestors may contribute generated prose.
            # Independent branches receive stable scene-list boundaries
            # instead of racing on whichever sibling happened to finish first.
            "recentGeneratedScenes": recent_generated,
            "continuitySummary": latest_summary,
            "boundaryContext": _scene_boundary_context(
                ordered_scenes,
                requested_ids,
            ),
        }
        if unit_kind == "continuity_review":
            draft_scenes = [
                latest_ancestor_by_scene.get(scene_id)
                for scene_id in requested_ids
            ]
            if any(scene is None for scene in draft_scenes):
                raise RuntimeError("long_task_review_dependency_output_missing")
            payload["draftScenes"] = draft_scenes
            payload["outputContract"] = {
                "reviewedSceneIds": "must equal requiredSceneIds in order",
                "issues": [{
                    "id": "stable issue id",
                    "sceneIds": ["one or more ids from requiredSceneIds"],
                    "severity": "blocking | major | minor",
                    "category": "timeline | character | prop | setup_payoff | format | boundary",
                    "problem": "concise evidence-based problem",
                    "instruction": "specific revision instruction",
                }],
                "summary": "compact cross-episode review summary",
                "assistantResponse": "short user-facing review result",
            }
            prompt_prefix = (
                "你正在执行宿主编排的全局连续性审阅节点。审阅 draftScenes 的"
                "跨集时间线、人物状态、道具、伏笔回收、场景边界和 Fountain 格式。"
                "只返回紧凑的问题报告，不得复制、改写或返回任何 sceneText。"
                "没有问题时 issues 返回空数组。返回一个 JSON 对象，不要 Markdown，"
                "不要在 JSON 外解释。"
            )
        elif unit_kind == "scene_revision":
            draft_scenes = [
                latest_ancestor_by_scene.get(scene_id)
                for scene_id in requested_ids
            ]
            if any(scene is None for scene in draft_scenes):
                raise RuntimeError("long_task_revision_source_missing")
            review_issues = [
                thaw_json_mapping(issue)
                for previous in previous_units
                for report in (previous.metadata.get("reviewReport"),)
                if isinstance(report, Mapping)
                for issue in report.get("issues", [])
                if isinstance(issue, Mapping)
                and any(
                    str(scene_id or "").strip() in set(requested_ids)
                    for scene_id in issue.get("sceneIds", [])
                )
            ]
            if not review_issues:
                raise RuntimeError("long_task_revision_issues_missing")
            payload["draftScenes"] = draft_scenes
            payload["reviewIssues"] = review_issues
            payload["outputContract"] = scene_output_contract
            prompt_prefix = (
                "你正在执行宿主编排的定向修订节点。仅根据 reviewIssues 修订"
                " requiredSceneIds 中的单个场景，保留未被问题影响的内容和场景契约。"
                "必须逐项解决问题，并返回一个完整 JSON 对象；不要 Markdown，"
                "不要在 JSON 外解释。"
            )
        elif unit_kind == "scene_generation":
            payload["outputContract"] = scene_output_contract
            prompt_prefix = (
                "你正在执行宿主编排的正文创作步骤。只创作 "
                "requiredSceneIds 指定的场景，严格保持顺序，不得补写、跳过或重写"
                "其他场景。返回一个 JSON 对象，不要 Markdown，不要在 JSON 外解释。"
                "每场使用 Fountain 格式和 @人物名角色提示；continuitySummary 必须"
                "压缩保留人物状态、时间线、关系变化、关键道具、伏笔和未解决冲突。"
            )
        prompt = (
            prompt_prefix
            + "\n"
            + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        )
        return _PreparedBatch(prompt=prompt, scenes=tuple(selected))

    async def _validate_batch_result(self, task, unit, content: str):
        try:
            payload = parse_json_object(content)
        except Exception as error:
            raise RuntimeError("long_task_batch_output_invalid_json") from error
        raw_scenes = payload.get("scenes")
        if not isinstance(raw_scenes, list):
            raise RuntimeError("long_task_batch_scenes_missing")
        expected_ids = [str(item) for item in unit.metadata.get("sceneIds", [])]
        actual_ids = [
            str(item.get("sceneId") or "")
            for item in raw_scenes
            if isinstance(item, Mapping)
        ]
        if actual_ids != expected_ids or len(raw_scenes) != len(expected_ids):
            raise RuntimeError("long_task_batch_scene_coverage_mismatch")
        scene_list = await self._load_scene_list(thaw_json_mapping(task.metadata))
        scenes_by_id = {
            str(scene.get("id")): scene
            for scene in scene_list.get("scenes", [])
            if isinstance(scene, Mapping)
        }
        normalized = []
        for item in raw_scenes:
            scene_id = str(item.get("sceneId"))
            scene_text = str(item.get("sceneText") or "").strip()
            if (
                not scene_text
                or len(scene_text)
                > SCENE_DRAFT_PAYLOAD_LIMITS.scene_text_chars
            ):
                raise RuntimeError("long_task_batch_scene_text_invalid")
            execution = normalize_scene_execution(
                scene=scenes_by_id[scene_id],
                execution=item.get("execution"),
            )
            summary = str(item.get("continuitySummary") or "").strip()
            if not summary:
                summary = execution["continuityState"]
            normalized.append({
                "sceneId": scene_id,
                "sceneText": scene_text,
                "execution": execution,
                "continuitySummary": summary[:6_000],
            })
        assistant_response = str(payload.get("assistantResponse") or "").strip()
        if not assistant_response:
            assistant_response = _derived_batch_response(
                unit,
                normalized,
            )
        assistant_response = assistant_response[:6_000]
        return normalized, assistant_response

    async def _validate_review_result(self, unit, content: str):
        try:
            payload = parse_json_object(content)
        except Exception as error:
            raise RuntimeError("long_task_review_output_invalid_json") from error
        if any(key in payload for key in ("scenes", "draftScenes", "sceneText")):
            raise RuntimeError("long_task_review_prose_forbidden")
        expected_ids = [str(item) for item in unit.metadata.get("sceneIds", [])]
        reviewed_ids = payload.get("reviewedSceneIds")
        if not isinstance(reviewed_ids, list) or [
            str(item or "").strip() for item in reviewed_ids
        ] != expected_ids:
            raise RuntimeError("long_task_review_coverage_mismatch")
        raw_issues = payload.get("issues")
        if not isinstance(raw_issues, list) or len(raw_issues) > 64:
            raise RuntimeError("long_task_review_issues_invalid")
        allowed = set(expected_ids)
        issues: list[dict[str, object]] = []
        for index, raw_issue in enumerate(raw_issues):
            if not isinstance(raw_issue, Mapping):
                raise RuntimeError("long_task_review_issue_invalid")
            scene_ids = [
                str(item or "").strip()
                for item in raw_issue.get("sceneIds", [])
                if str(item or "").strip()
            ]
            severity = str(raw_issue.get("severity") or "").strip()
            if (
                not scene_ids
                or any(scene_id not in allowed for scene_id in scene_ids)
                or severity not in {"blocking", "major", "minor"}
            ):
                raise RuntimeError("long_task_review_issue_invalid")
            issue = {
                "id": str(raw_issue.get("id") or f"issue-{index + 1}"),
                "sceneIds": list(dict.fromkeys(scene_ids)),
                "severity": severity,
                "category": str(raw_issue.get("category") or "").strip(),
                "problem": str(raw_issue.get("problem") or "").strip(),
                "instruction": str(raw_issue.get("instruction") or "").strip(),
            }
            if any(
                not str(issue[field]) or len(str(issue[field])) > 4_000
                for field in ("category", "problem", "instruction")
            ):
                raise RuntimeError("long_task_review_issue_invalid")
            issues.append(issue)
        summary = str(payload.get("summary") or "").strip()[:6_000]
        report = {
            "reviewedSceneIds": expected_ids,
            "issues": issues,
            "summary": summary,
        }
        assistant_response = str(
            payload.get("assistantResponse") or ""
        ).strip()[:6_000]
        if not assistant_response:
            assistant_response = (
                f"已完成 {len(expected_ids)} 场跨集连续性审阅，"
                f"发现 {len(issues)} 个需要处理的问题。"
            )
        return report, assistant_response

    async def _finalize(self, task):
        task_metadata = thaw_json_mapping(task.metadata)
        scene_list = await self._load_scene_list(task_metadata)
        accepted = await self._load_accepted_draft(task.owner_id)
        ordered_scenes = ordered_scene_mappings(scene_list)
        scenes_by_id = {
            str(scene.get("id")): scene
            for scene in ordered_scenes
            if str(scene.get("id") or "")
        }
        units = await self._repository.list_units(task.id)
        target_ids = [str(item) for item in task_metadata.get("targetSceneIds", [])]
        generated_by_id: dict[str, Mapping] = {}
        generation_units = sorted(
            (
                unit for unit in units
                if str(unit.metadata.get("unitKind") or "")
                in {"", "scene_generation"}
                and unit.id != "finalize"
            ),
            key=lambda item: item.position,
        )
        for unit in generation_units:
            for scene in unit.metadata.get("scenes", []):
                if not isinstance(scene, Mapping):
                    continue
                scene_id = str(scene.get("sceneId") or "").strip()
                if not scene_id or scene_id in generated_by_id:
                    raise RuntimeError("long_task_final_coverage_mismatch")
                generated_by_id[scene_id] = scene
        if set(generated_by_id) != set(target_ids):
            raise RuntimeError("long_task_final_coverage_mismatch")
        reviewed_scene_ids = [
            str(scene_id)
            for unit in units
            if str(unit.metadata.get("unitKind") or "")
            == "continuity_review"
            for report in (unit.metadata.get("reviewReport"),)
            if isinstance(report, Mapping)
            for scene_id in report.get("reviewedSceneIds", [])
            if str(scene_id).strip()
        ]
        # Conditional Rewriters either persist one revised scene or a
        # deterministic passthrough of the Writer candidate. Apply those
        # scene checkpoints in canonical workflow order.
        revision_units = sorted(
            (
                unit for unit in units
                if str(unit.metadata.get("unitKind") or "")
                == "scene_revision"
            ),
            key=lambda item: item.position,
        )
        revised_scene_ids: set[str] = set()
        for unit in revision_units:
            unit_scene_ids: set[str] = set()
            for scene in unit.metadata.get("scenes", []):
                if not isinstance(scene, Mapping):
                    continue
                scene_id = str(scene.get("sceneId") or "").strip()
                if (
                    scene_id not in generated_by_id
                    or scene_id in unit_scene_ids
                ):
                    raise RuntimeError("long_task_review_coverage_mismatch")
                unit_scene_ids.add(scene_id)
                generated_by_id[scene_id] = scene
                revised_scene_ids.add(scene_id)
        if revised_scene_ids != set(target_ids):
            raise RuntimeError("long_task_review_coverage_mismatch")
        # Completion order is intentionally irrelevant.  The accepted scene
        # list range remains the single canonical assembly order.
        generated = [generated_by_id[scene_id] for scene_id in target_ids]
        accepted_json = _mapping((accepted or {}).get("content_json"))
        previous_ids = [str(item) for item in accepted_json.get("completedSceneIds", [])]
        if str(task_metadata.get("scope") or "") == "next_episodes":
            target_episodes = {
                scene.get("episodeNumber")
                for scene_id in target_ids
                for scene in (scenes_by_id.get(scene_id),)
                if scene is not None
            }
            previous_id_set = set(previous_ids)
            expected_target_ids = [
                str(scene.get("id"))
                for scene in ordered_scenes
                if str(scene.get("id")) not in previous_id_set
                and scene.get("episodeNumber") in target_episodes
            ]
            if expected_target_ids != target_ids:
                raise RuntimeError("long_task_final_scope_mismatch")
        previous_executions = accepted_json.get("sceneExecutions", [])
        completed_ids = [*previous_ids, *target_ids]
        executions = validate_scene_execution_history(
            scene_list_content=scene_list,
            completed_scene_ids=completed_ids,
            scene_executions=[
                *(previous_executions if isinstance(previous_executions, list) else []),
                *(scene["execution"] for scene in generated),
            ],
            previous_executions=(
                previous_executions if isinstance(previous_executions, list) else []
            ),
        )
        all_scene_ids = [
            str(scene.get("id"))
            for scene in ordered_scenes
            if str(scene.get("id") or "")
        ]
        content_text = "\n\n".join(
            str(scene["sceneText"]).strip() for scene in generated
        )
        scene_list_id = str(task_metadata.get("sceneListDocumentId") or "")
        target_headings = [
            str(scenes_by_id.get(scene_id, {}).get("heading") or "").strip()
            for scene_id in target_ids
        ]
        derived = [scene_list_id]
        if accepted is not None:
            derived.append(str(accepted["id"]))
        proposal = {
            "kind": "scene_draft",
            "title": f"Agent 长篇正文 · {target_ids[0]}–{target_ids[-1]}",
            "contentJson": {
                "schemaVersion": 1,
                "generatedBy": "screenplay-agent-long-task",
                "documentKind": "scene_draft",
                "sceneListId": scene_list_id,
                "sceneId": target_ids[0],
                "sceneHeading": target_headings[0],
                "newSceneIds": target_ids,
                "newSceneHeadings": target_headings,
                "completedSceneIds": completed_ids,
                "isComplete": completed_ids == all_scene_ids,
                "longTaskId": task.id,
                "continuityReviewedSceneIds": list(dict.fromkeys(
                    reviewed_scene_ids
                )),
                "episodeDrafts": build_episode_draft_batches(
                    scenes_by_id=scenes_by_id,
                    generated_scenes=generated,
                ),
            },
            "contentText": content_text,
            "derivedFromIds": list(dict.fromkeys(derived)),
        }
        final_response = (
            f"已完成本次批量创作，共 {len(target_ids)} 场："
            f"{'、'.join(target_headings)}。"
            "正文已按场景表顺序完成覆盖与连续性校验，并生成可应用提案。"
        )
        work_item = await self._work_items.get(task.work_item_id)
        await self._work_items.complete(WorkItemTransitionCommand(
            work_item_id=work_item.id,
            expected_revision=work_item.revision,
        ))
        return LongTaskUnitResult(
            output_ref=f"run://{task.created_by_run_id}/screenplay-proposal",
            metadata={
                "proposal": proposal,
                "finalResponse": final_response,
            },
        )

    async def _load_scene_list(self, metadata):
        row = await get_current_document(
            self._composition.database,
            str(metadata.get("projectId") or ""),
            kind="scene_list",
        )
        if (
            row is None
            or str(row.get("id") or "")
            != str(metadata.get("sceneListDocumentId") or "")
        ):
            raise RuntimeError("long_task_scene_list_changed")
        episode_rows = await list_structured_episode_rows(
            self._composition.database,
            document_id=str(row.get("id") or ""),
            include_content=True,
        )
        if not episode_rows:
            return _mapping(row.get("content_json"))
        return {
            "scenes": [
                dict(scene)
                for episode in episode_rows
                for scene in episode.get("content_json", {}).get("scenes", [])
                if isinstance(scene, Mapping)
            ],
        }

    async def _load_accepted_draft(self, project_id: str):
        return await latest_accepted_draft_document(
            self._composition.database,
            project_id,
            include_text=False,
        )

    async def _accepted_tail(self, project_id: str) -> str:
        episodes = await list_episode_rows(
            self._composition.database,
            project_id=project_id,
            status="accepted",
            include_content=True,
        )
        return (
            str(episodes[-1].get("content_text") or "")[-12_000:]
            if episodes
            else ""
        )


def _mapping(value):
    if isinstance(value, Mapping):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, Mapping) else {}


def _continuity_summary_from_scenes(scenes) -> str:
    if not scenes:
        return ""
    latest = str(scenes[-1].get("continuitySummary") or "").strip()
    unresolved = list(dict.fromkeys(
        str(note).strip()
        for scene in scenes
        for note in scene["execution"].get("unresolvedNotes", [])
        if str(note).strip()
    ))
    return (latest + ("\n未解决：" + "；".join(unresolved) if unresolved else ""))[:6_000]


def _dependency_ancestor_ids(unit, units_by_id: Mapping[str, object]) -> set[str]:
    ancestors: set[str] = set()
    pending = list(getattr(unit, "dependencies", ()) or ())
    while pending:
        dependency_id = str(pending.pop() or "").strip()
        if not dependency_id or dependency_id in ancestors:
            continue
        ancestors.add(dependency_id)
        dependency = units_by_id.get(dependency_id)
        if dependency is not None:
            pending.extend(getattr(dependency, "dependencies", ()) or ())
    return ancestors


def _scene_boundary_context(
    ordered_scenes: Sequence[Mapping[str, object]],
    requested_ids: Sequence[str],
) -> dict[str, object]:
    if not requested_ids:
        return {"previousScene": None, "nextScene": None}
    indexes = {
        str(scene.get("id") or ""): index
        for index, scene in enumerate(ordered_scenes)
    }
    first_index = indexes.get(str(requested_ids[0]))
    last_index = indexes.get(str(requested_ids[-1]))

    def compact(scene: Mapping[str, object] | None):
        if scene is None:
            return None
        return {
            key: thaw_json_value(scene.get(key))
            for key in (
                "id",
                "episodeNumber",
                "heading",
                "objective",
                "conflict",
                "turn",
                "synopsis",
                "characters",
                "location",
                "time",
            )
            if scene.get(key) is not None
        }

    previous_scene = (
        ordered_scenes[first_index - 1]
        if first_index is not None and first_index > 0
        else None
    )
    next_scene = (
        ordered_scenes[last_index + 1]
        if last_index is not None and last_index + 1 < len(ordered_scenes)
        else None
    )
    return {
        "previousScene": compact(previous_scene),
        "nextScene": compact(next_scene),
    }


def _draft_position_context(
    *,
    ordered_scenes: Sequence[Mapping[str, object]],
    completed_scene_ids: Sequence[str],
    requested_scene_ids: Sequence[str],
) -> dict[str, object]:
    completed = set(completed_scene_ids)
    requested = set(requested_scene_ids)
    episode_rows: dict[int, list[str]] = {}
    requested_episodes: list[int] = []
    for scene in ordered_scenes:
        raw_episode = scene.get("episodeNumber")
        if (
            isinstance(raw_episode, bool)
            or not isinstance(raw_episode, int)
            or raw_episode <= 0
        ):
            continue
        scene_id = str(scene.get("id") or "").strip()
        if not scene_id:
            continue
        episode_rows.setdefault(raw_episode, []).append(scene_id)
        if scene_id in requested and raw_episode not in requested_episodes:
            requested_episodes.append(raw_episode)
    completed_episodes = [
        episode
        for episode, scene_ids in episode_rows.items()
        if scene_ids and all(scene_id in completed for scene_id in scene_ids)
    ]
    first_requested_episode = (
        min(requested_episodes) if requested_episodes else None
    )
    preceding_episode = next(
        (
            episode
            for episode in sorted(episode_rows, reverse=True)
            if first_requested_episode is not None
            and episode < first_requested_episode
        ),
        None,
    )
    return {
        "completedSceneCount": len(completed_scene_ids),
        "completedEpisodeNumbers": completed_episodes,
        "assignedEpisodeNumbers": requested_episodes,
        "assignedSceneIds": list(requested_scene_ids),
        "precedingEpisodeNumber": preceding_episode,
        "precedingEpisodeSceneIds": (
            episode_rows.get(preceding_episode, [])
            if preceding_episode is not None
            else []
        ),
    }


def _unit_stream_title(unit) -> str:
    label = str(unit.metadata.get("label") or "").strip()
    if label:
        return label
    headings = unit.metadata.get("sceneHeadings")
    names = [
        str(item or "").strip()
        for item in (
            headings
            if isinstance(headings, Sequence)
            and not isinstance(headings, (str, bytes, bytearray))
            else ()
        )
        if str(item or "").strip()
    ]
    action = {
        "continuity_review": "审阅",
        "scene_revision": "修订",
    }.get(str(unit.metadata.get("unitKind") or ""), "创作")
    return f"{action} {'、'.join(names)}" if names else unit.id


def _agent_role_for_unit(
    unit_kind: str,
    *,
    declared_role: str = "",
) -> str:
    expected = {
        "scene_generation": "screenplay_writer",
        "continuity_review": "screenplay_reviewer",
        "scene_revision": "screenplay_rewriter",
    }.get(unit_kind)
    if expected is not None and (not declared_role or declared_role == expected):
        return expected
    raise RuntimeError("unsupported_screenplay_execution_unit_kind")


def _agent_title_for_unit(agent_role: str, unit) -> str:
    role = {
        "screenplay_reviewer": "剧本 Reviewer",
        "screenplay_rewriter": "剧本 Rewriter",
    }.get(agent_role, "剧本 Writer")
    attempt = max(1, int(unit.attempt or 1))
    suffix = f" · {unit.id}"
    if attempt > 1:
        suffix += f" · 重试 {attempt - 1}"
    return role + suffix


def _derived_batch_response(unit, scenes: Sequence[Mapping]) -> str:
    headings = [
        str(item or "").strip()
        for item in unit.metadata.get("sceneHeadings", [])
        if str(item or "").strip()
    ]
    scene_ids = [
        str(scene.get("sceneId") or "").strip()
        for scene in scenes
        if str(scene.get("sceneId") or "").strip()
    ]
    labels = headings or scene_ids
    scope = "、".join(labels)
    action = {
        "scene_revision": "定向修订",
        "continuity_review": "连续性审阅",
    }.get(str(unit.metadata.get("unitKind") or ""), "正文创作")
    return (
        f"已完成 {scope} 共 {len(scenes)} 场的{action}，"
        "并记录了各场的目标、冲突、转折和连续性状态。"
    )


@dataclass(frozen=True, slots=True)
class _PreparedBatch:
    prompt: str
    scenes: tuple[Mapping[str, object], ...]


__all__ = ["ScreenplayLongTaskExecution"]
