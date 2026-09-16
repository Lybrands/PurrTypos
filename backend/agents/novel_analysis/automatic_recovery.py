"""Replacement-owned automatic recovery for due Novel Analysis tasks."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable, Mapping

from agents.novel_analysis.domain import (
    NOVEL_ANALYSIS_REPLACEMENT_DOMAIN_NAMESPACE,
)
from agents.novel_analysis.entry_service import (
    NovelAnalysisReplacementExecutionService,
)
from agents.novel_analysis.recovery_service import (
    NovelAnalysisReplacementRecoveryService,
)
from agents.novel_analysis.planner_contract import SCALABLE_ANALYSIS_RECIPE_VERSION
from agents.novel_analysis.scalable_profile import (
    scalable_novel_analysis_implementation,
)
from infrastructure.persistence.sqlite_long_task_repository import (
    append_long_task_event,
)


logger = logging.getLogger(__name__)

_REPLACEMENT_TASK_KIND = "novel_analysis.scalable.v2"
_REPLACEMENT_IDENTITY = scalable_novel_analysis_implementation(
    recipe_version=SCALABLE_ANALYSIS_RECIPE_VERSION,
)


class NovelAnalysisReplacementAutomaticRecovery:
    """Dispatch only due tasks owned by the exact replacement identity."""

    def __init__(
        self,
        db,
        composition,
        *,
        recovery_factory: Callable[[], object] | None = None,
    ) -> None:
        self._db = db
        self._composition = composition
        self._recovery_factory = recovery_factory
        self._dispatching: set[str] = set()
        self._recover_lock = asyncio.Lock()

    async def recover_due(
        self,
        *,
        timestamp_ms: int | None = None,
    ) -> tuple[str, ...]:
        async with self._recover_lock:
            return await self._recover_due(timestamp_ms=timestamp_ms)

    async def _recover_due(
        self,
        *,
        timestamp_ms: int | None,
    ) -> tuple[str, ...]:
        now_ms = int(time.time() * 1_000) if timestamp_ms is None else int(
            timestamp_ms
        )
        rows = await self._db.fetch_all(
            "SELECT t.id, t.metadata_json FROM ai_agent_long_tasks AS t "
            "JOIN ai_agent_runs AS r ON r.id = t.created_by_run_id "
            "WHERE t.namespace = ? AND t.kind = ? AND t.status = 'paused' "
            "AND t.state_reason_scope = 'system' "
            "AND t.cancel_requested_at_ms IS NULL "
            "AND r.agent_kind = ? AND r.implementation_id = ? "
            "AND r.implementation_version = ? AND r.tool_contract_version = ? "
            "AND r.recipe_version = ? AND r.artifact_schema_version = ? "
            "ORDER BY t.update_time ASC, t.id ASC",
            [
                NOVEL_ANALYSIS_REPLACEMENT_DOMAIN_NAMESPACE,
                _REPLACEMENT_TASK_KIND,
                _REPLACEMENT_IDENTITY.agent_kind.value,
                _REPLACEMENT_IDENTITY.implementation_id,
                _REPLACEMENT_IDENTITY.implementation_version,
                _REPLACEMENT_IDENTITY.tool_contract_version,
                _REPLACEMENT_IDENTITY.recipe_version,
                _REPLACEMENT_IDENTITY.artifact_schema_version,
            ],
        )
        dispatched: list[str] = []
        for row in rows:
            task_id = str(row["id"])
            if task_id in self._dispatching:
                continue
            metadata = _metadata(row.get("metadata_json"))
            due_ms = _non_negative_int(metadata.get("autoResumeNotBeforeMs"))
            if (
                due_ms <= 0
                or due_ms > now_ms
                or bool(metadata.get("autoRecoveryBudgetExceeded"))
            ):
                continue
            attempt = _non_negative_int(metadata.get("automaticRecoveryAttempt"))
            try:
                recovery = self._create_recovery()
                runtime = await recovery.resolve_automatic_runtime(task_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                await self._record_event(
                    task_id=task_id,
                    attempt=attempt,
                    event_type="automatic_recovery_dispatch_failed",
                    reason_code="automatic_recovery_dispatch_failed",
                )
                logger.exception(
                    "Failed to prepare replacement Novel Analysis recovery for %s",
                    task_id,
                )
                continue
            if runtime is None:
                await self._record_event(
                    task_id=task_id,
                    attempt=attempt,
                    event_type="automatic_recovery_binding_unavailable",
                    reason_code="runtime_binding_unavailable",
                )
                continue
            command_id = f"novel-analysis-auto-recovery:{task_id}:{attempt}"
            self._dispatching.add(task_id)
            worker = asyncio.create_task(self._execute(
                recovery,
                task_id=task_id,
                command_id=command_id,
                attempt=attempt,
                runtime=runtime,
            ))
            self._composition.track_background_run(worker)
            await self._record_event(
                task_id=task_id,
                attempt=attempt,
                event_type="automatic_recovery_dispatched",
                reason_code="automatic_recovery_dispatched",
                payload={"commandId": command_id},
            )
            dispatched.append(task_id)
        return tuple(dispatched)

    def _create_recovery(self):
        if self._recovery_factory is not None:
            return self._recovery_factory()
        entry = NovelAnalysisReplacementExecutionService(
            self._db,
            self._composition,
            enforce_create_policy=False,
        )
        return NovelAnalysisReplacementRecoveryService(
            self._db,
            self._composition,
            entry_service=entry,
        )

    async def _execute(
        self,
        recovery,
        *,
        task_id: str,
        command_id: str,
        attempt: int,
        runtime,
    ) -> None:
        try:
            async for _ in recovery.resume(
                task_id=task_id,
                run_command_id=command_id,
                runtime=runtime,
                signal=asyncio.Event(),
                recovery_source="automatic",
            ):
                pass
        except asyncio.CancelledError:
            raise
        except Exception:
            await self._record_event(
                task_id=task_id,
                attempt=attempt,
                event_type="automatic_recovery_dispatch_failed",
                reason_code="automatic_recovery_dispatch_failed",
            )
            logger.exception(
                "Failed to dispatch replacement Novel Analysis recovery for %s",
                task_id,
            )
        finally:
            self._dispatching.discard(task_id)

    async def _record_event(
        self,
        *,
        task_id: str,
        attempt: int,
        event_type: str,
        reason_code: str,
        payload: Mapping[str, object] | None = None,
    ) -> None:
        await append_long_task_event(
            self._db,
            task_id=task_id,
            event_type=event_type,
            reason_code=reason_code,
            reason_scope="system",
            source_key=f"{task_id}:{event_type}:{attempt}",
            payload={
                "automaticRecoveryAttempt": attempt,
                **dict(payload or {}),
            },
        )


async def monitor_novel_analysis_replacement_recovery(
    recovery: NovelAnalysisReplacementAutomaticRecovery,
    *,
    poll_interval_seconds: float = 2.0,
    stop_event: asyncio.Event | None = None,
) -> None:
    if poll_interval_seconds <= 0:
        raise ValueError("novel analysis recovery poll interval must be positive")
    stop = stop_event or asyncio.Event()
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=poll_interval_seconds)
        except TimeoutError:
            pass
        if stop.is_set():
            return
        try:
            recovered = await recovery.recover_due()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "Failed to inspect due replacement Novel Analysis recoveries"
            )
            continue
        if recovered:
            logger.info(
                "Dispatched automatic recovery for %s replacement Novel Analysis "
                "task(s): %s",
                len(recovered),
                ", ".join(recovered),
            )


def _metadata(raw: object) -> dict[str, object]:
    try:
        value = json.loads(str(raw or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return dict(value) if isinstance(value, dict) else {}


def _non_negative_int(value: object) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


__all__ = [
    "NovelAnalysisReplacementAutomaticRecovery",
    "monitor_novel_analysis_replacement_recovery",
]
