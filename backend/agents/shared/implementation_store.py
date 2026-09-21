"""SQLite persistence for immutable Agent implementation identity."""

from __future__ import annotations

from typing import Any

from agents.shared.implementation import (
    AgentImplementationIdentity,
    AgentKind,
    resolve_implementation,
)


_COLUMNS = (
    "agent_kind, implementation_id, implementation_version, "
    "tool_contract_version, recipe_version, artifact_schema_version"
)


class AgentImplementationConflictError(ValueError):
    """A Run was already assigned another immutable implementation."""


class SqliteAgentImplementationStore:
    def __init__(self, db) -> None:
        self._db = db

    async def bind(
        self,
        run_id: str,
        identity: AgentImplementationIdentity,
    ) -> AgentImplementationIdentity:
        normalized_run_id = _required_run_id(run_id)
        if self._db.current_task_owns_transaction():
            return await self._bind(normalized_run_id, identity)
        async with self._db.transaction(cancellation_linearizable=True):
            return await self._bind(normalized_run_id, identity)

    async def _bind(
        self,
        run_id: str,
        identity: AgentImplementationIdentity,
    ) -> AgentImplementationIdentity:
        row = await self._db.fetch_one(
            f"SELECT {_COLUMNS} FROM ai_agent_runs WHERE id = ?",
            [run_id],
        )
        if row is None:
            raise LookupError("Agent Run does not exist")
        persisted = _identity_from_row(row)
        if persisted is not None:
            if persisted != identity:
                raise AgentImplementationConflictError(
                    "Agent Run implementation identity conflicts"
                )
            return persisted
        if any(row.get(column) is not None for column in _COLUMN_NAMES):
            raise AgentImplementationConflictError(
                "Agent Run implementation identity is incomplete"
            )
        await self._db.execute(
            "UPDATE ai_agent_runs SET agent_kind = ?, implementation_id = ?, "
            "implementation_version = ?, tool_contract_version = ?, "
            "recipe_version = ?, artifact_schema_version = ? WHERE id = ?",
            [
                identity.agent_kind.value,
                identity.implementation_id,
                identity.implementation_version,
                identity.tool_contract_version,
                identity.recipe_version,
                identity.artifact_schema_version,
                run_id,
            ],
        )
        return identity

    async def load(
        self,
        run_id: str,
    ) -> AgentImplementationIdentity | None:
        row = await self._db.fetch_one(
            f"SELECT {_COLUMNS} FROM ai_agent_runs WHERE id = ?",
            [_required_run_id(run_id)],
        )
        if row is None:
            raise LookupError("Agent Run does not exist")
        identity = _identity_from_row(row)
        if identity is None and any(
            row.get(column) is not None for column in _COLUMN_NAMES
        ):
            raise AgentImplementationConflictError(
                "Agent Run implementation identity is incomplete"
            )
        return identity

    async def resolve(
        self,
        run_id: str,
        *,
        expected_agent_kind: AgentKind,
    ) -> AgentImplementationIdentity:
        persisted = await self.load(run_id)
        return resolve_implementation(
            None if persisted is None else persisted.to_mapping(),
            expected_agent_kind=expected_agent_kind,
        )


_COLUMN_NAMES = (
    "agent_kind",
    "implementation_id",
    "implementation_version",
    "tool_contract_version",
    "recipe_version",
    "artifact_schema_version",
)


def _identity_from_row(
    row: dict[str, Any],
) -> AgentImplementationIdentity | None:
    if all(row.get(column) is None for column in _COLUMN_NAMES):
        return None
    if any(
        row.get(column) is None
        for column in _COLUMN_NAMES
        if column != "recipe_version"
    ):
        return None
    return AgentImplementationIdentity(
        agent_kind=AgentKind(str(row["agent_kind"])),
        implementation_id=str(row["implementation_id"]),
        implementation_version=row["implementation_version"],
        tool_contract_version=row["tool_contract_version"],
        recipe_version=row.get("recipe_version"),
        artifact_schema_version=row["artifact_schema_version"],
    )


def _required_run_id(value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError("Agent Run id is required")
    return normalized


__all__ = [
    "AgentImplementationConflictError",
    "SqliteAgentImplementationStore",
]
