from __future__ import annotations

import json

import pytest

from application.provider_capacity_policy import (
    configured_capacity_limit,
    normalize_provider_capacity_policies,
)
from application.run_provenance import digest_model_endpoint
from database.connection import DatabaseConnection
from infrastructure.persistence.provider_health_repository import (
    ProviderHealthRepository,
    ProviderHealthScope,
)
from infrastructure.persistence.stability_query import (
    get_novel_analysis_reliability_snapshot,
)
from routers.settings import _save_provider_capacity_policies


def test_capacity_policies_normalize_endpoints_and_reject_ambiguous_duplicates():
    policies = normalize_provider_capacity_policies([
        {
            "provider": "OpenAI",
            "endpoint": " https://example.test/v1/// ",
            "maxConcurrentCalls": 3,
        },
    ])

    assert policies == [{
        "provider": "openai",
        "endpoint": "https://example.test/v1",
        "maxConcurrentCalls": 3,
    }]
    with pytest.raises(ValueError, match="cannot repeat an endpoint"):
        normalize_provider_capacity_policies([
            *policies,
            {
                "provider": "openai",
                "endpoint": "https://example.test/v1/",
                "maxConcurrentCalls": 1,
            },
        ])


@pytest.mark.asyncio
async def test_capacity_is_shared_across_models_at_one_endpoint(tmp_path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    try:
        endpoint = "https://example.test/v1"
        await _save_provider_capacity_policies(db, [{
            "provider": "openai",
            "endpoint": endpoint,
            "maxConcurrentCalls": 1,
        }])
        repository = ProviderHealthRepository(db)
        first_scope = ProviderHealthScope(
            provider="openai",
            model="model-a",
            endpoint_digest=digest_model_endpoint(endpoint),
        )
        second_scope = ProviderHealthScope(
            provider="openai",
            model="model-b",
            endpoint_digest=digest_model_endpoint(endpoint),
        )

        assert (await repository.acquire(first_scope, "lease-a", now_ms=1_000)).allowed
        blocked = await repository.acquire(second_scope, "lease-b", now_ms=1_000)
        assert blocked.allowed is False
        assert blocked.reason_code == "provider_capacity_limited"

        await repository.release("lease-a")
        assert (await repository.acquire(second_scope, "lease-b", now_ms=1_001)).allowed
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_corrupt_policy_falls_back_to_the_conservative_default_capacity(tmp_path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    try:
        endpoint = "https://example.test/v1"
        await db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?)",
            ["ai_provider_capacity_policies", "not-json"],
        )
        repository = ProviderHealthRepository(db)
        scopes = [
            ProviderHealthScope(
                provider="openai",
                model=f"model-{index}",
                endpoint_digest=digest_model_endpoint(endpoint),
            )
            for index in range(3)
        ]

        assert (await repository.acquire(scopes[0], "lease-a", now_ms=1_000)).allowed
        assert (await repository.acquire(scopes[1], "lease-b", now_ms=1_000)).allowed
        blocked = await repository.acquire(scopes[2], "lease-c", now_ms=1_000)
        assert blocked.reason_code == "provider_capacity_limited"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_policy_write_records_only_observable_capacity_changes(tmp_path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    try:
        first = [{
            "provider": "openai",
            "endpoint": "https://example.test/v1",
            "maxConcurrentCalls": 3,
        }]
        second = [{**first[0], "maxConcurrentCalls": 1}]
        await _save_provider_capacity_policies(db, first)
        await _save_provider_capacity_policies(db, first)
        await _save_provider_capacity_policies(db, second)
        await _save_provider_capacity_policies(db, [])

        setting = await db.fetch_one(
            "SELECT value FROM settings WHERE key = ?",
            ["ai_provider_capacity_policies"],
        )
        assert json.loads(setting["value"]) == []
        events = await db.fetch_all(
            "SELECT event_type, previous_capacity, next_capacity "
            "FROM ai_provider_capacity_policy_events ORDER BY id ASC",
        )
        assert events == [
            {
                "event_type": "capacity_policy_set",
                "previous_capacity": None,
                "next_capacity": 3,
            },
            {
                "event_type": "capacity_policy_changed",
                "previous_capacity": 3,
                "next_capacity": 1,
            },
            {
                "event_type": "capacity_policy_reset",
                "previous_capacity": 1,
                "next_capacity": None,
            },
        ]
    finally:
        await db.close()


def test_invalid_values_do_not_produce_an_unsafe_capacity():
    assert configured_capacity_limit(
        [{"provider": "openai", "endpoint": "https://example.test", "maxConcurrentCalls": 99}],
        provider="openai",
        endpoint_digest=digest_model_endpoint("https://example.test"),
    ) == 2


@pytest.mark.asyncio
async def test_reliability_projection_exposes_capacity_without_exposing_endpoint(tmp_path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    try:
        endpoint = "https://private-provider.example.test/v1"
        session_id = await db.execute_and_get_id(
            "INSERT INTO ai_sessions (title) VALUES ('capacity evidence')",
        )
        await db.execute(
            "INSERT INTO ai_agent_runs (id, session_id, status) VALUES (?, ?, 'done')",
            ["capacity-run", session_id],
        )
        await db.execute(
            "INSERT INTO ai_agent_long_tasks "
            "(id, namespace, kind, owner_id, created_by_run_id, status, total_units, completed_units, failed_units, max_parallelism, metadata_json) "
            "VALUES (?, 'purrtypos.novel_analysis', 'novel_source_analysis', ?, ?, 'paused', 0, 0, 0, 1, ?)",
            [
                "capacity-task",
                "revision-1",
                "capacity-run",
                json.dumps({
                    "runtimeBinding": {
                        "provider": "openai",
                        "model": "test-model",
                        "endpoint": endpoint,
                    },
                }),
            ],
        )
        await _save_provider_capacity_policies(db, [{
            "provider": "openai",
            "endpoint": endpoint,
            "maxConcurrentCalls": 1,
        }])
        scope = ProviderHealthScope(
            provider="openai",
            model="test-model",
            endpoint_digest=digest_model_endpoint(endpoint),
        )
        health = ProviderHealthRepository(db)
        assert (await health.acquire(scope, "evidence-lease", now_ms=1_000)).allowed
        await health.release("evidence-lease")

        snapshot = await get_novel_analysis_reliability_snapshot(
            db,
            session_id=session_id,
        )

        assert snapshot["providers"]["current"] == [{
            "provider": "openai",
            "model": "test-model",
            "state": "closed",
            "failureCount": 0,
            "lastFailureCode": None,
            "openUntilMs": None,
            "rampUntilMs": None,
            "configuredCapacity": 1,
        }]
        assert snapshot["providers"]["capacityPolicyEvents"] == [{
            "provider": "openai",
            "eventType": "capacity_policy_set",
            "count": 1,
        }]
        assert endpoint not in str(snapshot)
    finally:
        await db.close()
