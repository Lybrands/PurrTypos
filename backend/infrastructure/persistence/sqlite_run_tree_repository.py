"""Durable command journal for PurrA's public reference tree state machine.

Only tree state transitions are replayed, never tools or model invocations.
Transactions serialize workers; a warm instance applies only the journal tail.
The codec is explicit and versioned: incompatible journals fail closed.
"""
from collections.abc import Mapping, Sequence
from dataclasses import fields, is_dataclass
import json
import time

from purra.api import (
    AgentCapabilityGrant, BeginRootAgentCommand, ChildAgentSpec,
    ContinueAgentCommand, InMemoryRunTreeRepository, SpawnAgentsCommand,
)
from purra.errors import ContractViolationError
from purra.adapter_state import AgentTreeState

_TYPES = {cls.__name__: cls for cls in (
    AgentCapabilityGrant, BeginRootAgentCommand, ChildAgentSpec,
    ContinueAgentCommand, SpawnAgentsCommand,
)}
_MUTATIONS = frozenset((
    "begin_root", "spawn_agents", "continue_agent", "claim_run",
    "renew_run_lease", "mark_waiting", "release_waiting", "suspend_run",
    "complete_run", "fail_run", "cancel_subtree", "close_agent",
))
# Replay is bound to the exact reference transition implementation, not just
# its package version. A future implementation needs an explicit migration.
_SCHEMA = "purrtypos.run-tree/v4:f299728c25054ba5fac541c7c74a6cfd941ef79dd1bcf6fec1d73b5562fc3718"


def _encode(value):
    if is_dataclass(value) and type(value).__name__ in _TYPES:
        return {"type": type(value).__name__, "fields": {
            f.name: _encode(getattr(value, f.name)) for f in fields(value)
        }}
    if isinstance(value, Mapping):
        return {"map": {key: _encode(item) for key, item in value.items()}}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return {"list": [_encode(item) for item in value]}
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise TypeError("Unsupported tree journal value")


def _decode(value):
    if not isinstance(value, dict):
        return value
    if set(value) == {"type", "fields"} and value["type"] in _TYPES:
        return _TYPES[value["type"]](**{k: _decode(v) for k, v in value["fields"].items()})
    if set(value) == {"map"}:
        return {k: _decode(v) for k, v in value["map"].items()}
    if set(value) == {"list"}:
        return tuple(_decode(v) for v in value["list"])
    raise ContractViolationError("Invalid tree journal codec", code="run_tree_journal_invalid")


class SqliteRunTreeRepository:
    def __init__(self, db, *, clock_ms=None):
        self._db = db
        self._clock = clock_ms or (lambda: int(time.time() * 1000))
        self._reset()

    def _reset(self, run_sequence=0):
        self._now = 0
        self._revision = 0
        self._tree = InMemoryRunTreeRepository(
            clock_ms=lambda: self._now,
            state=AgentTreeState(run_sequence=run_sequence),
        )

    async def _load_initial_run_sequence(self):
        await self._db.execute(
            "CREATE TABLE IF NOT EXISTS ai_agent_tree_state_v4 ("
            "singleton INTEGER PRIMARY KEY CHECK (singleton = 1), "
            "initial_run_sequence INTEGER NOT NULL)"
        )
        row = await self._db.fetch_one(
            "SELECT initial_run_sequence FROM ai_agent_tree_state_v4 "
            "WHERE singleton = 1"
        )
        if row is None:
            maximum = await self._db.fetch_one(
                "SELECT COALESCE(MAX(CASE WHEN id GLOB 'agent-run-[0-9]*' "
                "THEN CAST(substr(id, 11) AS INTEGER) END), 0) AS value "
                "FROM ai_agent_runs"
            )
            seed = int(maximum["value"] or 0)
            await self._db.execute(
                "INSERT INTO ai_agent_tree_state_v4 "
                "(singleton, initial_run_sequence) VALUES (1, ?)",
                [seed],
            )
            return seed
        return int(row["initial_run_sequence"])

    async def _call(self, method, *args, **kwargs):
        try:
            async with self._db.transaction(cancellation_linearizable=True):
                await self._db.execute(
                    "CREATE TABLE IF NOT EXISTS ai_agent_tree_commands_v4 ("
                    "sequence INTEGER PRIMARY KEY AUTOINCREMENT, schema TEXT NOT NULL, "
                    "clock_ms INTEGER NOT NULL, method TEXT NOT NULL, arguments TEXT NOT NULL)"
                )
                if self._revision == 0:
                    self._reset(await self._load_initial_run_sequence())
                rows = await self._db.fetch_all(
                    "SELECT * FROM ai_agent_tree_commands_v4 WHERE sequence > ? ORDER BY sequence",
                    [self._revision],
                )
                for row in rows:
                    if row["schema"] != _SCHEMA or row["method"] not in _MUTATIONS:
                        raise ContractViolationError("Unsupported tree journal", code="run_tree_journal_invalid")
                    self._now = row["clock_ms"]
                    call = _decode(json.loads(row["arguments"]))
                    await getattr(self._tree, row["method"])(*call["args"], **call["kwargs"])
                    self._revision = row["sequence"]
                self._now = self._clock()
                arguments = json.dumps(_encode({"args": args, "kwargs": kwargs}), ensure_ascii=False, allow_nan=False)
                result = await getattr(self._tree, method)(*args, **kwargs)
                if method in _MUTATIONS:
                    await self._db.execute(
                        "INSERT INTO ai_agent_tree_commands_v4(schema, clock_ms, method, arguments) VALUES (?, ?, ?, ?)",
                        [_SCHEMA, self._now, method, arguments],
                    )
                    row = await self._db.fetch_one("SELECT MAX(sequence) AS revision FROM ai_agent_tree_commands_v4")
                    self._revision = row["revision"]
                return result
        except BaseException:
            # A rollback/cancellation must never leave uncommitted cached state.
            self._reset()
            raise

    async def begin_root(self, *args, **kwargs):
        return await self._call("begin_root", *args, **kwargs)

    async def spawn_agents(self, *args, **kwargs):
        return await self._call("spawn_agents", *args, **kwargs)

    async def continue_agent(self, *args, **kwargs):
        return await self._call("continue_agent", *args, **kwargs)

    async def claim_run(self, *args, **kwargs):
        return await self._call("claim_run", *args, **kwargs)

    async def renew_run_lease(self, *args, **kwargs):
        return await self._call("renew_run_lease", *args, **kwargs)

    async def require_run_claim(self, *args, **kwargs):
        return await self._call("require_run_claim", *args, **kwargs)

    async def mark_waiting(self, *args, **kwargs):
        return await self._call("mark_waiting", *args, **kwargs)

    async def release_waiting(self, *args, **kwargs):
        return await self._call("release_waiting", *args, **kwargs)

    async def suspend_run(self, *args, **kwargs):
        return await self._call("suspend_run", *args, **kwargs)

    async def complete_run(self, *args, **kwargs):
        return await self._call("complete_run", *args, **kwargs)

    async def fail_run(self, *args, **kwargs):
        return await self._call("fail_run", *args, **kwargs)

    async def cancel_subtree(self, *args, **kwargs):
        return await self._call("cancel_subtree", *args, **kwargs)

    async def aggregate_runs(self, *args, **kwargs):
        return await self._call("aggregate_runs", *args, **kwargs)

    async def close_agent(self, *args, **kwargs):
        return await self._call("close_agent", *args, **kwargs)

    async def get_agent(self, *args, **kwargs):
        return await self._call("get_agent", *args, **kwargs)

    async def get_run(self, *args, **kwargs):
        return await self._call("get_run", *args, **kwargs)

    async def get_checkpoint(self, *args, **kwargs):
        return await self._call("get_checkpoint", *args, **kwargs)

    async def list_runnable(self, *args, **kwargs):
        return await self._call("list_runnable", *args, **kwargs)

    async def list_descendants(self, *args, **kwargs):
        return await self._call("list_descendants", *args, **kwargs)

    async def list_agent_descendants(self, *args, **kwargs):
        return await self._call("list_agent_descendants", *args, **kwargs)
