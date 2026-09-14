"""Durable, narrow circuit breaker for model-provider availability."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass

from application.provider_capacity_policy import (
    SETTING_KEY as PROVIDER_CAPACITY_POLICY_SETTING_KEY,
    configured_capacity_limit,
)


_WINDOW_MS = 60_000
_RATE_LIMIT_OPEN_MS = 30_000
_INITIAL_OPEN_MS = 15_000
_MAX_OPEN_MS = 120_000
_PROBE_LEASE_MS = 30_000
_RECOVERY_RAMP_MS = 30_000
_TRIP_FAILURES = 3
_CALL_LEASE_MS = 150_000


@dataclass(frozen=True, slots=True)
class ProviderHealthScope:
    provider: str
    model: str
    endpoint_digest: str

    @property
    def key(self) -> str:
        raw = "\x1f".join((self.provider, self.model, self.endpoint_digest))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @property
    def capacity_key(self) -> str:
        raw = "\x1f".join((self.provider, self.endpoint_digest))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ProviderAdmission:
    allowed: bool
    reason_code: str | None = None


class ProviderHealthRepository:
    """Persist state so a restart does not create a synchronized retry burst."""

    def __init__(self, db) -> None:
        self._db = db

    async def admit(self, scope: ProviderHealthScope, *, now_ms: int | None = None) -> bool:
        now = _now_ms(now_ms)
        async with self._db.transaction():
            return await self._admit_locked(scope, now)

    async def acquire(
        self,
        scope: ProviderHealthScope,
        lease_id: str,
        *,
        now_ms: int | None = None,
    ) -> ProviderAdmission:
        """Atomically reserve one real Provider call for this endpoint.

        The capacity key intentionally omits the model: models served by the
        same endpoint contend for the same upstream connection budget.
        """

        now = _now_ms(now_ms)
        async with self._db.transaction():
            # Do not turn an expired open circuit into half-open until a slot
            # is available for a real probe.  Otherwise unrelated active
            # calls could consume a probe lease without making a request.
            health = await self._load(scope)
            if health is not None and _health_blocks_admission(health, now):
                await self._append_event(
                    scope,
                    event_type="admission_rejected",
                    reason_code="provider_circuit_open",
                    state_before=str(health["state"]),
                    state_after=str(health["state"]),
                    failure_count=int(health.get("failure_count") or 0),
                    open_until_ms=health.get("open_until_ms"),
                )
                return ProviderAdmission(False, "provider_circuit_open")
            if health is None:
                await self._db.execute(
                    "INSERT INTO ai_provider_health (scope_key, provider, model, endpoint_digest, "
                    "state, window_started_at_ms, failure_count) VALUES (?, ?, ?, ?, 'closed', ?, 0)",
                    [scope.key, scope.provider, scope.model, scope.endpoint_digest, now],
                )
                health = {
                    "state": "closed",
                    "failure_count": 0,
                    "open_until_ms": None,
                }
            await self._db.execute(
                "DELETE FROM ai_provider_call_leases WHERE expires_at_ms <= ?",
                [now],
            )
            active = await self._db.fetch_one(
                "SELECT COUNT(*) AS count FROM ai_provider_call_leases "
                "WHERE capacity_key = ? AND expires_at_ms > ?",
                [scope.capacity_key, now],
            )
            capacity_limit = await self._capacity_limit(scope, health, now)
            if int((active or {}).get("count") or 0) >= capacity_limit:
                if health is not None:
                    await self._append_event(
                        scope,
                        event_type="admission_rejected",
                        reason_code="provider_capacity_limited",
                        state_before=str(health["state"]),
                        state_after=str(health["state"]),
                        failure_count=int(health.get("failure_count") or 0),
                        open_until_ms=health.get("open_until_ms"),
                    )
                return ProviderAdmission(False, "provider_capacity_limited")
            if not await self._admit_locked(scope, now):
                return ProviderAdmission(False, "provider_circuit_open")
            await self._db.execute(
                "INSERT INTO ai_provider_call_leases "
                "(lease_id, capacity_key, scope_key, expires_at_ms) VALUES (?, ?, ?, ?)",
                [lease_id, scope.capacity_key, scope.key, now + _CALL_LEASE_MS],
            )
            return ProviderAdmission(True)

    async def release(self, lease_id: str) -> None:
        await self._db.execute(
            "DELETE FROM ai_provider_call_leases WHERE lease_id = ?",
            [lease_id],
        )

    async def record_success(
        self,
        scope: ProviderHealthScope,
        *,
        now_ms: int | None = None,
    ) -> None:
        now = _now_ms(now_ms)
        async with self._db.transaction():
            row = await self._load(scope)
            if row is None:
                await self._db.execute(
                    "INSERT INTO ai_provider_health (scope_key, provider, model, endpoint_digest, "
                    "state, window_started_at_ms, failure_count) VALUES (?, ?, ?, ?, 'closed', ?, 0)",
                    [scope.key, scope.provider, scope.model, scope.endpoint_digest, now],
                )
                return
            recovered = (
                str(row["state"]) != "closed"
                or int(row.get("failure_count") or 0) > 0
            )
            current_ramp_until_ms = int(row.get("ramp_until_ms") or 0)
            ramp_until_ms = (
                now + _RECOVERY_RAMP_MS
                if recovered
                else (
                    current_ramp_until_ms
                    if current_ramp_until_ms > now
                    else None
                )
            )
            await self._db.execute(
                "UPDATE ai_provider_health SET state='closed', failure_count=0, "
                "open_until_ms=NULL, probe_expires_at_ms=NULL, ramp_until_ms=?, "
                "last_failure_code=NULL, "
                "revision=revision+1, update_time=CURRENT_TIMESTAMP WHERE scope_key=?",
                [ramp_until_ms, scope.key],
            )
            if recovered:
                await self._append_event(
                    scope,
                    event_type="provider_recovered",
                    state_before=str(row["state"]),
                    state_after="closed",
                    failure_count=0,
                    ramp_until_ms=ramp_until_ms,
                )

    async def record_failure(
        self,
        scope: ProviderHealthScope,
        code: str,
        *,
        now_ms: int | None = None,
    ) -> None:
        now = _now_ms(now_ms)
        normalized_code = str(code or "model_gateway_error")[:240]
        async with self._db.transaction():
            row = await self._load(scope)
            if row is None:
                failures = 1
                window_started = now
                previous_state = "closed"
            else:
                previous_state = str(row["state"])
                window_started = int(row.get("window_started_at_ms") or now)
                failures = int(row.get("failure_count") or 0) + 1
                if now - window_started > _WINDOW_MS:
                    window_started = now
                    failures = 1
            should_open = (
                normalized_code == "provider_rate_limited"
                or previous_state == "half_open"
                or failures >= _TRIP_FAILURES
            )
            if should_open:
                open_for_ms = _open_duration_ms(
                    normalized_code,
                    failures=failures,
                )
                state = "open"
                open_until_ms = now + open_for_ms
                probe_expires_at_ms = None
            else:
                state = "closed"
                open_until_ms = None
                probe_expires_at_ms = None
            await self._db.execute(
                "INSERT INTO ai_provider_health (scope_key, provider, model, endpoint_digest, state, "
                "window_started_at_ms, failure_count, open_until_ms, probe_expires_at_ms, ramp_until_ms, last_failure_code) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(scope_key) DO UPDATE SET state=excluded.state, "
                "window_started_at_ms=excluded.window_started_at_ms, failure_count=excluded.failure_count, "
                "open_until_ms=excluded.open_until_ms, probe_expires_at_ms=excluded.probe_expires_at_ms, "
                "ramp_until_ms=NULL, "
                "last_failure_code=excluded.last_failure_code, revision=ai_provider_health.revision+1, "
                "update_time=CURRENT_TIMESTAMP",
                [
                    scope.key, scope.provider, scope.model, scope.endpoint_digest,
                    state, window_started, failures, open_until_ms,
                    probe_expires_at_ms, None, normalized_code,
                ],
            )
            await self._append_event(
                scope,
                event_type=("circuit_opened" if state == "open" else "failure_observed"),
                reason_code=normalized_code,
                state_before=previous_state,
                state_after=state,
                failure_count=failures,
                open_until_ms=open_until_ms,
            )

    async def _load(self, scope: ProviderHealthScope):
        return await self._db.fetch_one(
            "SELECT state, window_started_at_ms, failure_count, open_until_ms, "
            "probe_expires_at_ms, ramp_until_ms FROM ai_provider_health WHERE scope_key=?",
            [scope.key],
        )

    async def _capacity_limit(
        self,
        scope: ProviderHealthScope,
        health,
        now: int,
    ) -> int:
        """Return the endpoint-wide cap, including a persisted recovery ramp."""

        state = str((health or {}).get("state") or "")
        if state in {"open", "half_open"}:
            # An expired open circuit must receive an empty endpoint slot for
            # its sole probe; it cannot join an already busy endpoint.
            return 1
        ramp = await self._db.fetch_one(
            "SELECT MAX(CASE WHEN COALESCE(ramp_until_ms, 0) > ? "
            "THEN 1 ELSE 0 END) AS active_ramp FROM ai_provider_health "
            "WHERE provider = ? AND endpoint_digest = ?",
            [now, scope.provider, scope.endpoint_digest],
        )
        if int((ramp or {}).get("active_ramp") or 0):
            return 1
        return await self._configured_capacity_limit(scope)

    async def _configured_capacity_limit(self, scope: ProviderHealthScope) -> int:
        row = await self._db.fetch_one(
            "SELECT value FROM settings WHERE key = ?",
            [PROVIDER_CAPACITY_POLICY_SETTING_KEY],
        )
        if not row:
            return configured_capacity_limit(
                None,
                provider=scope.provider,
                endpoint_digest=scope.endpoint_digest,
            )
        try:
            raw_policy = json.loads(str(row.get("value") or "[]"))
        except (TypeError, ValueError, json.JSONDecodeError):
            raw_policy = None
        return configured_capacity_limit(
            raw_policy,
            provider=scope.provider,
            endpoint_digest=scope.endpoint_digest,
        )

    async def _admit_locked(self, scope: ProviderHealthScope, now: int) -> bool:
        row = await self._load(scope)
        if row is None:
            await self._db.execute(
                "INSERT INTO ai_provider_health (scope_key, provider, model, endpoint_digest, "
                "state, window_started_at_ms, failure_count) VALUES (?, ?, ?, ?, 'closed', ?, 0)",
                [scope.key, scope.provider, scope.model, scope.endpoint_digest, now],
            )
            return True
        state = str(row["state"])
        if state == "closed":
            return True
        if state == "open":
            if int(row.get("open_until_ms") or 0) > now:
                return False
            await self._db.execute(
                "UPDATE ai_provider_health SET state='half_open', probe_expires_at_ms=?, "
                "revision=revision+1, update_time=CURRENT_TIMESTAMP WHERE scope_key=?",
                [now + _PROBE_LEASE_MS, scope.key],
            )
            await self._append_event(
                scope,
                event_type="provider_probe_granted",
                state_before="open",
                state_after="half_open",
                failure_count=int(row.get("failure_count") or 0),
            )
            return True
        if int(row.get("probe_expires_at_ms") or 0) > now:
            return False
        await self._db.execute(
            "UPDATE ai_provider_health SET probe_expires_at_ms=?, revision=revision+1, "
            "update_time=CURRENT_TIMESTAMP WHERE scope_key=?",
            [now + _PROBE_LEASE_MS, scope.key],
        )
        await self._append_event(
            scope,
            event_type="provider_probe_granted",
            state_before="half_open",
            state_after="half_open",
            failure_count=int(row.get("failure_count") or 0),
        )
        return True

    async def _append_event(
        self,
        scope: ProviderHealthScope,
        *,
        event_type: str,
        reason_code: str | None = None,
        state_before: str | None = None,
        state_after: str | None = None,
        failure_count: int = 0,
        open_until_ms: int | None = None,
        ramp_until_ms: int | None = None,
    ) -> None:
        await self._db.execute(
            "INSERT INTO ai_provider_health_events "
            "(scope_key, event_type, reason_code, state_before, state_after, "
            "failure_count, open_until_ms, ramp_until_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                scope.key,
                str(event_type)[:80],
                str(reason_code)[:240] if reason_code else None,
                str(state_before)[:32] if state_before else None,
                str(state_after)[:32] if state_after else None,
                max(0, int(failure_count)),
                int(open_until_ms) if open_until_ms is not None else None,
                int(ramp_until_ms) if ramp_until_ms is not None else None,
            ],
        )


def _open_duration_ms(code: str, *, failures: int) -> int:
    if code == "provider_rate_limited":
        return _RATE_LIMIT_OPEN_MS
    exponent = min(max(0, failures - _TRIP_FAILURES), 3)
    return min(_MAX_OPEN_MS, _INITIAL_OPEN_MS * (2 ** exponent))


def _health_blocks_admission(row, now: int) -> bool:
    state = str(row.get("state") or "")
    if state == "open":
        return int(row.get("open_until_ms") or 0) > now
    return state == "half_open" and int(row.get("probe_expires_at_ms") or 0) > now


def _now_ms(value: int | None) -> int:
    return int(time.time() * 1000) if value is None else int(value)


__all__ = ["ProviderAdmission", "ProviderHealthRepository", "ProviderHealthScope"]
