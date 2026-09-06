"""Keep screenplay claims behind their durable planning checkpoints."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from purra.errors import ContractViolationError
from purra.long_tasks import LongTaskRecord, LongTaskUnitRecord


class ScreenplayCheckpointClaimGuard:
    def __init__(self, db) -> None:
        self._db = db

    async def __call__(
        self,
        task: LongTaskRecord,
        candidate: LongTaskUnitRecord,
    ) -> bool:
        if not self._db.current_task_owns_transaction():
            raise RuntimeError("checkpoint claim admission requires the claim transaction")
        if task.namespace != "purrtypos.screenplay":
            return True
        if task.metadata.get("checkpointPlanningEnabled") is not True:
            return True
        operation_id = str(task.metadata.get("operationId") or "").strip()
        if not operation_id:
            return True
        rows = await self._db.fetch_all(
            "SELECT position, status, metadata_json "
            "FROM ai_agent_long_task_units WHERE task_id = ? "
            "ORDER BY position ASC",
            [task.id],
        )
        expected: set[str] = set()
        review_rows: list[Mapping[str, Any]] = []
        for row in rows:
            metadata = json.loads(row.get("metadata_json") or "{}")
            unit_input = metadata.get("input") or {}
            if (
                str(metadata.get("unitKind") or "") != "validate_manifest_part"
                or not isinstance(unit_input, Mapping)
            ):
                continue
            validation_kind = str(unit_input.get("validationKind") or "")
            if validation_kind == "review_episode":
                review_rows.append(row)
            if int(row["position"]) >= candidate.position:
                continue
            if str(row["status"]) != "completed":
                continue
            if validation_kind == "draft_episode":
                episode = int(unit_input.get("episodeNumber") or 0)
                if episode:
                    expected.add(f"episode:{episode}")
            elif validation_kind == "document":
                expected.add("document:sections")
        if (
            review_rows
            and max(int(row["position"]) for row in review_rows) < candidate.position
            and all(str(row["status"]) == "completed" for row in review_rows)
        ):
            expected.add("review:aggregate")
        if not expected:
            return True
        checkpoints = await self._db.fetch_all(
            "SELECT checkpoint_key, status, error_code "
            "FROM screenplay_checkpoint_plans WHERE operation_id = ?",
            [operation_id],
        )
        by_key = {str(row["checkpoint_key"]): row for row in checkpoints}
        for checkpoint_key in expected:
            checkpoint = by_key.get(checkpoint_key)
            if checkpoint is not None and str(checkpoint["status"]) == "failed":
                raise ContractViolationError(
                    "screenplay checkpoint planning failed",
                    code=str(
                        checkpoint.get("error_code") or "screenplay_checkpoint_failed"
                    )[:240],
                )
        applied_keys = {
            key for key, row in by_key.items() if str(row["status"]) == "applied"
        }
        return expected.issubset(applied_keys)
