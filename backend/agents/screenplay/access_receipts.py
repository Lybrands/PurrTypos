"""Durable receipts for resources actually consumed by a Screenplay Operation."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from agents.screenplay.contracts import ScreenplayPartOperationScope
from purra.json_values import canonical_json_digest


class ScreenplayAccessKind(StrEnum):
    REVISION = "revision"
    SOURCE_ITEM = "source_item"
    DEPENDENCY = "dependency"


@dataclass(frozen=True, slots=True)
class ScreenplayAccessReceipt:
    operation_scope_id: str
    access_kind: ScreenplayAccessKind
    resource_ref: str
    content_digest: str
    metadata: Mapping[str, Any]

    def to_mapping(self) -> dict[str, object]:
        return {
            "operationScopeId": self.operation_scope_id,
            "accessKind": self.access_kind.value,
            "resourceRef": self.resource_ref,
            "contentDigest": self.content_digest,
            "metadata": dict(self.metadata),
        }


class ScreenplayOperationAccessReceiptStore:
    def __init__(self, db) -> None:
        self._db = db

    async def record(
        self,
        *,
        scope: ScreenplayPartOperationScope,
        run_id: str,
        access_kind: ScreenplayAccessKind | str,
        resource_ref: str,
        content_digest: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> ScreenplayAccessReceipt:
        kind = ScreenplayAccessKind(access_kind)
        observed_run = str(run_id or "").strip()
        resource = str(resource_ref or "").strip()
        digest = str(content_digest or "").strip()
        if not observed_run or not resource or not digest:
            raise ValueError("Screenplay access receipt identity is incomplete")
        frozen_metadata = dict(metadata or {})
        canonical_json_digest(frozen_metadata)
        encoded = json.dumps(
            frozen_metadata,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        async with self._db.transaction(cancellation_linearizable=True):
            await self._db.execute(
                "INSERT OR IGNORE INTO screenplay_operation_access_receipts "
                "(operation_scope_id, project_id, task_id, unit_id, attempt, "
                "access_kind, resource_ref, content_digest, metadata_json, "
                "observed_by_run_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    scope.operation_scope_id,
                    scope.project_id,
                    scope.task_id,
                    scope.unit_id,
                    scope.attempt,
                    kind.value,
                    resource,
                    digest,
                    encoded,
                    observed_run,
                ],
            )
            row = await self._db.fetch_one(
                "SELECT * FROM screenplay_operation_access_receipts "
                "WHERE operation_scope_id = ? AND access_kind = ? "
                "AND resource_ref = ?",
                [scope.operation_scope_id, kind.value, resource],
            )
            if row is None or any((
                row["project_id"] != scope.project_id,
                row["task_id"] != scope.task_id,
                row["unit_id"] != scope.unit_id,
                int(row["attempt"]) != scope.attempt,
                row["content_digest"] != digest,
                row["metadata_json"] != encoded,
                row["observed_by_run_id"] != observed_run,
            )):
                raise ValueError("Screenplay access receipt identity conflict")
        return ScreenplayAccessReceipt(
            operation_scope_id=scope.operation_scope_id,
            access_kind=kind,
            resource_ref=resource,
            content_digest=digest,
            metadata=frozen_metadata,
        )

    async def list_for_scope(
        self,
        scope: ScreenplayPartOperationScope,
    ) -> tuple[ScreenplayAccessReceipt, ...]:
        rows = await self._db.fetch_all(
            "SELECT access_kind, resource_ref, content_digest, metadata_json "
            "FROM screenplay_operation_access_receipts "
            "WHERE operation_scope_id = ? AND project_id = ? AND task_id = ? "
            "AND unit_id = ? AND attempt = ? "
            "ORDER BY access_kind, resource_ref",
            [
                scope.operation_scope_id,
                scope.project_id,
                scope.task_id,
                scope.unit_id,
                scope.attempt,
            ],
        )
        return tuple(ScreenplayAccessReceipt(
            operation_scope_id=scope.operation_scope_id,
            access_kind=ScreenplayAccessKind(row["access_kind"]),
            resource_ref=str(row["resource_ref"]),
            content_digest=str(row["content_digest"]),
            metadata=json.loads(row["metadata_json"]),
        ) for row in rows)


def screenplay_access_receipt_digest(
    receipts: tuple[ScreenplayAccessReceipt, ...],
) -> str:
    return canonical_json_digest([item.to_mapping() for item in receipts])


__all__ = [
    "ScreenplayAccessKind",
    "ScreenplayAccessReceipt",
    "ScreenplayOperationAccessReceiptStore",
    "screenplay_access_receipt_digest",
]
