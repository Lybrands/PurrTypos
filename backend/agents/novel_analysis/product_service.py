"""Version-routed product controls for Novel Analysis execution."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping

from agents.novel_analysis.entry_service import (
    NovelAnalysisReplacementExecutionService,
)
from agents.novel_analysis.recovery_service import (
    NovelAnalysisReplacementRecoveryService,
)
from agents.novel_analysis.follow_up_service import (
    NovelAnalysisReplacementFollowUpService,
)
from agents.novel_analysis.edit_replay import (
    NovelAnalysisReplacementEditLifecycle,
)
from agents.novel_analysis.attempt_artifact import (
    NOVEL_ANALYSIS_ATTEMPT_ARTIFACT_NAMESPACE,
)
from agents.novel_analysis.review_artifact import (
    NOVEL_ANALYSIS_REVIEWED_ARTIFACT_NAMESPACE,
)
from agents.novel_analysis.recipe import NOVEL_ANALYSIS_REPLACEMENT_RECIPE_VERSION
from agents.shared.implementation import (
    AgentKind,
    replacement_implementation,
)
from agents.shared.implementation_registry import AgentLifecycleAction
from agents.shared.saved_model_binding import capture_saved_model_binding
from application.agent_cancellation_service import AgentCancellationService
from exceptions import AppError, NotFoundError
from purra.json_values import thaw_json_mapping
from purra.long_tasks import LongTaskStatus
from infrastructure.persistence.sqlite_artifact_repository import (
    SqliteArtifactRepository,
)


_TASK_NAMESPACE = "purrtypos.novel_analysis"
_REPLACEMENT_TASK_KIND = "novel_analysis.purra-native"
_LEGACY_TASK_KIND = "novel_source_analysis"
_LEGACY_READ_ONLY_MESSAGE = (
    "旧版小说分析仅供查看，请发起新的分析后再进行此操作"
)


class VersionedNovelAnalysisProductService:
    """Route create by policy and existing controls by persisted ownership."""

    def __init__(self, db, composition) -> None:
        self._db = db
        self._composition = composition
        self._tasks = composition.long_task_repository
        self._router = composition.agent_implementation_router
        self._cancellation = AgentCancellationService(db, composition)

    async def start(
        self,
        *,
        source_revision_id: str,
        command_id: str,
        prompt: str,
        runtime,
    ) -> dict[str, object]:
        route = self._router.for_create(AgentKind.NOVEL_ANALYSIS)
        if route.identity != replacement_implementation(AgentKind.NOVEL_ANALYSIS):
            raise AppError("新版小说分析当前不可用", 503)
        existing = await self._db.fetch_one(
            "SELECT id FROM ai_agent_runs WHERE binding_namespace = ? "
            "AND binding_aggregate_id = ? AND binding_command_id = ?",
            [_TASK_NAMESPACE, source_revision_id, command_id],
        )
        if existing is not None:
            return await self._start_receipt(
                source_revision_id, command_id, dispatch_active=False
            )
        active = await self._active_task(source_revision_id)
        if active is not None:
            metadata = thaw_json_mapping(active.metadata)
            active_command = str(
                metadata.get("commandId")
                or metadata.get("idempotencyKey")
                or ""
            )
            if active_command != command_id:
                raise AppError(
                    "已有来源分析尚未结束，请恢复或取消当前任务",
                    409,
                )
            return await self._start_receipt(
                source_revision_id, command_id, dispatch_active=False
            )
        entry = NovelAnalysisReplacementExecutionService(
            self._db,
            self._composition,
        )
        # Validate source scope and the host-owned saved model identity before
        # returning 202. The detached execution recompiles the immutable scope.
        await entry.build_request(
            source_revision_id=source_revision_id,
            command_id=command_id,
            prompt=prompt,
            runtime=runtime,
        )
        task = asyncio.create_task(self._drain(entry.run(
            source_revision_id=source_revision_id,
            command_id=command_id,
            prompt=prompt,
            runtime=runtime,
            signal=asyncio.Event(),
        )))
        self._composition.track_background_run(task)
        return await self._start_receipt(
            source_revision_id, command_id, dispatch_active=not task.done()
        )

    async def pause(
        self,
        task_id: str,
        *,
        expected_revision: int | None = None,
    ) -> dict[str, object]:
        task = await self._require_replacement_task(
            task_id,
            AgentLifecycleAction.CANCEL,
        )
        try:
            task = await self._tasks.pause(
                task.id,
                expected_revision=expected_revision,
                reason_code="user_paused_novel_analysis",
            )
        except ValueError as error:
            raise AppError(str(error), 409) from error
        await self._cancel_running_roots(task.id)
        return await self._task_receipt(task, command_status="completed")

    async def follow_up(
        self,
        *,
        source_revision_id: str,
        artifact_id: str | None,
        prompt: str,
        command_id: str,
        runtime,
    ) -> dict[str, object]:
        normalized_artifact_id = str(artifact_id or "").strip()
        lookup_id = normalized_artifact_id.removeprefix(
            "novel-analysis-v1://"
        ).removeprefix("novel-analysis-artifact://")
        artifact = None
        if lookup_id:
            artifact = await SqliteArtifactRepository(self._db).load(lookup_id)
            if artifact is None:
                raise NotFoundError("来源分析结果不存在")
            if artifact.namespace not in {
                NOVEL_ANALYSIS_ATTEMPT_ARTIFACT_NAMESPACE,
                NOVEL_ANALYSIS_REVIEWED_ARTIFACT_NAMESPACE,
            }:
                raise AppError(_LEGACY_READ_ONLY_MESSAGE, 409)
        elif self._router.for_create(
            AgentKind.NOVEL_ANALYSIS
        ).identity != replacement_implementation(AgentKind.NOVEL_ANALYSIS):
            raise AppError("新版小说分析当前不可用", 503)
        existing = await self._db.fetch_one(
            "SELECT id FROM ai_agent_runs WHERE binding_namespace = ? "
            "AND binding_aggregate_id = ? AND binding_command_id = ?",
            [_TASK_NAMESPACE, source_revision_id, command_id],
        )
        if existing is not None:
            return {
                "status": "accepted",
                "commandId": command_id,
                "dispatchActive": False,
            }
        active = await self._db.fetch_one(
            "SELECT id FROM ai_agent_runs WHERE binding_namespace = ? "
            "AND binding_aggregate_id = ? AND status IN ('pending', 'running')",
            [_TASK_NAMESPACE, source_revision_id],
        )
        if active is not None:
            raise AppError("已有来源分析交互正在进行，请稍后再试", 409)
        service = NovelAnalysisReplacementFollowUpService(
            self._db,
            self._composition,
        )
        await service.build_request(
            source_revision_id=source_revision_id,
            artifact_id=lookup_id,
            command_id=command_id,
            prompt=prompt,
            runtime=runtime,
        )
        background = asyncio.create_task(self._drain(service.run(
            source_revision_id=source_revision_id,
            artifact_id=lookup_id,
            command_id=command_id,
            prompt=prompt,
            runtime=runtime,
            signal=asyncio.Event(),
        )))
        self._composition.track_background_run(background)
        return {
            "status": "accepted",
            "commandId": command_id,
            "dispatchActive": not background.done(),
        }

    async def replace_turn(
        self,
        *,
        source_revision_id: str,
        command_id: str,
        target_run_id: str,
        prompt: str,
        runtime,
    ) -> dict[str, object]:
        row = await self._db.fetch_one(
            "SELECT id FROM ai_agent_runs WHERE id = ?",
            [target_run_id],
        )
        if row is None:
            raise NotFoundError("编辑的分析消息不存在")
        route = await self._router.for_run(
            target_run_id,
            action=AgentLifecycleAction.REPLAY,
            expected_agent_kind=AgentKind.NOVEL_ANALYSIS,
        )
        if route.identity != replacement_implementation(
            AgentKind.NOVEL_ANALYSIS,
            recipe_version=NOVEL_ANALYSIS_REPLACEMENT_RECIPE_VERSION,
        ):
            raise AppError(_LEGACY_READ_ONLY_MESSAGE, 409)
        lifecycle = NovelAnalysisReplacementEditLifecycle(
            self._db,
            source_revision_id=source_revision_id,
            command_id=command_id,
            target_run_id=target_run_id,
        )
        target = await lifecycle.inspect()
        existing = await self._db.fetch_one(
            "SELECT id FROM ai_agent_runs WHERE binding_namespace = ? "
            "AND binding_aggregate_id = ? AND binding_command_id = ?",
            [_TASK_NAMESPACE, source_revision_id, command_id],
        )
        if existing is not None:
            return {
                "status": "accepted",
                "sourceRevisionId": source_revision_id,
                "commandId": command_id,
                "dispatchActive": False,
            }
        active_run = await self._db.fetch_one(
            "SELECT id FROM ai_agent_runs WHERE binding_namespace = ? "
            "AND binding_aggregate_id = ? AND status IN ('pending', 'running')",
            [_TASK_NAMESPACE, source_revision_id],
        )
        if active_run is not None:
            raise AppError("已有来源分析交互正在进行，请稍后再试", 409)
        if target.interaction_kind == "follow_up":
            service = NovelAnalysisReplacementFollowUpService(
                self._db,
                self._composition,
            )
            await service.build_request(
                source_revision_id=source_revision_id,
                artifact_id=target.analysis_artifact_id,
                command_id=command_id,
                prompt=prompt,
                runtime=runtime,
                history_before_run_id=target.run_id,
            )
            updates = service.run(
                source_revision_id=source_revision_id,
                artifact_id=target.analysis_artifact_id,
                command_id=command_id,
                prompt=prompt,
                runtime=runtime,
                signal=asyncio.Event(),
                history_before_run_id=target.run_id,
                run_binding_lifecycle=lifecycle,
            )
        else:
            if await self._active_task(source_revision_id) is not None:
                raise AppError("已有来源分析尚未结束，请恢复或取消当前任务", 409)
            service = NovelAnalysisReplacementExecutionService(
                self._db,
                self._composition,
                enforce_create_policy=False,
            )
            await service.build_request(
                source_revision_id=source_revision_id,
                command_id=command_id,
                prompt=prompt,
                runtime=runtime,
                implementation_owner_run_id=target.run_id,
            )
            updates = service.run(
                source_revision_id=source_revision_id,
                command_id=command_id,
                prompt=prompt,
                runtime=runtime,
                signal=asyncio.Event(),
                run_binding_lifecycle=lifecycle,
                implementation_owner_run_id=target.run_id,
            )
        background = asyncio.create_task(self._drain(updates))
        self._composition.track_background_run(background)
        return {
            "status": "accepted",
            "sourceRevisionId": source_revision_id,
            "commandId": command_id,
            "dispatchActive": not background.done(),
        }

    async def resume(
        self,
        *,
        task_id: str,
        run_command_id: str,
        runtime,
        retry_failed: bool,
    ) -> dict[str, object]:
        task = await self._require_replacement_task(
            task_id,
            AgentLifecycleAction.RESUME,
        )
        expected = LongTaskStatus.FAILED if retry_failed else LongTaskStatus.PAUSED
        if task.status is not expected:
            raise AppError(
                "来源分析任务状态不允许按当前方式恢复",
                409,
            )
        metadata = thaw_json_mapping(task.metadata)
        requested_binding = await capture_saved_model_binding(self._db, runtime)
        if (
            requested_binding is None
            or requested_binding != metadata.get("runtimeBinding")
        ):
            raise AppError("来源分析恢复所用模型配置已变化", 409)
        entry = NovelAnalysisReplacementExecutionService(
            self._db,
            self._composition,
            enforce_create_policy=False,
        )
        recovery = NovelAnalysisReplacementRecoveryService(
            self._db,
            self._composition,
            entry_service=entry,
        )
        background = asyncio.create_task(self._drain(recovery.resume(
            task_id=task.id,
            run_command_id=run_command_id,
            runtime=runtime,
            signal=asyncio.Event(),
            retry_failed=retry_failed,
        )))
        self._composition.track_background_run(background)
        return await self._task_receipt(
            task,
            command_status="accepted",
            command_id=run_command_id,
        )

    async def cancel(self, task_id: str) -> dict[str, object]:
        task = await self._require_replacement_task(
            task_id,
            AgentLifecycleAction.CANCEL,
        )
        if not task.status.terminal:
            try:
                task = await self._tasks.cancel(task.id)
            except ValueError as error:
                raise AppError(str(error), 409) from error
        await self._cancel_running_roots(task.id)
        return await self._task_receipt(task, command_status="completed")

    async def _require_replacement_task(
        self,
        task_id: str,
        action: AgentLifecycleAction,
    ):
        task = await self._tasks.load(task_id)
        if task is None:
            raise NotFoundError("来源分析任务不存在")
        route = await self._router.for_run(
            task.created_by_run_id,
            action=action,
            expected_agent_kind=AgentKind.NOVEL_ANALYSIS,
        )
        if route.identity != replacement_implementation(
            AgentKind.NOVEL_ANALYSIS,
            recipe_version=NOVEL_ANALYSIS_REPLACEMENT_RECIPE_VERSION,
        ):
            raise AppError(_LEGACY_READ_ONLY_MESSAGE, 409)
        return task

    async def _active_task(self, source_revision_id: str):
        for kind in (_REPLACEMENT_TASK_KIND, _LEGACY_TASK_KIND):
            active = await self._tasks.find_active(
                namespace=_TASK_NAMESPACE,
                owner_id=source_revision_id,
                kind=kind,
            )
            if active is not None:
                return active
        return None

    async def _cancel_running_roots(self, task_id: str) -> None:
        for binding in await self._tasks.list_run_bindings(task_id):
            row = await self._db.fetch_one(
                "SELECT status FROM ai_agent_runs WHERE id = ?",
                [binding.run_id],
            )
            if row is not None and row.get("status") == "running":
                await self._cancellation.cancel(binding.run_id)

    async def _start_receipt(
        self,
        source_revision_id: str,
        command_id: str,
        *,
        dispatch_active: bool,
    ) -> dict[str, object]:
        rows = await self._db.fetch_all(
            "SELECT DISTINCT id FROM novel_source_sections "
            "WHERE revision_id = ?",
            [source_revision_id],
        )
        if not rows:
            revision = await self._db.fetch_one(
                "SELECT id FROM novel_source_revisions WHERE id = ?",
                [source_revision_id],
            )
            if revision is None:
                raise NotFoundError("来源版本不存在")
        return {
            "status": "accepted",
            "sourceRevisionId": source_revision_id,
            "commandId": command_id,
            "sectionCount": len(rows),
            "dispatchActive": dispatch_active,
        }

    async def _task_receipt(
        self,
        task,
        *,
        command_status: str,
        command_id: str | None = None,
    ) -> dict[str, object]:
        row = await self._db.fetch_one(
            "SELECT state_reason_code, state_reason_scope, metadata_json "
            "FROM ai_agent_long_tasks WHERE id = ?",
            [task.id],
        )
        metadata = _mapping((row or {}).get("metadata_json"))
        status = {
            "pending": "queued",
            "running": "running",
            "paused": "paused",
            "completed": "completed",
            "failed": "failed",
            "canceled": "canceled",
        }.get(task.status.value)
        reason = str((row or {}).get("state_reason_code") or "").strip() or None
        scope = str((row or {}).get("state_reason_scope") or "").strip()
        pause_kind = scope if scope in {"system", "user", "budget"} else None
        due = metadata.get("autoResumeNotBeforeMs")
        due = due if isinstance(due, int) and due > 0 else None
        return {
            "commandStatus": command_status,
            "taskId": task.id,
            "workflowStatus": status,
            "workflowPauseKind": pause_kind,
            "workflowReasonCode": reason,
            "workflowResumable": status in {"paused", "failed"},
            "workflowAutoResumeAtMs": due,
            "workflowAutoRecoveryEligible": bool(
                status == "paused"
                and pause_kind == "system"
                and due is not None
                and isinstance(metadata.get("runtimeBinding"), Mapping)
                and not metadata.get("autoRecoveryBudgetExceeded")
            ),
            "taskRevision": task.revision,
            "totalUnits": task.total_units,
            "completedUnits": task.completed_units,
            "failedUnits": task.failed_units,
            **({"commandId": command_id} if command_id else {}),
        }

    @staticmethod
    async def _drain(updates) -> None:
        async for _ in updates:
            pass


def _mapping(value) -> dict:
    if isinstance(value, Mapping):
        return thaw_json_mapping(value)
    if not value:
        return {}
    import json

    try:
        decoded = json.loads(str(value))
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


__all__ = ["VersionedNovelAnalysisProductService"]
