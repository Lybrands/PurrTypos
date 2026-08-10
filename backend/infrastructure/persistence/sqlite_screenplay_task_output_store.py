"""Host-owned screenplay payloads referenced by PurrA durable units."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any


class SqliteScreenplayTaskOutputStore:
    def __init__(self, db) -> None:
        self._db = db

    async def put(
        self,
        *,
        task_id: str,
        unit_id: str,
        output: Mapping[str, Any],
    ) -> str:
        output_ref = f"screenplay-task-output://{task_id}/{unit_id}"
        encoded = json.dumps(
            dict(output),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        async with self._db.transaction(cancellation_linearizable=True):
            existing = await self._db.fetch_one(
                "SELECT output_json FROM screenplay_agent_task_outputs "
                "WHERE task_id = ? AND unit_id = ?",
                [task_id, unit_id],
            )
            if existing is not None:
                if str(existing["output_json"]) != encoded:
                    raise RuntimeError("screenplay_task_output_conflict")
                return output_ref
            await self._db.execute(
                "INSERT INTO screenplay_agent_task_outputs "
                "(task_id, unit_id, output_ref, output_json) "
                "VALUES (?, ?, ?, ?)",
                [task_id, unit_id, output_ref, encoded],
            )
        return output_ref

    async def load(self, output_ref: str) -> dict[str, Any]:
        row = await self._db.fetch_one(
            "SELECT output_json FROM screenplay_agent_task_outputs "
            "WHERE output_ref = ?",
            [str(output_ref or "").strip()],
        )
        if row is None:
            raise LookupError("screenplay task output does not exist")
        value = json.loads(str(row["output_json"]))
        if not isinstance(value, dict):
            raise RuntimeError("screenplay task output is not an object")
        return value

    async def load_unit(
        self,
        task_id: str,
        unit_id: str,
    ) -> tuple[str, dict[str, Any]] | None:
        row = await self._db.fetch_one(
            "SELECT output_ref, output_json "
            "FROM screenplay_agent_task_outputs "
            "WHERE task_id = ? AND unit_id = ?",
            [task_id, unit_id],
        )
        if row is None:
            return None
        value = json.loads(str(row["output_json"]))
        if not isinstance(value, dict):
            raise RuntimeError("screenplay task output is not an object")
        return str(row["output_ref"]), value

    async def list_for_task(self, task_id: str) -> dict[str, dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT unit_id, output_json FROM screenplay_agent_task_outputs "
            "WHERE task_id = ? ORDER BY create_time, unit_id",
            [task_id],
        )
        return {
            str(row["unit_id"]): json.loads(str(row["output_json"]))
            for row in rows
        }


__all__ = ["SqliteScreenplayTaskOutputStore"]
