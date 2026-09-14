"""Replacement-only Screenplay conversation application entry."""

from __future__ import annotations

import asyncio
import uuid

from agents.screenplay.conversation_projection import (
    ScreenplayReplacementConversationStore,
    ScreenplayReplacementTurnLifecycle,
)
from agents.screenplay.contracts import ScreenplayStageCommand
from agents.screenplay.conversation_query import (
    ScreenplayReplacementConversationQuery,
)
from agents.screenplay.entry_service import ScreenplayReplacementExecutionService
from agents.screenplay.recipe import ScreenplayHostRecipeSpec
from agents.screenplay.recovery_service import (
    ScreenplayReplacementRecoveryService,
)
from agents.screenplay.truncation_service import (
    ScreenplayReplacementTruncationService,
)
from agents.screenplay.ordinary_service import (
    ScreenplayReplacementOrdinaryService,
)
from application.agent_cancellation_service import AgentCancellationService
from exceptions import AppError, NotFoundError


class ScreenplayReplacementConversationService:
    def __init__(self, db, composition, *, owner_id: str, runs=None) -> None:
        self._db = db
        self._composition = composition
        self._entry = ScreenplayReplacementExecutionService(
            db, composition, runs=runs
        )
        self._turns = ScreenplayReplacementConversationStore(
            db, owner_id=owner_id
        )
        self._recovery = ScreenplayReplacementRecoveryService(
            db,
            composition,
            entry_service=self._entry,
        )
        self._truncation = ScreenplayReplacementTruncationService(db)
        self._query = ScreenplayReplacementConversationQuery(
            db, output_repository=composition.output_journal
        )
        self._ordinary = ScreenplayReplacementOrdinaryService(
            db, composition, runs=runs
        )

    async def submit_turn(
        self,
        *,
        command_id: str,
        project_id: str,
        session_id: int | None = None,
        content: str | None = None,
        stage_command: ScreenplayStageCommand | None = None,
        runtime=None,
        request=None,
    ):
        if request is not None:
            session_id = request.sessionId
            content = request.content
            runtime = request.runtime
            wire_command = request.stageCommand
            if wire_command is None:
                turn_id = "spaturn_" + uuid.uuid4().hex
                ordinary_request = await self._ordinary.build_request(
                    project_id=project_id,
                    session_id=session_id,
                    turn_id=turn_id,
                    command_id=command_id,
                    prompt=content,
                    runtime=runtime,
                )
                return await self._turns.admit_ordinary(
                    request=ordinary_request,
                    command_id=command_id,
                    user_content=content,
                )
            stage_command = ScreenplayStageCommand.from_mapping(
                wire_command.model_dump(mode="json")
            )
        if not isinstance(stage_command, ScreenplayStageCommand):
            raise ValueError(
                "Screenplay replacement requires an explicit formal stage command"
            )
        existing = await self._turns.find_command(
            project_id=project_id, command_id=command_id
        )
        if existing is not None:
            if any((
                existing["sessionId"] != session_id,
                existing["userContent"] != content,
                existing["stageCommand"] != stage_command.to_mapping(),
            )):
                raise AppError("同一个对话命令对应了不同请求", 409)
            return existing
        turn_id = "spaturn_" + uuid.uuid4().hex
        request = await self._entry.build_request(
            project_id=project_id,
            session_id=session_id,
            turn_id=turn_id,
            command_id=command_id,
            prompt=content,
            stage_command=stage_command,
            runtime=runtime,
        )
        return await self._turns.admit(
            request=request,
            command_id=command_id,
            user_content=content,
            stage_command=stage_command.to_mapping(),
        )

    async def execute_turn(self, turn_id: str, runtime, *, signal=None):
        visible_turn = await self._turns.load_turn(turn_id)
        if visible_turn is None:
            raise NotFoundError("剧本 Agent Turn 不存在")
        if visible_turn["stageCommand"] is None:
            lifecycle = ScreenplayReplacementTurnLifecycle(
                self._db, self._turns, turn_id=turn_id
            )
            await lifecycle.validate()
            terminal = None
            try:
                async for update in self._ordinary.run(
                    project_id=visible_turn["projectId"],
                    session_id=visible_turn["sessionId"],
                    turn_id=visible_turn["id"],
                    command_id=visible_turn["commandId"],
                    prompt=visible_turn["userContent"],
                    runtime=runtime,
                    signal=signal or asyncio.Event(),
                    run_binding_lifecycle=lifecycle,
                ):
                    terminal = update
            except BaseException as error:
                await lifecycle.on_start_failed(
                    str(getattr(error, "code", "") or type(error).__name__)
                )
                raise
            return terminal
        execution = await self._turns.load_execution(turn_id)
        if execution is None:
            raise NotFoundError("剧本 Agent Turn 不存在")
        turn = execution["turn"]
        requirements = execution["requirements"]
        recipe = ScreenplayHostRecipeSpec.from_mapping(
            requirements.get("hostRecipe")
        )
        expected_binding = requirements.get("runtimeBinding")
        if not isinstance(expected_binding, dict):
            raise ValueError("Screenplay replacement runtime binding is missing")
        command = ScreenplayStageCommand.from_mapping(turn["stageCommand"])
        lifecycle = ScreenplayReplacementTurnLifecycle(
            self._db, self._turns, turn_id=turn_id
        )
        await lifecycle.validate()
        terminal = None
        try:
            async for update in self._entry.run(
                project_id=turn["projectId"],
                session_id=turn["sessionId"],
                turn_id=turn["id"],
                command_id=turn["commandId"],
                prompt=turn["userContent"],
                stage_command=command,
                runtime=runtime,
                host_recipe=recipe,
                expected_runtime_binding=expected_binding,
                signal=signal or asyncio.Event(),
                run_binding_lifecycle=lifecycle,
            ):
                terminal = update
        except BaseException as error:
            await lifecycle.on_start_failed(
                str(getattr(error, "code", "") or type(error).__name__)
            )
            raise
        return terminal

    def dispatch_turn(self, turn_id: str, runtime):
        task = asyncio.create_task(self.execute_turn(turn_id, runtime))
        self._composition.track_background_run(task)
        return task

    async def load_turn(self, turn_id: str):
        return await self._turns.load_turn(turn_id)

    async def cancel_turn(
        self,
        turn_id: str,
        *,
        command_id: str | None = None,
        idempotency_key: str | None = None,
    ):
        receipt = await self._turns.request_cancel(
            turn_id, command_id=str(command_id or idempotency_key or ""),
        )
        root_run_id = str(receipt.get("rootRunId") or "").strip()
        if receipt["status"] == "running" and root_run_id:
            cancellation = await AgentCancellationService(
                self._db,
                self._composition,
            ).cancel(root_run_id)
            if cancellation is None:
                raise ValueError("Screenplay replacement Root is unavailable")
            receipt = await self._turns.cancel_view(turn_id)
            receipt["runCancellation"] = cancellation
        return receipt

    async def resume_operation(
        self,
        operation_id: str,
        *,
        command_id: str,
        expected_operation_revision: int,
        runtime,
        signal=None,
    ):
        terminal = None
        async for update in self._recovery.resume(
            operation_id=operation_id,
            run_command_id=command_id,
            expected_operation_revision=expected_operation_revision,
            runtime=runtime,
            signal=signal or asyncio.Event(),
        ):
            terminal = update
        return terminal

    async def prepare_resume(self, operation_id: str, *, idempotency_key: str, request):
        receipt = None
        async for update in self._recovery.resume(
            operation_id=operation_id,
            run_command_id=idempotency_key,
            expected_operation_revision=request.expectedOperationRevision,
            runtime=request.runtime,
            signal=asyncio.Event(),
            reserve_only=True,
        ):
            receipt = update
        if not isinstance(receipt, dict):
            raise ValueError("Screenplay replacement resume receipt is missing")
        return receipt

    def dispatch_resumed_operation(
        self,
        operation_id: str,
        runtime,
        *,
        continuation_command: str,
    ):
        task = asyncio.create_task(self._drain_reserved_resume(
            operation_id=operation_id,
            runtime=runtime,
            continuation_command=continuation_command,
        ))
        self._composition.track_background_run(task)
        return task

    async def _drain_reserved_resume(
        self,
        *,
        operation_id: str,
        runtime,
        continuation_command: str,
    ) -> None:
        async for _update in self._recovery.resume_reserved(
            operation_id=operation_id,
            run_command_id=continuation_command,
            runtime=runtime,
            signal=asyncio.Event(),
        ):
            pass

    async def get_snapshot(self, *, project_id: str, session_id: int):
        return await self._query.snapshot(
            project_id=project_id, session_id=session_id
        )

    async def resolve_resume_runtime(self, operation_id: str):
        return await self._recovery.resolve_automatic_runtime(operation_id)

    async def recover_stale_admissions(self, *, timestamp_ms: int | None = None):
        return await self._recovery.recover_stale_admissions(
            timestamp_ms=timestamp_ms
        )

    async def truncate_from_turn(self, turn_id: str):
        rows = await self._truncation.tail(turn_id)
        for row in rows:
            if str(row.get("turn_status") or "") not in {
                "queued", "planning", "running", "paused",
            }:
                continue
            await self.cancel_turn(
                str(row["turn_id"]),
                command_id=f"truncate:{row['turn_id']}:cancel",
            )
        return await self._truncation.delete_terminal_tail(turn_id)


__all__ = ["ScreenplayReplacementConversationService"]
