from __future__ import annotations

import aiosqlite
import pytest
import pytest_asyncio

from agents.shared.implementation import (
    AGENT_IMPLEMENTATION_ATTRIBUTE,
    AgentKind,
    LEGACY_IMPLEMENTATION_ID,
    legacy_implementation,
    replacement_implementation,
)
from agents.shared.implementation_projector import AgentImplementationBeginProjector
from agents.shared.implementation_store import (
    AgentImplementationConflictError,
    SqliteAgentImplementationStore,
)
from database.connection import DatabaseConnection
from infrastructure.persistence.sqlite_agent_output_repository import (
    SqliteAgentOutputRepository,
)
from infrastructure.persistence.sqlite_run_repository import SqliteRunRepository
from infrastructure.persistence.run_store import create_run
from purra.contracts import RunBinding, RunCreateParams, RunStatus
from purra.events import AgentEvent, CoreEventType


@pytest_asyncio.fixture
async def temp_db(tmp_path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    try:
        yield db
    finally:
        await db.close()


async def _run(db, run_id: str = "run_implementation_test") -> str:
    return await create_run(
        db,
        run_id=run_id,
        session_id=None,
        prompt="test",
        mode="agent",
    )


@pytest.mark.asyncio
async def test_unversioned_historical_run_resolves_only_as_legacy(temp_db) -> None:
    run_id = await _run(temp_db)
    store = SqliteAgentImplementationStore(temp_db)

    assert await store.load(run_id) is None
    resolved = await store.resolve(
        run_id,
        expected_agent_kind=AgentKind.WRITING,
    )

    assert resolved.agent_kind is AgentKind.WRITING
    assert resolved.implementation_id == LEGACY_IMPLEMENTATION_ID


@pytest.mark.asyncio
async def test_replacement_identity_is_persisted_and_idempotent(temp_db) -> None:
    run_id = await _run(temp_db)
    store = SqliteAgentImplementationStore(temp_db)
    identity = replacement_implementation(
        AgentKind.NOVEL_ANALYSIS,
        recipe_version=1,
    )

    assert await store.bind(run_id, identity) == identity
    assert await store.bind(run_id, identity) == identity
    assert await store.load(run_id) == identity
    assert await store.resolve(
        run_id,
        expected_agent_kind=AgentKind.NOVEL_ANALYSIS,
    ) == identity


@pytest.mark.asyncio
async def test_run_cannot_be_rebound_to_another_implementation(temp_db) -> None:
    run_id = await _run(temp_db)
    store = SqliteAgentImplementationStore(temp_db)
    identity = replacement_implementation(AgentKind.WRITING)
    await store.bind(run_id, identity)

    with pytest.raises(AgentImplementationConflictError, match="conflicts"):
        await store.bind(
            run_id,
            replacement_implementation(AgentKind.SCREENPLAY),
        )

    with pytest.raises(aiosqlite.IntegrityError, match="immutable"):
        await temp_db.execute(
            "UPDATE ai_agent_runs SET implementation_version = 2 WHERE id = ?",
            [run_id],
        )


@pytest.mark.asyncio
async def test_partial_implementation_identity_fails_closed(temp_db) -> None:
    run_id = await _run(temp_db)
    with pytest.raises(aiosqlite.IntegrityError, match="immutable"):
        await temp_db.execute(
            "UPDATE ai_agent_runs SET implementation_id = 'purra-native' "
            "WHERE id = ?",
            [run_id],
        )

    with pytest.raises(aiosqlite.IntegrityError, match="immutable"):
        await temp_db.execute(
            """
            UPDATE ai_agent_runs
            SET implementation_id = 'purra-native',
                implementation_version = 1,
                tool_contract_version = 1,
                recipe_version = 1,
                artifact_schema_version = 1
            WHERE id = ?
            """,
            [run_id],
        )


@pytest.mark.asyncio
async def test_unknown_run_cannot_be_classified_as_legacy(temp_db) -> None:
    store = SqliteAgentImplementationStore(temp_db)

    with pytest.raises(LookupError, match="does not exist"):
        await store.resolve(
            "run_missing",
            expected_agent_kind=AgentKind.WRITING,
        )


@pytest.mark.asyncio
async def test_run_begin_persists_identity_in_the_canonical_transaction(
    temp_db,
) -> None:
    identity = replacement_implementation(AgentKind.WRITING)
    outputs = SqliteAgentOutputRepository(
        temp_db,
        run_repository=SqliteRunRepository(temp_db),
        run_begin_projector=AgentImplementationBeginProjector(temp_db),
    )

    begun, _event = await outputs.begin_run_lifecycle(
        RunCreateParams(
            session_id=None,
            prompt="test",
            mode="agent",
            binding=RunBinding(
                namespace="replacement.test",
                aggregate_id="book-1",
                command_id="command-1",
                attributes={
                    AGENT_IMPLEMENTATION_ATTRIBUTE: identity.to_mapping(),
                },
            ),
        ),
        AgentEvent(
            type=CoreEventType.RUN_STARTED,
            payload={"status": RunStatus.RUNNING.value},
        ),
    )

    assert await SqliteAgentImplementationStore(temp_db).load(
        begun.run_id
    ) == identity


@pytest.mark.asyncio
async def test_run_begin_persists_explicit_legacy_profile_identity(temp_db) -> None:
    outputs = SqliteAgentOutputRepository(
        temp_db,
        run_repository=SqliteRunRepository(temp_db),
        run_begin_projector=AgentImplementationBeginProjector(temp_db),
    )

    begun, _event = await outputs.begin_run_lifecycle(
        RunCreateParams(
            session_id=None,
            prompt="test",
            mode="agent",
            binding=RunBinding(
                namespace="writing.chat.request",
                aggregate_id="1",
                command_id="command-legacy",
                attributes={"agentProfile": "writing"},
            ),
        ),
        AgentEvent(
            type=CoreEventType.RUN_STARTED,
            payload={"status": RunStatus.RUNNING.value},
        ),
    )

    assert await SqliteAgentImplementationStore(temp_db).load(
        begun.run_id
    ) == legacy_implementation(AgentKind.WRITING)


@pytest.mark.asyncio
async def test_invalid_begin_identity_rolls_back_run_and_started_event(
    temp_db,
) -> None:
    outputs = SqliteAgentOutputRepository(
        temp_db,
        run_repository=SqliteRunRepository(temp_db),
        run_begin_projector=AgentImplementationBeginProjector(temp_db),
    )

    with pytest.raises(ValueError, match="shape"):
        await outputs.begin_run_lifecycle(
            RunCreateParams(
                session_id=None,
                prompt="test",
                mode="agent",
                binding=RunBinding(
                    namespace="replacement.test",
                    aggregate_id="book-1",
                    command_id="command-invalid",
                    attributes={AGENT_IMPLEMENTATION_ATTRIBUTE: {}},
                ),
            ),
            AgentEvent(
                type=CoreEventType.RUN_STARTED,
                payload={"status": RunStatus.RUNNING.value},
            ),
        )

    assert await temp_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_runs"
    ) == {"count": 0}
    assert await temp_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_run_events"
    ) == {"count": 0}


@pytest.mark.asyncio
async def test_begin_identity_must_match_profile_and_domain(temp_db) -> None:
    outputs = SqliteAgentOutputRepository(
        temp_db,
        run_repository=SqliteRunRepository(temp_db),
        run_begin_projector=AgentImplementationBeginProjector(temp_db),
    )
    identity = replacement_implementation(AgentKind.WRITING)

    with pytest.raises(ValueError, match="conflicts with profile"):
        await outputs.begin_run_lifecycle(
            RunCreateParams(
                session_id=None,
                prompt="test",
                mode="agent",
                binding=RunBinding(
                    namespace="replacement.test",
                    aggregate_id="book-1",
                    command_id="profile-conflict",
                    attributes={
                        "agentProfile": "screenplay",
                        AGENT_IMPLEMENTATION_ATTRIBUTE: identity.to_mapping(),
                    },
                ),
            ),
            AgentEvent(
                type=CoreEventType.RUN_STARTED,
                payload={"status": RunStatus.RUNNING.value},
            ),
        )

    with pytest.raises(ValueError, match="conflicts with domain"):
        await outputs.begin_run_lifecycle(
            RunCreateParams(
                session_id=None,
                prompt="test",
                mode="agent",
                binding=RunBinding(
                    namespace="replacement.test",
                    aggregate_id="book-1",
                    command_id="domain-conflict",
                    attributes={
                        "agentProfile": "writing",
                        "domainNamespace": "purrtypos.screenplay",
                        AGENT_IMPLEMENTATION_ATTRIBUTE: identity.to_mapping(),
                    },
                ),
            ),
            AgentEvent(
                type=CoreEventType.RUN_STARTED,
                payload={"status": RunStatus.RUNNING.value},
            ),
        )

    assert await temp_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_runs"
    ) == {"count": 0}
