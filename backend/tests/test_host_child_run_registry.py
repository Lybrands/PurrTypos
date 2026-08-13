from __future__ import annotations

import asyncio
import importlib
import json
from pathlib import Path

import pytest
import pytest_asyncio

from database.connection import DatabaseConnection
from purra.errors import ContractViolationError


def _registry_type():
    try:
        module = importlib.import_module(
            "infrastructure.persistence.sqlite_host_child_run_registry"
        )
    except ModuleNotFoundError as error:
        pytest.fail(f"durable host-child registry is missing: {error}")
    return module.SqliteHostChildRunRegistry


@pytest_asyncio.fixture
async def registry_db(tmp_path: Path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    try:
        yield db
    finally:
        await db.close()


def _contract() -> dict[str, object]:
    return {
        "sessionId": 7,
        "turnId": "turn-7",
        "bindingNamespace": "screenplay.agent.task",
        "bindingAggregateId": "project-7",
        "bindingCommandId": "task-7:unit-7",
        "parentRunId": "root-7",
        "rootRunId": "root-7",
        "delegationId": None,
        "agentRole": "screenplay-part",
        "depth": 1,
        "agentProfile": "screenplay-agent",
        "domainNamespace": "screenplay.agent",
        "responseMode": "validated_result",
    }


async def _insert_attempt_run(
    db,
    reservation,
    *,
    run_id: str,
    status: str,
    contract: dict[str, object] | None = None,
) -> None:
    persisted = dict(contract or _contract())
    attributes = {
        "agentProfile": persisted["agentProfile"],
        "domainNamespace": persisted["domainNamespace"],
        "hostChild": {
            "protocol": "purra.host-child/v1",
            "identityDigest": reservation.identity_digest,
            "attemptKey": reservation.attempt_key,
            "generation": reservation.generation,
            "responseMode": persisted["responseMode"],
        },
    }
    await db.execute(
        "INSERT INTO ai_agent_runs "
        "(id, session_id, status, mode, prompt, binding_namespace, "
        "binding_aggregate_id, binding_command_id, binding_attributes_json, "
        "parent_run_id, root_run_id, delegation_id, agent_role, run_depth) "
        "VALUES (?, ?, ?, 'agent', '', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            run_id,
            persisted["sessionId"],
            status,
            persisted["bindingNamespace"],
            persisted["bindingAggregateId"],
            persisted["bindingCommandId"],
            json.dumps(attributes, ensure_ascii=False, separators=(",", ":")),
            persisted["parentRunId"],
            persisted["rootRunId"],
            persisted["delegationId"],
            persisted["agentRole"],
            persisted["depth"],
        ],
    )
    await db.execute(
        "INSERT INTO ai_agent_run_events "
        "(run_id, event_type, payload_json, event_id, turn_id, sequence, "
        "source, kind, channel, visibility, occurred_at, emitted_at, "
        "source_event_key) VALUES (?, 'run.lifecycle', '{}', ?, ?, 1, "
        "'runtime', 'run.lifecycle', 'lifecycle', 'public', "
        "'2026-08-14T00:00:00+00:00', '2026-08-14T00:00:00+00:00', ?)",
        [run_id, f"event-{run_id}", persisted["turnId"], f"run:{run_id}:running"],
    )


@pytest.mark.asyncio
async def test_same_host_child_key_has_one_concurrent_reservation(registry_db):
    registry = _registry_type()(registry_db, reservation_ttl_ms=1_000)

    receipts = await asyncio.gather(*(
        registry.reserve(
            host_child_key="project-7/task-7/unit-7/main",
            identity_digest="identity-7",
            contract=_contract(),
            owner_token=f"owner-{index}",
            timestamp_ms=100,
        )
        for index in range(8)
    ))

    assert [item.disposition.value for item in receipts].count("create") == 1
    assert [item.disposition.value for item in receipts].count("wait") == 7
    assert len({item.generation for item in receipts}) == 1
    assert len({item.attempt_key for item in receipts}) == 1


@pytest.mark.asyncio
async def test_same_host_child_key_rejects_identity_or_contract_conflict(
    registry_db,
):
    registry = _registry_type()(registry_db, reservation_ttl_ms=1_000)
    await registry.reserve(
        host_child_key="project-7/task-7/unit-7/main",
        identity_digest="identity-7",
        contract=_contract(),
        owner_token="owner-1",
        timestamp_ms=100,
    )

    with pytest.raises(ContractViolationError, match="identity"):
        await registry.reserve(
            host_child_key="project-7/task-7/unit-7/main",
            identity_digest="other-identity",
            contract=_contract(),
            owner_token="owner-2",
            timestamp_ms=101,
        )
    conflicting = {**_contract(), "rootRunId": "other-root"}
    with pytest.raises(ContractViolationError, match="contract"):
        await registry.reserve(
            host_child_key="project-7/task-7/unit-7/main",
            identity_digest="identity-7",
            contract=conflicting,
            owner_token="owner-2",
            timestamp_ms=101,
        )


@pytest.mark.asyncio
async def test_expired_reservation_without_run_is_reclaimed_same_attempt(
    registry_db,
):
    registry = _registry_type()(registry_db, reservation_ttl_ms=10)
    first = await registry.reserve(
        host_child_key="project-7/task-7/unit-7/main",
        identity_digest="identity-7",
        contract=_contract(),
        owner_token="crashed-owner",
        timestamp_ms=100,
    )

    reclaimed = await _registry_type()(
        registry_db,
        reservation_ttl_ms=10,
    ).reserve(
        host_child_key="project-7/task-7/unit-7/main",
        identity_digest="identity-7",
        contract=_contract(),
        owner_token="new-owner",
        timestamp_ms=111,
    )

    assert reclaimed.disposition.value == "create"
    assert reclaimed.generation == first.generation
    assert reclaimed.attempt_key == first.attempt_key
    assert reclaimed.owner_token == "new-owner"


@pytest.mark.asyncio
async def test_run_created_before_receipt_bind_is_reconciled_without_new_attempt(
    registry_db,
):
    registry = _registry_type()(registry_db, reservation_ttl_ms=1_000)
    initial = await registry.reserve(
        host_child_key="project-7/task-7/unit-7/main",
        identity_digest="identity-7",
        contract=_contract(),
        owner_token="crashed-owner",
        timestamp_ms=100,
    )
    await _insert_attempt_run(
        registry_db,
        initial,
        run_id="child-run-7",
        status="done",
    )

    recovered = await _registry_type()(registry_db).reserve(
        host_child_key=initial.host_child_key,
        identity_digest=initial.identity_digest,
        contract=_contract(),
        owner_token="new-owner",
        timestamp_ms=101,
    )

    assert recovered.disposition.value == "bound"
    assert recovered.run_id == "child-run-7"
    assert recovered.terminal_status == "done"
    assert recovered.generation == initial.generation


@pytest.mark.asyncio
async def test_receipt_bind_exact_replay_is_idempotent(registry_db):
    registry = _registry_type()(registry_db, reservation_ttl_ms=1_000)
    initial = await registry.reserve(
        host_child_key="project-7/task-7/unit-7/main",
        identity_digest="identity-7",
        contract=_contract(),
        owner_token="owner-1",
        timestamp_ms=100,
    )
    await _insert_attempt_run(
        registry_db,
        initial,
        run_id="child-run-bind",
        status="running",
    )

    first = await registry.bind_run(
        initial,
        run_id="child-run-bind",
        owner_token="owner-1",
    )
    replay = await registry.bind_run(
        initial,
        run_id="child-run-bind",
        owner_token="owner-1",
    )

    assert replay == first


@pytest.mark.asyncio
async def test_run_before_bind_contract_mismatch_fails_closed(registry_db):
    registry = _registry_type()(registry_db, reservation_ttl_ms=1_000)
    initial = await registry.reserve(
        host_child_key="project-7/task-7/unit-7/main",
        identity_digest="identity-7",
        contract=_contract(),
        owner_token="crashed-owner",
        timestamp_ms=100,
    )
    conflicting = {**_contract(), "rootRunId": "wrong-root"}
    await _insert_attempt_run(
        registry_db,
        initial,
        run_id="child-run-wrong",
        status="done",
        contract=conflicting,
    )

    with pytest.raises(ContractViolationError, match="persisted Run"):
        await _registry_type()(registry_db).reserve(
            host_child_key=initial.host_child_key,
            identity_digest=initial.identity_digest,
            contract=_contract(),
            owner_token="new-owner",
            timestamp_ms=101,
        )


@pytest.mark.asyncio
async def test_failed_attempt_advances_once_but_canceled_never_retries(
    registry_db,
):
    registry = _registry_type()(registry_db, reservation_ttl_ms=1_000)
    initial = await registry.reserve(
        host_child_key="project-7/task-7/unit-7/main",
        identity_digest="identity-7",
        contract=_contract(),
        owner_token="owner-1",
        timestamp_ms=100,
    )
    await _insert_attempt_run(
        registry_db,
        initial,
        run_id="failed-run",
        status="failed",
    )
    initial = await registry.reserve(
        host_child_key=initial.host_child_key,
        identity_digest=initial.identity_digest,
        contract=_contract(),
        owner_token="observer",
        timestamp_ms=101,
    )

    failed = await registry.advance_failed(
        initial,
        expected_run_id="failed-run",
        owner_token="retry-owner-1",
        timestamp_ms=200,
    )
    contender = await registry.advance_failed(
        initial,
        expected_run_id="failed-run",
        owner_token="retry-owner-2",
        timestamp_ms=200,
    )

    assert failed.disposition.value == "create"
    assert failed.generation == 2
    assert contender.disposition.value == "wait"
    assert contender.generation == 2
    with pytest.raises(ContractViolationError, match="failed Run"):
        await registry.advance_failed(
            failed,
            expected_run_id="canceled-run",
            owner_token="retry-owner-3",
            timestamp_ms=201,
        )


@pytest.mark.asyncio
async def test_deleted_aggregate_receipt_can_recreate_key_without_attempt_collision(
    registry_db,
):
    registry = _registry_type()(registry_db, reservation_ttl_ms=1_000)
    initial = await registry.reserve(
        host_child_key="opaque-project-key",
        identity_digest="identity-7",
        contract=_contract(),
        owner_token="owner-1",
        timestamp_ms=100,
    )
    await _insert_attempt_run(
        registry_db,
        initial,
        run_id="retained-audit-run",
        status="done",
    )
    await registry_db.execute(
        "DELETE FROM ai_agent_host_child_runs WHERE host_child_key = ?",
        [initial.host_child_key],
    )

    recreated = await registry.reserve(
        host_child_key=initial.host_child_key,
        identity_digest="new-identity",
        contract=_contract(),
        owner_token="owner-2",
        timestamp_ms=200,
    )
    await _insert_attempt_run(
        registry_db,
        recreated,
        run_id="new-run-after-aggregate-recreate",
        status="running",
    )

    assert recreated.generation == 1
    assert recreated.attempt_key != initial.attempt_key
