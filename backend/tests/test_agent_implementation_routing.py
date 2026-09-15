from __future__ import annotations

import pytest
import pytest_asyncio

from agents.shared.implementation import (
    AgentImplementationIdentity,
    AgentKind,
    LEGACY_IMPLEMENTATION_ID,
    REPLACEMENT_IMPLEMENTATION_ID,
    legacy_implementation,
    replacement_implementation,
)
from agents.shared.implementation_registry import (
    AgentImplementationNotInstalledError,
    AgentImplementationProfile,
    AgentImplementationRegistry,
    AgentLifecycleAction,
    AgentRolloutPolicy,
    legacy_implementation_profiles,
)
from agents.shared.implementation_router import (
    AgentImplementationRouteConflictError,
    SqliteAgentImplementationRouter,
)
from agents.shared.cancellation import VersionRoutedRunCancellationProjector
from agents.shared.run_query import VersionedAgentRunQueryService
from agents.novel_analysis.scalable_profile import (
    NOVEL_ANALYSIS_SCALABLE_PROFILE_ID,
    scalable_novel_analysis_implementation,
    scalable_novel_analysis_implementation_profile,
)
from application.composition_factory import create_agent_composition
from database.connection import DatabaseConnection
from infrastructure.persistence.run_store import create_run
from purra.contracts import RunBinding


@pytest_asyncio.fixture
async def temp_db(tmp_path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    try:
        yield db
    finally:
        await db.close()


def _registry(*extra: AgentImplementationProfile) -> AgentImplementationRegistry:
    return AgentImplementationRegistry((*legacy_implementation_profiles(), *extra))


def test_create_policy_defaults_each_agent_to_installed_legacy() -> None:
    router = SqliteAgentImplementationRouter(None, _registry())

    for agent_kind in AgentKind:
        route = router.for_create(agent_kind)
        assert route.action is AgentLifecycleAction.CREATE
        assert route.identity.agent_kind is agent_kind
        assert route.identity.implementation_id == LEGACY_IMPLEMENTATION_ID
        assert route.runtime_profile_id == agent_kind.value
        assert route.identity_source == "create_policy"


def test_each_agent_can_select_replacement_independently() -> None:
    writing_replacement = AgentImplementationProfile(
        replacement_implementation(AgentKind.WRITING),
        "writing-native-v1",
    )
    router = SqliteAgentImplementationRouter(
        None,
        _registry(writing_replacement),
        rollout_policy=AgentRolloutPolicy(frozenset({AgentKind.WRITING})),
    )

    assert router.for_create(AgentKind.WRITING).runtime_profile_id == "writing-native-v1"
    assert (
        router.for_create(AgentKind.NOVEL_ANALYSIS).identity.implementation_id
        == LEGACY_IMPLEMENTATION_ID
    )
    assert (
        router.for_create(AgentKind.SCREENPLAY).identity.implementation_id
        == LEGACY_IMPLEMENTATION_ID
    )


def test_enabled_but_uninstalled_replacement_fails_closed() -> None:
    router = SqliteAgentImplementationRouter(
        None,
        _registry(),
        rollout_policy=AgentRolloutPolicy(frozenset({AgentKind.WRITING})),
    )

    with pytest.raises(AgentImplementationNotInstalledError, match="not installed"):
        router.for_create(AgentKind.WRITING)


def test_recipe_version_is_run_identity_not_profile_registration() -> None:
    profile = AgentImplementationProfile(
        replacement_implementation(AgentKind.NOVEL_ANALYSIS),
        "novel-analysis-native-v1",
    )
    router = SqliteAgentImplementationRouter(
        None,
        _registry(profile),
        rollout_policy=AgentRolloutPolicy(
            frozenset({AgentKind.NOVEL_ANALYSIS})
        ),
    )

    route = router.for_create(AgentKind.NOVEL_ANALYSIS, recipe_version=7)
    assert route.profile is profile
    assert route.identity.recipe_version == 7


def test_profile_registration_cannot_pin_recipe_version() -> None:
    with pytest.raises(ValueError, match="cannot pin"):
        AgentImplementationProfile(
            replacement_implementation(
                AgentKind.NOVEL_ANALYSIS,
                recipe_version=7,
            ),
            "invalid",
        )


@pytest.mark.asyncio
async def test_scalable_analysis_identity_routes_without_relabeling_native_v1(
    temp_db,
) -> None:
    native_v1 = AgentImplementationProfile(
        replacement_implementation(AgentKind.NOVEL_ANALYSIS),
        "novel-analysis-native-v1",
    )
    scalable_v2 = scalable_novel_analysis_implementation_profile()
    router = SqliteAgentImplementationRouter(
        temp_db,
        _registry(native_v1, scalable_v2),
    )
    v1_run = await create_run(
        temp_db, run_id="analysis-native-v1", session_id=None,
        prompt="old", mode="novel_analysis",
    )
    v2_run = await create_run(
        temp_db, run_id="analysis-scalable-v2", session_id=None,
        prompt="new", mode="novel_analysis",
    )
    from agents.shared.implementation_store import SqliteAgentImplementationStore
    store = SqliteAgentImplementationStore(temp_db)
    await store.bind(v1_run, replacement_implementation(AgentKind.NOVEL_ANALYSIS))
    await store.bind(v2_run, scalable_novel_analysis_implementation())

    v1_route = await router.for_run(v1_run, action=AgentLifecycleAction.REPLAY)
    v2_route = await router.for_run(v2_run, action=AgentLifecycleAction.REPLAY)

    assert v1_route.runtime_profile_id == "novel-analysis-native-v1"
    assert v2_route.runtime_profile_id == NOVEL_ANALYSIS_SCALABLE_PROFILE_ID


@pytest.mark.asyncio
async def test_historical_run_is_inferred_as_dated_legacy(temp_db) -> None:
    run_id = await create_run(
        temp_db,
        run_id="historical-analysis",
        session_id=None,
        prompt="test",
        mode="novel_analysis",
        binding=RunBinding(
            namespace="novel_source_analysis",
            aggregate_id="revision-1",
            command_id="root",
        ),
    )
    router = SqliteAgentImplementationRouter(temp_db, _registry())

    route = await router.for_run(run_id, action=AgentLifecycleAction.REPLAY)

    assert route.identity == legacy_implementation(AgentKind.NOVEL_ANALYSIS)
    assert route.identity_source == "legacy_inferred"


@pytest.mark.asyncio
async def test_historical_child_inherits_unambiguous_root_kind(temp_db) -> None:
    root_id = await create_run(
        temp_db,
        run_id="historical-writing-root",
        session_id=None,
        prompt="test",
        mode="agent",
        binding=RunBinding(
            namespace="writing.chat.request",
            aggregate_id="1",
            command_id="root",
        ),
    )
    child_id = await create_run(
        temp_db,
        run_id="historical-writing-child",
        session_id=None,
        prompt="child",
        mode="agent_child",
        root_run_id=root_id,
        parent_run_id=root_id,
    )
    router = SqliteAgentImplementationRouter(temp_db, _registry())

    route = await router.for_run(child_id, action=AgentLifecycleAction.CANCEL)

    assert route.identity.agent_kind is AgentKind.WRITING
    assert route.identity_source == "legacy_inferred"
    assert await router.should_route(child_id) is True


@pytest.mark.asyncio
async def test_conflicting_historical_evidence_fails_closed(temp_db) -> None:
    run_id = await create_run(
        temp_db,
        run_id="historical-conflict",
        session_id=None,
        prompt="test",
        mode="screenplay",
        binding=RunBinding(
            namespace="writing.chat.request",
            aggregate_id="1",
            command_id="root",
        ),
    )
    router = SqliteAgentImplementationRouter(temp_db, _registry())

    with pytest.raises(AgentImplementationRouteConflictError, match="conflicting"):
        await router.for_run(run_id, action=AgentLifecycleAction.REPLAY)


@pytest.mark.asyncio
async def test_unknown_persisted_implementation_never_falls_back(temp_db) -> None:
    run_id = await create_run(
        temp_db,
        run_id="future-writing",
        session_id=None,
        prompt="test",
        mode="writing",
    )
    future = AgentImplementationIdentity(
        agent_kind=AgentKind.WRITING,
        implementation_id=REPLACEMENT_IMPLEMENTATION_ID,
        implementation_version=99,
        tool_contract_version=1,
        artifact_schema_version=1,
    )
    from agents.shared.implementation_store import SqliteAgentImplementationStore

    await SqliteAgentImplementationStore(temp_db).bind(run_id, future)
    router = SqliteAgentImplementationRouter(temp_db, _registry())

    with pytest.raises(AgentImplementationNotInstalledError, match="v99"):
        await router.for_run(run_id, action=AgentLifecycleAction.RESUME)


@pytest.mark.asyncio
async def test_persisted_identity_conflicting_with_run_evidence_fails(temp_db) -> None:
    run_id = await create_run(
        temp_db,
        run_id="persisted-evidence-conflict",
        session_id=None,
        prompt="test",
        mode="screenplay",
        binding=RunBinding(
            namespace="screenplay.conversation_turn",
            aggregate_id="project-1",
            command_id="turn-1",
        ),
    )
    from agents.shared.implementation_store import SqliteAgentImplementationStore

    await SqliteAgentImplementationStore(temp_db).bind(
        run_id,
        legacy_implementation(AgentKind.WRITING),
    )
    router = SqliteAgentImplementationRouter(temp_db, _registry())

    with pytest.raises(AgentImplementationRouteConflictError, match="conflicts"):
        await router.for_run(run_id, action=AgentLifecycleAction.REPLAY)


@pytest.mark.asyncio
async def test_expected_agent_kind_mismatch_fails_closed(temp_db) -> None:
    run_id = await create_run(
        temp_db,
        run_id="historical-screenplay",
        session_id=None,
        prompt="test",
        mode="screenplay",
    )
    router = SqliteAgentImplementationRouter(temp_db, _registry())

    with pytest.raises(AgentImplementationRouteConflictError, match="another"):
        await router.for_run(
            run_id,
            action=AgentLifecycleAction.CANCEL,
            expected_agent_kind=AgentKind.WRITING,
        )


@pytest.mark.asyncio
async def test_composition_installs_only_replacement_create_routes(
    temp_db,
) -> None:
    composition = create_agent_composition(temp_db)
    try:
        for agent_kind in AgentKind:
            assert (
                composition.agent_implementation_router.for_create(
                    agent_kind
                ).identity.implementation_id
                == REPLACEMENT_IMPLEMENTATION_ID
            )
    finally:
        await composition.shutdown()

    partial = create_agent_composition(
        temp_db,
        agent_rollout_policy=AgentRolloutPolicy(
            frozenset({AgentKind.WRITING})
        ),
    )
    try:
        assert all(
            partial.agent_implementation_router.for_create(
                agent_kind
            ).identity.implementation_id
            == REPLACEMENT_IMPLEMENTATION_ID
            for agent_kind in AgentKind
        )
    finally:
        await partial.shutdown()


class _CancellationProjector:
    def __init__(self) -> None:
        self.calls = []

    async def project(self, run_id, receipt):
        self.calls.append((run_id, receipt))


@pytest.mark.asyncio
async def test_cancellation_projection_dispatches_by_implementation(temp_db) -> None:
    run_id = await create_run(
        temp_db,
        run_id="historical-writing-cancel",
        session_id=None,
        prompt="test",
        mode="writing",
    )
    router = SqliteAgentImplementationRouter(temp_db, _registry())
    writing = _CancellationProjector()
    screenplay = _CancellationProjector()
    routed = VersionRoutedRunCancellationProjector(
        router,
        {"writing": (writing,), "screenplay": (screenplay,)},
    )
    receipt = object()

    await routed.project(run_id, receipt)

    assert writing.calls == [(run_id, receipt)]
    assert screenplay.calls == []


@pytest.mark.asyncio
async def test_cancellation_projection_ignores_non_product_runtime_run(temp_db) -> None:
    run_id = await create_run(
        temp_db,
        run_id="framework-fixture",
        session_id=None,
        prompt="test",
        mode="agent",
    )
    router = SqliteAgentImplementationRouter(temp_db, _registry())
    projector = _CancellationProjector()
    routed = VersionRoutedRunCancellationProjector(
        router,
        {"writing": (projector,)},
    )

    await routed.project(run_id, object())

    assert projector.calls == []


@pytest.mark.asyncio
async def test_versioned_replay_marks_inferred_legacy_source(temp_db) -> None:
    run_id = await create_run(
        temp_db,
        run_id="historical-writing-replay",
        session_id=None,
        prompt="test",
        mode="writing",
    )
    composition = create_agent_composition(temp_db)
    try:
        query = VersionedAgentRunQueryService(
            composition.run_snapshot_reader,
            composition.output_repository,
            composition.agent_implementation_router,
        )
        snapshot = await query.get_snapshot(run_id)
    finally:
        await composition.shutdown()

    assert snapshot is not None
    assert snapshot["implementation"] == {
        **legacy_implementation(AgentKind.WRITING).to_mapping(),
        "identitySource": "legacy_inferred",
        "runtimeProfileId": "writing",
    }
