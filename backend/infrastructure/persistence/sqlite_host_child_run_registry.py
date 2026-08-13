"""SQLite CAS registry for durable host-orchestrated Child Run identity."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from uuid import uuid4

from application.host_child_runs import (
    HOST_CHILD_BINDING_PROTOCOL,
    HostChildReservation,
    HostChildReservationDisposition,
)
from purra.errors import ContractViolationError
from purra.contracts import AgentRunResult, RunStatus
from purra.json_values import thaw_json_mapping
from purra.normalization import positive_int, required_text


class SqliteHostChildRunRegistry:
    def __init__(self, db, *, reservation_ttl_ms: int = 30_000) -> None:
        self._db = db
        self._ttl_ms = positive_int(
            reservation_ttl_ms,
            "host child reservation ttl",
        )

    async def reserve(
        self,
        *,
        host_child_key: str,
        identity_digest: str,
        contract: Mapping[str, object],
        owner_token: str,
        timestamp_ms: int,
    ) -> HostChildReservation:
        key = required_text(host_child_key, "host child key")
        digest = required_text(identity_digest, "host child identity digest")
        owner = required_text(owner_token, "host child reservation owner")
        now = int(timestamp_ms)
        normalized_contract = thaw_json_mapping(contract)
        contract_json = _dump(normalized_contract)
        async with self._db.transaction(cancellation_linearizable=True):
            row = await self._db.fetch_one(
                "SELECT * FROM ai_agent_host_child_runs "
                "WHERE host_child_key = ?",
                [key],
            )
            if row is None:
                generation = 1
                attempt_key = _new_attempt_key(key, generation)
                await self._db.execute(
                    "INSERT INTO ai_agent_host_child_runs "
                    "(host_child_key, identity_digest, contract_json, "
                    "generation, attempt_key, reservation_owner, "
                    "reservation_expires_at_ms) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [
                        key,
                        digest,
                        contract_json,
                        generation,
                        attempt_key,
                        owner,
                        now + self._ttl_ms,
                    ],
                )
                row = await self._require(key)
                return _receipt(
                    row,
                    HostChildReservationDisposition.CREATE,
                )
            _validate_identity(row, digest, contract_json)
            row = await self._reconcile(row)
            if row.get("run_id"):
                return _receipt(
                    row,
                    HostChildReservationDisposition.BOUND,
                )
            expires_at = int(row.get("reservation_expires_at_ms") or 0)
            if expires_at <= now:
                await self._db.execute(
                    "UPDATE ai_agent_host_child_runs SET "
                    "reservation_owner = ?, reservation_expires_at_ms = ?, "
                    "update_time = CURRENT_TIMESTAMP "
                    "WHERE host_child_key = ? AND run_id IS NULL "
                    "AND COALESCE(reservation_expires_at_ms, 0) <= ?",
                    [owner, now + self._ttl_ms, key, now],
                )
                row = await self._require(key)
                disposition = (
                    HostChildReservationDisposition.CREATE
                    if row.get("reservation_owner") == owner
                    else HostChildReservationDisposition.WAIT
                )
                return _receipt(row, disposition)
            return _receipt(row, HostChildReservationDisposition.WAIT)

    async def advance_failed(
        self,
        reservation: HostChildReservation,
        *,
        expected_run_id: str,
        owner_token: str,
        timestamp_ms: int,
    ) -> HostChildReservation:
        expected = required_text(expected_run_id, "failed host child run id")
        owner = required_text(owner_token, "host child reservation owner")
        now = int(timestamp_ms)
        async with self._db.transaction(cancellation_linearizable=True):
            row = await self._require(reservation.host_child_key)
            _validate_identity(
                row,
                reservation.identity_digest,
                _dump(thaw_json_mapping(reservation.contract)),
            )
            if (
                int(row["generation"]) != reservation.generation
                or str(row.get("run_id") or "") != expected
                or str(row.get("terminal_status") or "") != "failed"
            ):
                current = await self._reconcile(row)
                if int(current["generation"]) > reservation.generation:
                    return _receipt(
                        current,
                        HostChildReservationDisposition.WAIT,
                    )
                raise ContractViolationError(
                    "host child retry requires the expected failed Run"
                )
            generation = reservation.generation + 1
            await self._db.execute(
                "UPDATE ai_agent_host_child_runs SET generation = ?, "
                "attempt_key = ?, reservation_owner = ?, "
                "reservation_expires_at_ms = ?, run_id = NULL, "
                "terminal_status = NULL, update_time = CURRENT_TIMESTAMP "
                "WHERE host_child_key = ? AND generation = ? "
                "AND run_id = ? AND terminal_status = 'failed'",
                [
                    generation,
                    _new_attempt_key(reservation.host_child_key, generation),
                    owner,
                    now + self._ttl_ms,
                    reservation.host_child_key,
                    reservation.generation,
                    expected,
                ],
            )
            row = await self._require(reservation.host_child_key)
            disposition = (
                HostChildReservationDisposition.CREATE
                if row.get("reservation_owner") == owner
                else HostChildReservationDisposition.WAIT
            )
            return _receipt(row, disposition)

    async def bind_run(
        self,
        reservation: HostChildReservation,
        *,
        run_id: str,
        owner_token: str,
    ) -> HostChildReservation:
        normalized_run = required_text(run_id, "host child run id")
        owner = required_text(owner_token, "host child reservation owner")
        async with self._db.transaction(cancellation_linearizable=True):
            row = await self._require(reservation.host_child_key)
            _validate_identity(
                row,
                reservation.identity_digest,
                _dump(thaw_json_mapping(reservation.contract)),
            )
            current_run = str(row.get("run_id") or "")
            if (
                current_run == normalized_run
                and int(row["generation"]) == reservation.generation
                and str(row["attempt_key"]) == reservation.attempt_key
            ):
                run = await self._db.fetch_one(
                    "SELECT * FROM ai_agent_runs WHERE id = ?",
                    [normalized_run],
                )
                if run is None:
                    raise ContractViolationError(
                        "host child receipt references a missing persisted Run"
                    )
                await self._validate_persisted_run(row, run)
                return _receipt(
                    await self._reconcile(row),
                    HostChildReservationDisposition.BOUND,
                )
            if (
                int(row["generation"]) != reservation.generation
                or str(row["attempt_key"]) != reservation.attempt_key
                or str(row.get("reservation_owner") or "") != owner
            ):
                raise ContractViolationError(
                    "host child Run cannot bind a stale reservation"
                )
            if current_run and current_run != normalized_run:
                raise ContractViolationError(
                    "host child receipt is already bound to another Run"
                )
            run = await self._db.fetch_one(
                "SELECT * FROM ai_agent_runs WHERE id = ?",
                [normalized_run],
            )
            if run is None:
                raise ContractViolationError("host child Run does not exist")
            await self._validate_persisted_run(row, run)
            await self._db.execute(
                "UPDATE ai_agent_host_child_runs SET run_id = ?, "
                "terminal_status = CASE WHEN ? = 'running' THEN NULL ELSE ? END, "
                "reservation_owner = NULL, reservation_expires_at_ms = NULL, "
                "update_time = CURRENT_TIMESTAMP WHERE host_child_key = ? "
                "AND generation = ? AND attempt_key = ? "
                "AND (run_id IS NULL OR run_id = ?)",
                [
                    normalized_run,
                    run["status"],
                    run["status"],
                    reservation.host_child_key,
                    reservation.generation,
                    reservation.attempt_key,
                    normalized_run,
                ],
            )
            return _receipt(
                await self._require(reservation.host_child_key),
                HostChildReservationDisposition.BOUND,
            )

    async def refresh(
        self,
        reservation: HostChildReservation,
    ) -> HostChildReservation:
        async with self._db.transaction(cancellation_linearizable=True):
            row = await self._require(reservation.host_child_key)
            _validate_identity(
                row,
                reservation.identity_digest,
                _dump(thaw_json_mapping(reservation.contract)),
            )
            row = await self._reconcile(row)
            return _receipt(
                row,
                (
                    HostChildReservationDisposition.BOUND
                    if row.get("run_id")
                    else HostChildReservationDisposition.WAIT
                ),
            )

    async def load_result(
        self,
        reservation: HostChildReservation,
    ) -> AgentRunResult:
        async with self._db.transaction(cancellation_linearizable=True):
            row = await self._require(reservation.host_child_key)
            _validate_identity(
                row,
                reservation.identity_digest,
                _dump(thaw_json_mapping(reservation.contract)),
            )
            row = await self._reconcile(row)
            run_id = str(row.get("run_id") or "")
            status = str(row.get("terminal_status") or "")
            if not run_id or status not in {
                RunStatus.DONE.value,
                RunStatus.FAILED.value,
                RunStatus.BLOCKED.value,
                RunStatus.CANCELED.value,
            }:
                raise ContractViolationError(
                    "host child result requires a terminal persisted Run"
                )
            run = await self._db.fetch_one(
                "SELECT final_response, model_name FROM ai_agent_runs "
                "WHERE id = ?",
                [run_id],
            )
            if run is None:
                raise ContractViolationError("host child persisted Run is missing")
            error = (
                str(run.get("final_response") or "") or status
                if status == RunStatus.FAILED.value
                else None
            )
            return AgentRunResult(
                run_id=run_id,
                status=RunStatus(status),
                final_response=(
                    str(run.get("final_response") or "")
                    if status == RunStatus.DONE.value
                    else ""
                ),
                error=error,
                model=str(run.get("model_name") or "") or None,
            )

    async def _reconcile(self, row: Mapping[str, object]):
        run = (
            await self._db.fetch_one(
                "SELECT * FROM ai_agent_runs WHERE id = ?",
                [row["run_id"]],
            )
            if row.get("run_id")
            else await self._db.fetch_one(
                "SELECT * FROM ai_agent_runs WHERE "
                "json_extract(binding_attributes_json, "
                "'$.hostChild.attemptKey') = ?",
                [row["attempt_key"]],
            )
        )
        if run is None:
            if row.get("run_id"):
                raise ContractViolationError(
                    "host child receipt references a missing persisted Run"
                )
            return row
        await self._validate_persisted_run(row, run)
        if row.get("run_id"):
            await self._db.execute(
                "UPDATE ai_agent_host_child_runs SET terminal_status = "
                "CASE WHEN ? = 'running' THEN NULL ELSE ? END, "
                "update_time = CURRENT_TIMESTAMP WHERE host_child_key = ? "
                "AND run_id = ? AND attempt_key = ?",
                [
                    run["status"],
                    run["status"],
                    row["host_child_key"],
                    run["id"],
                    row["attempt_key"],
                ],
            )
        else:
            await self._db.execute(
                "UPDATE ai_agent_host_child_runs SET run_id = ?, "
                "terminal_status = CASE WHEN ? = 'running' THEN NULL ELSE ? END, "
                "reservation_owner = NULL, reservation_expires_at_ms = NULL, "
                "update_time = CURRENT_TIMESTAMP WHERE host_child_key = ? "
                "AND run_id IS NULL AND attempt_key = ?",
                [
                    run["id"],
                    run["status"],
                    run["status"],
                    row["host_child_key"],
                    row["attempt_key"],
                ],
            )
        return await self._require(str(row["host_child_key"]))

    async def _validate_persisted_run(self, receipt, run) -> None:
        contract = json.loads(str(receipt["contract_json"]))
        attributes = _mapping(run.get("binding_attributes_json"))
        host_child = attributes.get("hostChild")
        if not isinstance(host_child, Mapping):
            raise ContractViolationError(
                "host child persisted Run has no identity binding"
            )
        expected_run = {
            "session_id": contract.get("sessionId"),
            "binding_namespace": contract.get("bindingNamespace"),
            "binding_aggregate_id": contract.get("bindingAggregateId"),
            "binding_command_id": contract.get("bindingCommandId"),
            "parent_run_id": contract.get("parentRunId"),
            "root_run_id": contract.get("rootRunId"),
            "delegation_id": contract.get("delegationId"),
            "agent_role": contract.get("agentRole"),
            "run_depth": contract.get("depth"),
        }
        if {name: run.get(name) for name in expected_run} != expected_run:
            raise ContractViolationError(
                "host child persisted Run conflicts with its contract"
            )
        expected_attributes = {
            "agentProfile": contract.get("agentProfile"),
            "domainNamespace": contract.get("domainNamespace"),
        }
        if {
            name: attributes.get(name) for name in expected_attributes
        } != expected_attributes:
            raise ContractViolationError(
                "host child persisted Run profile conflicts with its contract"
            )
        expected_identity = {
            "protocol": HOST_CHILD_BINDING_PROTOCOL,
            "identityDigest": receipt["identity_digest"],
            "attemptKey": receipt["attempt_key"],
            "generation": receipt["generation"],
            "responseMode": contract.get("responseMode"),
        }
        if {name: host_child.get(name) for name in expected_identity} != (
            expected_identity
        ):
            raise ContractViolationError(
                "host child persisted Run identity conflicts with its receipt"
            )
        started = await self._db.fetch_all(
            "SELECT turn_id, source, kind, channel, visibility, "
            "output_stream_id, invocation_id FROM ai_agent_run_events "
            "WHERE source_event_key = ?",
            [f"run:{run['id']}:running"],
        )
        expected_started = {
            "turn_id": contract.get("turnId"),
            "source": "runtime",
            "kind": "run.lifecycle",
            "channel": "lifecycle",
            "visibility": "public",
            "output_stream_id": None,
            "invocation_id": None,
        }
        if (
            len(started) != 1
            or {name: started[0].get(name) for name in expected_started}
            != expected_started
        ):
            raise ContractViolationError(
                "host child persisted Run turn conflicts with its contract"
            )

    async def _require(self, key: str):
        row = await self._db.fetch_one(
            "SELECT * FROM ai_agent_host_child_runs WHERE host_child_key = ?",
            [key],
        )
        if row is None:
            raise ContractViolationError("host child reservation does not exist")
        return row


def _validate_identity(row, identity_digest: str, contract_json: str) -> None:
    if str(row.get("identity_digest") or "") != identity_digest:
        raise ContractViolationError("host child key identity conflicts")
    if str(row.get("contract_json") or "") != contract_json:
        raise ContractViolationError("host child key contract conflicts")


def _receipt(row, disposition) -> HostChildReservation:
    return HostChildReservation(
        host_child_key=str(row["host_child_key"]),
        identity_digest=str(row["identity_digest"]),
        contract=json.loads(str(row["contract_json"])),
        generation=int(row["generation"]),
        attempt_key=str(row["attempt_key"]),
        owner_token=str(row.get("reservation_owner") or "") or None,
        run_id=str(row.get("run_id") or "") or None,
        terminal_status=str(row.get("terminal_status") or "") or None,
        disposition=disposition,
    )


def _new_attempt_key(host_child_key: str, generation: int) -> str:
    # A receipt keeps this value stable across reserve/bind crash recovery.
    # A fresh aggregate incarnation receives a new nonce so retained audit Runs
    # cannot occupy its partial-unique attempt identity.
    value = (
        f"{host_child_key}\0{generation}\0{uuid4().hex}"
    ).encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def _dump(value: Mapping[str, object]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _mapping(value: object) -> dict[str, object]:
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


__all__ = ["SqliteHostChildRunRegistry"]
