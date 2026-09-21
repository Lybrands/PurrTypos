"""Operation-scoped usage receipts and Root Run aggregation for Screenplay."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
import json
from typing import Any

from agents.screenplay.contracts import ScreenplayPartOperationScope
from purra.json_values import canonical_json_digest
from purra.long_tasks import LongTaskStatus, LongTaskUsage


@dataclass(frozen=True, slots=True)
class ScreenplayOperationUsageReceipt:
    operation_scope_id: str
    task_id: str
    unit_id: str
    attempt: int
    root_run_id: str
    usage: LongTaskUsage
    metadata: Mapping[str, Any]

    def to_mapping(self) -> dict[str, object]:
        return {
            "operationScopeId": self.operation_scope_id,
            "taskId": self.task_id,
            "unitId": self.unit_id,
            "attempt": self.attempt,
            "rootRunId": self.root_run_id,
            "usage": self.usage.to_mapping(),
            "metadata": dict(self.metadata),
        }


class ScreenplayOperationUsageStore:
    """Persist immutable usage per Operation, then settle each Root exactly once."""

    def __init__(self, db) -> None:
        self._db = db

    async def record_from_events(
        self,
        *,
        scope: ScreenplayPartOperationScope,
        root_run_id: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> ScreenplayOperationUsageReceipt:
        root = str(root_run_id or "").strip()
        if not root:
            raise ValueError("Screenplay Operation usage Root Run is required")
        rows = await self._db.fetch_all(
            "SELECT event_type, payload_json FROM ai_agent_run_events "
            "WHERE run_id = ? AND event_type IN "
            "('model.call_recorded', 'context.usage_recorded') "
            "AND json_extract(payload_json, '$.executionScopeId') = ? "
            "ORDER BY id",
            [root, scope.operation_scope_id],
        )
        calls = 0
        reported: list[Mapping[str, Any]] = []
        for row in rows:
            payload = _json_mapping(row.get("payload_json"))
            if row.get("event_type") == "model.call_recorded":
                count = int(payload.get("count") or 0)
                if count < 0:
                    raise ValueError("Screenplay Operation model call count is invalid")
                calls += count
            else:
                reported.append(payload)
        if len(reported) > calls:
            raise ValueError("Screenplay Operation usage exceeds model calls")
        usage = LongTaskUsage(
            invocation_count=calls,
            unreported_usage_attempts=calls - len(reported),
            input_tokens=sum(_token(item, "actualInputTokens") for item in reported),
            generation_tokens=sum(
                _token(item, "actualGenerationTokens") for item in reported
            ),
            reasoning_tokens=(
                None
                if len(reported) != calls
                or any(item.get("reasoningTokens") is None for item in reported)
                else sum(_token(item, "reasoningTokens") for item in reported)
            ),
        )
        return await self.record(
            scope=scope,
            root_run_id=root,
            usage=usage,
            metadata=metadata,
        )

    async def record(
        self,
        *,
        scope: ScreenplayPartOperationScope,
        root_run_id: str,
        usage: LongTaskUsage,
        metadata: Mapping[str, Any] | None = None,
    ) -> ScreenplayOperationUsageReceipt:
        root = str(root_run_id or "").strip()
        if not root or not isinstance(usage, LongTaskUsage):
            raise ValueError("Screenplay Operation usage identity is incomplete")
        frozen_metadata = dict(metadata or {})
        encoded_metadata = _json_dump(frozen_metadata)
        digest = canonical_json_digest({
            "rootRunId": root,
            "usage": usage.to_mapping(),
            "metadata": frozen_metadata,
        })
        async with self._db.transaction(cancellation_linearizable=True):
            await self._db.execute(
                "INSERT OR IGNORE INTO screenplay_operation_usage_receipts "
                "(operation_scope_id, project_id, task_id, unit_id, attempt, "
                "root_run_id, invocation_count, unreported_usage_attempts, "
                "input_tokens, output_tokens, reasoning_tokens, metadata_json, "
                "usage_digest) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    scope.operation_scope_id,
                    scope.project_id,
                    scope.task_id,
                    scope.unit_id,
                    scope.attempt,
                    root,
                    usage.invocation_count,
                    usage.unreported_usage_attempts,
                    usage.input_tokens,
                    usage.generation_tokens,
                    usage.reasoning_tokens,
                    encoded_metadata,
                    digest,
                ],
            )
            row = await self._db.fetch_one(
                "SELECT * FROM screenplay_operation_usage_receipts "
                "WHERE operation_scope_id = ?",
                [scope.operation_scope_id],
            )
            if row is None or any((
                row["project_id"] != scope.project_id,
                row["task_id"] != scope.task_id,
                row["unit_id"] != scope.unit_id,
                int(row["attempt"]) != scope.attempt,
                row["root_run_id"] != root,
                row["usage_digest"] != digest,
            )):
                raise ValueError("Screenplay Operation usage receipt conflicts")
        return _receipt(row)

    async def require(
        self,
        scope: ScreenplayPartOperationScope,
    ) -> ScreenplayOperationUsageReceipt:
        row = await self._db.fetch_one(
            "SELECT * FROM screenplay_operation_usage_receipts "
            "WHERE operation_scope_id = ? AND project_id = ? AND task_id = ? "
            "AND unit_id = ? AND attempt = ?",
            [
                scope.operation_scope_id,
                scope.project_id,
                scope.task_id,
                scope.unit_id,
                scope.attempt,
            ],
        )
        if row is None:
            raise ValueError("Screenplay Operation usage receipt is missing")
        return _receipt(row)

    async def settle_task_roots(self, task_id: str, long_tasks) -> Mapping[str, Any]:
        rows = await self._db.fetch_all(
            "SELECT * FROM screenplay_operation_usage_receipts "
            "WHERE task_id = ? ORDER BY root_run_id, operation_scope_id",
            [str(task_id or "").strip()],
        )
        by_root: dict[str, list[ScreenplayOperationUsageReceipt]] = defaultdict(list)
        for row in rows:
            receipt = _receipt(row)
            by_root[receipt.root_run_id].append(receipt)
        roots: list[dict[str, object]] = []
        for root_run_id in sorted(by_root):
            usage = _aggregate(item.usage for item in by_root[root_run_id])
            task = await long_tasks.load(task_id)
            if task is None:
                raise ValueError("Screenplay usage task does not exist")
            updated = await long_tasks.record_usage(
                task_id,
                run_id=root_run_id,
                usage=usage,
                expected_revision=task.revision,
            )
            if updated.status not in {
                LongTaskStatus.RUNNING,
                LongTaskStatus.COMPLETED,
            }:
                raise ValueError("Screenplay usage settlement stopped the task")
            roots.append({"rootRunId": root_run_id, "usage": usage.to_mapping()})
        total = _aggregate(
            item.usage for receipts in by_root.values() for item in receipts
        )
        result = {"roots": roots, "total": total.to_mapping()}
        return {**result, "digest": canonical_json_digest(result)}


def _receipt(row) -> ScreenplayOperationUsageReceipt:
    return ScreenplayOperationUsageReceipt(
        operation_scope_id=str(row["operation_scope_id"]),
        task_id=str(row["task_id"]),
        unit_id=str(row["unit_id"]),
        attempt=int(row["attempt"]),
        root_run_id=str(row["root_run_id"]),
        usage=LongTaskUsage(
            invocation_count=int(row.get("invocation_count") or 0),
            unreported_usage_attempts=int(
                row.get("unreported_usage_attempts") or 0
            ),
            input_tokens=int(row.get("input_tokens") or 0),
            generation_tokens=int(row.get("output_tokens") or 0),
            reasoning_tokens=(
                None
                if row.get("reasoning_tokens") is None
                else int(row["reasoning_tokens"])
            ),
        ),
        metadata=_json_mapping(row.get("metadata_json")),
    )


def _aggregate(values) -> LongTaskUsage:
    frozen = tuple(values)
    return LongTaskUsage(
        invocation_count=sum(item.invocation_count for item in frozen),
        unreported_usage_attempts=sum(
            item.unreported_usage_attempts for item in frozen
        ),
        input_tokens=sum(item.input_tokens for item in frozen),
        generation_tokens=sum(item.generation_tokens for item in frozen),
        reasoning_tokens=(
            None
            if any(item.reasoning_tokens is None for item in frozen)
            else sum(int(item.reasoning_tokens or 0) for item in frozen)
        ),
    )


def _token(payload: Mapping[str, Any], key: str) -> int:
    value = int(payload.get(key) or 0)
    if value < 0:
        raise ValueError("Screenplay Operation token usage is invalid")
    return value


def _json_mapping(value: object) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, Mapping) else {}


def _json_dump(value: Mapping[str, Any]) -> str:
    canonical_json_digest(value)
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


__all__ = [
    "ScreenplayOperationUsageReceipt",
    "ScreenplayOperationUsageStore",
]
