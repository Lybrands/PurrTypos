"""Native screenplay Conversation/Turn application vertical slice."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator, Mapping
from typing import Any, Protocol

from agent_core.contracts import AgentRunResult, RunStatus
from agent_core.events import AgentEvent, CoreEventType
from application.request_mapping import build_chat_provider_options
from application.screenplay_agent_run_service import ScreenplayAgentRunService
from application.screenplay_sse_mapping import screenplay_update_to_sse_chunk
from application.screenplay_v2_service import ScreenplayV2ProjectService
from exceptions import AppError, NotFoundError
from infrastructure.persistence.sqlite_screenplay_conversation_repository import (
    SqliteScreenplayConversationRepository,
    runtime_profile,
)
from schemas.screenplay_agent_run import ScreenplayAgentRunRequest
from schemas.screenplay_conversation import (
    ScreenplayConversationRuntimeRequest,
    SubmitScreenplayConversationTurnRequest,
)
from utils.url import normalize_base_url


def _digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class ScreenplayTurnRunner(Protocol):
    def run(self, **kwargs) -> AsyncIterator[AgentEvent | AgentRunResult]: ...


class ScreenplayConversationService:
    def __init__(self, db, composition, *, runner: ScreenplayTurnRunner | None = None):
        self._db = db
        self._composition = composition
        self._repository = SqliteScreenplayConversationRepository(
            db,
            owner_id=composition.execution_owner_id,
        )
        self._projects = ScreenplayV2ProjectService(db)
        self._runner = runner or ScreenplayAgentRunService(composition)

    async def submit_turn(
        self,
        *,
        command_id: str,
        project_id: str,
        request: SubmitScreenplayConversationTurnRequest,
    ) -> dict[str, Any]:
        normalized_command_id = str(command_id or "").strip()
        normalized_project_id = str(project_id or "").strip()
        if not normalized_command_id:
            raise AppError("提交剧本对话必须提供 Idempotency-Key", 422)
        if len(normalized_command_id) > 200:
            raise AppError("Idempotency-Key 不能超过 200 个字符", 422)
        if not normalized_project_id:
            raise NotFoundError("剧本项目不存在")

        runtime = request.runtime
        profile = runtime_profile(
            api_provider=runtime.apiProvider,
            base_url=runtime.baseURL,
            options=runtime.options,
            locale=runtime.locale,
            context_window=runtime.contextWindow,
        )
        operation_payload = (
            request.operation.model_dump(mode="json")
            if request.operation is not None
            else None
        )
        request_digest = _digest({
            "projectId": normalized_project_id,
            "sessionId": request.sessionId,
            "content": request.content,
            "route": request.route,
            "operation": operation_payload,
            "runtimeProfile": profile,
        })
        return await self._repository.begin_turn(
            command_id=normalized_command_id,
            request_digest=request_digest,
            project_id=normalized_project_id,
            session_id=request.sessionId,
            route=request.route,
            content=request.content,
            runtime_profile=profile,
            operation=operation_payload,
        )

    def dispatch_turn(
        self,
        turn_id: str,
        runtime: ScreenplayConversationRuntimeRequest,
    ) -> asyncio.Task[None]:
        task = asyncio.create_task(self.execute_turn(turn_id, runtime))
        track = getattr(self._composition, "track_background_run", None)
        if callable(track):
            track(task)
        return task

    async def execute_turn(
        self,
        turn_id: str,
        runtime: ScreenplayConversationRuntimeRequest,
    ) -> None:
        if not await self._repository.claim_execution(turn_id):
            return
        turn = await self._repository.load_turn(turn_id)
        if turn is None:
            return
        heartbeat_task = asyncio.create_task(self._heartbeat_turn(turn_id))
        try:
            workspace = await self._projects.get_workspace(turn["projectId"])
            project = workspace["project"]
            source = project.get("source") if isinstance(project, Mapping) else {}
            source_book_id = (
                str(source.get("bookId") or "").strip() or None
                if isinstance(source, Mapping)
                else None
            )
            messages = await self._repository.history_messages(
                session_id=int(turn["sessionId"]),
                before_turn_id=turn_id,
            )
            options = dict(runtime.options)
            model = str(options.pop("model", "") or "").strip()
            temperature = options.pop("temperature", None)
            provider_options = build_chat_provider_options(
                model,
                options,
                normalize_base_url(runtime.baseURL),
                temperature,
            )
            operation_id = str(turn.get("operationId") or "").strip() or None
            operation = (
                await self._projects.get_operation(operation_id)
                if operation_id is not None
                else None
            )
            intent = (
                operation.get("intent", {})
                if isinstance(operation, Mapping)
                else {}
            )
            scope = (
                intent.get("scope", {})
                if isinstance(intent, Mapping)
                and isinstance(intent.get("scope"), Mapping)
                else {}
            )
            draft_scope = str(scope.get("mode") or "planner")
            draft_scene_count = scope.get("sceneCount", 1)
            if not isinstance(draft_scene_count, int):
                draft_scene_count = 1
            body = ScreenplayAgentRunRequest(
                messages=messages,
                apiKey=runtime.apiKey.get_secret_value(),
                baseURL=runtime.baseURL,
                apiProvider=runtime.apiProvider,
                locale=runtime.locale,
                options=dict(runtime.options),
                sessionId=int(turn["sessionId"]),
                enableAgentTools=operation is not None,
                chatAgentMode="agent" if operation is not None else "ask",
                contextWindow=runtime.contextWindow,
                agentProfile="screenplay",
                screenplayProjectId=str(turn["projectId"]),
                screenplayOperationId=operation_id,
                sourceBookId=source_book_id,
                activeStage=str(workspace["workflow"]["stage"]),
                screenplayTaskIntent=(
                    "stage_deliverable" if operation is not None else "chat"
                ),
                screenplayDraftSceneCount=(
                    draft_scene_count if operation is not None else 1
                ),
                screenplayDraftScope=(
                    draft_scope if operation is not None else "planner"
                ),
            )
            signal = asyncio.Event()
            stream = self._runner.run(
                body=body,
                api_key=runtime.apiKey.get_secret_value(),
                provider_options=provider_options,
                signal=signal,
                conversation_turn_id=turn_id,
            )
            result: AgentRunResult | None = None
            try:
                async for update in stream:
                    if isinstance(update, AgentEvent):
                        if update.type == CoreEventType.RUN_STARTED and update.run_id:
                            await self._repository.bind_run(turn_id, update.run_id)
                        chunk = screenplay_update_to_sse_chunk(
                            update,
                            model=model,
                        )
                        if chunk is None:
                            continue
                        revision = chunk.get("screenplayRevisionReady")
                        revision_id = (
                            str(revision.get("revisionId") or "").strip() or None
                            if isinstance(revision, Mapping)
                            else None
                        )
                        await self._repository.append_chunk(
                            turn_id,
                            chunk=chunk,
                            assistant_delta=str(chunk.get("delta") or ""),
                            revision_id=revision_id,
                        )
                    else:
                        result = update
            finally:
                await stream.aclose()
            if result is None:
                raise RuntimeError("screenplay conversation Run has no terminal result")
            if result.status is RunStatus.DONE:
                await self._repository.complete_turn(
                    turn_id,
                    final_response=result.final_response,
                )
            elif result.status is RunStatus.CANCELED:
                await self._repository.cancel_turn(
                    turn_id,
                    command_id=(
                        "screenplay:run-canceled:"
                        + hashlib.sha256(turn_id.encode("utf-8")).hexdigest()
                    ),
                    request_digest=_digest({"turnId": turn_id, "source": "run"}),
                )
            else:
                await self._repository.fail_turn(
                    turn_id,
                    code=result.error or result.status.value,
                    message=result.error or "剧本 Agent 未能完成本次对话",
                )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            await self._repository.fail_turn(
                turn_id,
                code="conversation_execution_failed",
                message=str(error),
            )
            await self._cancel_active_operation(turn)
        finally:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass

    async def _heartbeat_turn(self, turn_id: str) -> None:
        while True:
            await asyncio.sleep(10)
            if not await self._repository.heartbeat_execution(turn_id):
                return

    async def _cancel_active_operation(self, turn: Mapping[str, Any]) -> None:
        operation_id = str(turn.get("operationId") or "").strip()
        if not operation_id:
            return
        try:
            operation = await self._projects.get_operation(operation_id)
            if str(operation.get("status") or "") not in {
                "queued",
                "running",
                "paused",
            }:
                return
            await self._projects.control_operation(
                command_id=(
                    "screenplay:conversation-failed:"
                    + hashlib.sha256(turn["id"].encode("utf-8")).hexdigest()
                ),
                operation_id=operation_id,
                action="cancel",
            )
        except Exception:
            # The Turn failure remains authoritative even if the Operation was
            # concurrently terminalized by the Run lifecycle projector.
            return

    async def resume_turn(
        self,
        turn_id: str,
        *,
        command_id: str,
        runtime: ScreenplayConversationRuntimeRequest,
    ) -> dict[str, Any]:
        normalized_command_id = str(command_id or "").strip()
        if not normalized_command_id:
            raise AppError("恢复剧本对话必须提供 Idempotency-Key", 422)
        existing_turn = await self._repository.load_turn(turn_id)
        if existing_turn is None:
            raise NotFoundError("剧本对话 Turn 不存在")
        if str(existing_turn["status"]) in {"completed", "failed", "canceled"}:
            raise AppError("剧本对话 Turn 已结束，不能恢复", 409)
        operation_id = str(existing_turn.get("operationId") or "").strip()
        operation_status: str | None = None
        if operation_id:
            operation = await self._projects.get_operation(operation_id)
            operation_status = str(operation.get("status") or "")
            if operation_status not in {"paused", "queued", "running"}:
                raise AppError(
                    "剧本 Operation 已结束，不能恢复对应对话 Turn",
                    409,
                )
        profile = runtime_profile(
            api_provider=runtime.apiProvider,
            base_url=runtime.baseURL,
            options=runtime.options,
            locale=runtime.locale,
            context_window=runtime.contextWindow,
        )
        turn = await self._repository.prepare_resume(
            turn_id,
            command_id=normalized_command_id,
            request_digest=_digest({
                "turnId": turn_id,
                "runtimeProfile": profile,
            }),
        )
        if operation_id:
            if operation_status == "paused":
                await self._projects.control_operation(
                    command_id=(
                        "screenplay:conversation-resume-operation:"
                        + hashlib.sha256(
                            f"{turn_id}:{normalized_command_id}".encode("utf-8")
                        ).hexdigest()
                    ),
                    operation_id=operation_id,
                    action="resume",
                )
        self.dispatch_turn(turn_id, runtime)
        return turn

    async def get_snapshot(
        self,
        *,
        project_id: str,
        session_id: int,
    ) -> dict[str, Any]:
        return await self._repository.get_snapshot(
            project_id=str(project_id or "").strip(),
            session_id=session_id,
        )

    async def list_events(
        self,
        *,
        project_id: str,
        session_id: int,
        after: int,
        limit: int,
    ) -> dict[str, Any]:
        return await self._repository.list_events(
            project_id=str(project_id or "").strip(),
            session_id=session_id,
            after=max(0, int(after)),
            limit=max(1, min(500, int(limit))),
        )

    async def cancel_turn(
        self,
        turn_id: str,
        *,
        command_id: str,
    ) -> dict[str, Any]:
        normalized_command_id = str(command_id or "").strip()
        if not normalized_command_id:
            raise AppError("取消剧本对话必须提供 Idempotency-Key", 422)
        turn = await self._repository.cancel_turn(
            turn_id,
            command_id=normalized_command_id,
            request_digest=_digest({"turnId": turn_id, "action": "cancel"}),
        )
        run_id = str(turn.get("runId") or "").strip()
        if run_id:
            await self._composition.execution_lease_store.request_cancellation(
                run_id
            )
        operation_id = str(turn.get("operationId") or "").strip()
        if operation_id:
            await self._projects.control_operation(
                command_id=(
                    "screenplay:conversation-cancel:"
                    + hashlib.sha256(turn_id.encode("utf-8")).hexdigest()
                ),
                operation_id=operation_id,
                action="cancel",
            )
        return turn


__all__ = ["ScreenplayConversationService", "ScreenplayTurnRunner"]
