from __future__ import annotations

from dataclasses import replace

import pytest
import pytest_asyncio

from agents.screenplay.profile import (
    SCREENPLAY_REPLACEMENT_DOMAIN_NAMESPACE,
    SCREENPLAY_REPLACEMENT_PROFILE_ID,
    ScreenplayReplacementProfile,
)
from agents.screenplay.contracts import (
    ScreenplayPartOperationScope,
    ScreenplaySourceItemRef,
)
from agents.shared.implementation import AgentKind, replacement_implementation
from agents.shared.implementation_registry import AgentRolloutPolicy
from agents.shared.composition_routing import RUNTIME_PROFILE_METADATA_KEY
from application.composition_factory import create_versioned_agent_composition
from database.connection import DatabaseConnection
from infrastructure.persistence.run_store import create_run
from purra.contracts import (
    AgentMessage,
    AgentRunRequest,
    DomainContext,
    MessageRole,
    ModelRequest,
    ExecutionState,
    PlanningMode,
)
import json
import hashlib
from purra.model_protocol import generic_capability_snapshot
from purra.long_tasks import RecipeLongTaskDispatcher
from agents.screenplay.dispatcher import ScreenplayReplacementDescriptorResolver
from types import SimpleNamespace


class _Executor:
    async def execute(self, context, signal=None):
        del context, signal
        raise AssertionError("dispatcher construction must not execute a Unit")


@pytest_asyncio.fixture
async def temp_db(tmp_path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    await db.execute(
        "INSERT INTO screenplay_projects (id, title) VALUES ('project-1', '隔离剧本')"
    )
    try:
        yield db
    finally:
        await db.close()


def _request(**payload_overrides) -> AgentRunRequest:
    return AgentRunRequest(
        messages=(AgentMessage(MessageRole.USER, "创建剧本结构"),),
        model=ModelRequest(
            provider="test",
            model="test-model",
            capability_snapshot=replace(
                generic_capability_snapshot(),
                profile_id="test:model",
            ),
        ),
        session_id=7,
        domain_context=DomainContext(
            namespace=SCREENPLAY_REPLACEMENT_DOMAIN_NAMESPACE,
            payload={
                "schemaVersion": 1,
                "projectId": "project-1",
                "turnId": "turn-1",
                "commandId": "command-1",
                **payload_overrides,
            },
        ),
        metadata={"screenplayRecipe": {
            "recipeVersion": 1,
            "operation": "create",
            "targetRole": "structure",
            "maxParallelism": 1,
            "parts": [{
                "id": "structure",
                "kind": "expansion",
                "semanticKey": "structure:series-arc",
                "dependsOn": [],
                "sourceRevisionRefs": [],
                "deliverableRevisionScope": {},
                "sourceBookId": None,
                "sourceItems": [],
                "episodeNumber": None,
                "sceneId": None,
            }, {
                "id": "projection",
                "kind": "host_projection",
                "semanticKey": "projection:structure",
                "dependsOn": ["structure"],
                "sourceRevisionRefs": [],
                "deliverableRevisionScope": {},
                "sourceBookId": None,
                "sourceItems": [],
                "episodeNumber": None,
                "sceneId": None,
            }, {
                "id": "validation",
                "kind": "validation",
                "semanticKey": "validation:structure",
                "dependsOn": ["projection"],
                "sourceRevisionRefs": [],
                "deliverableRevisionScope": {},
                "sourceBookId": None,
                "sourceItems": [],
                "episodeNumber": None,
                "sceneId": None,
            }, {
                "id": "final",
                "kind": "final_response",
                "semanticKey": "final:structure",
                "dependsOn": ["validation"],
                "sourceRevisionRefs": [],
                "deliverableRevisionScope": {},
                "sourceBookId": None,
                "sourceItems": [],
                "episodeNumber": None,
                "sceneId": None,
            }],
        }},
    )


def _unit_request(kind: str) -> AgentRunRequest:
    scene = kind == "draft_scene"
    scope = ScreenplayPartOperationScope(
        project_id="project-1",
        task_id="task-1",
        unit_id="unit-1",
        attempt=1,
        part_kind=kind,
        part_key="scene-1" if scene else "part:1",
        target_role="structure",
        source_revision_refs=(),
        deliverable_revision_scope={},
        episode_number=1 if scene else None,
        scene_id="scene-1" if scene else None,
    )
    root = _request()
    return replace(root, domain_context=DomainContext(
        namespace=SCREENPLAY_REPLACEMENT_DOMAIN_NAMESPACE,
        payload={
            "schemaVersion": 1,
            "projectId": "project-1",
            "unit": scope.to_mapping(),
        },
    ))


def _ordinary_request() -> AgentRunRequest:
    return replace(
        _request(),
        planning_mode=PlanningMode.REACTIVE,
        metadata={"interactionKind": "ordinary"},
    )


@pytest.mark.asyncio
async def test_profile_validates_root_scope_and_persists_identity(temp_db) -> None:
    profile = ScreenplayReplacementProfile(temp_db)

    prepared = await profile.prepare_request(_request())

    assert prepared.session_id == 7
    assert prepared.planning_mode is PlanningMode.PLANNED
    assert profile.task_admission() is profile
    binding = profile.run_binding_attributes(prepared)
    assert binding["screenplayRecipeBinding"]["recipeVersion"] == 1
    assert binding["screenplayRecipeBinding"]["operation"] == "create"
    assert binding["screenplayRecipeBinding"]["targetRole"] == "structure"
    assert binding["screenplayRecipeBinding"]["recipeDigest"].startswith("sha256:")
    assert binding["agentImplementation"] == (
        replacement_implementation(
            AgentKind.SCREENPLAY,
            recipe_version=1,
        ).to_mapping()
    )
    assert profile.adapter.tool_catalog.names == frozenset({
        "inspectScreenplayProjectV1",
        "readScreenplayBoundRevisionV1",
        "readScreenplayPartDependenciesV1",
        "listScreenplaySourceItemsV1",
        "readScreenplaySourceItemV1",
        "readScreenplaySceneScopeV1",
        "writeScreenplayCandidatePartV1",
    })


@pytest.mark.asyncio
async def test_formal_planner_projects_host_recipe_without_model_call(temp_db) -> None:
    profile = ScreenplayReplacementProfile(temp_db)

    result = await profile.adapter.planner.create_plan(
        _request(),
        SimpleNamespace(),
    )

    assert result.model_call_count == 0
    assert result.work_plan.task_spec.operation == "create"
    assert result.work_plan.task_spec.target == {}
    assert result.work_plan.task_spec.deliverable == "structure"
    assert [step.id for step in result.work_plan.steps] == [
        "prepare-structure",
        "produce-structure",
        "verify-structure",
    ]


@pytest.mark.asyncio
async def test_profile_keeps_ordinary_conversation_reactive_and_non_recipe(
    temp_db,
) -> None:
    profile = ScreenplayReplacementProfile(temp_db)

    prepared = await profile.prepare_request(_ordinary_request())
    binding = profile.run_binding_attributes(prepared)

    assert prepared.planning_mode is PlanningMode.REACTIVE
    assert binding["interactionKind"] == "ordinary"
    assert "screenplayRecipeBinding" not in binding
    assert binding["agentImplementation"] == replacement_implementation(
        AgentKind.SCREENPLAY,
        recipe_version=1,
    ).to_mapping()


@pytest.mark.asyncio
async def test_profile_rejects_legacy_or_extra_root_shape(temp_db) -> None:
    profile = ScreenplayReplacementProfile(temp_db)

    with pytest.raises(ValueError, match="shape is invalid"):
        await profile.prepare_request(_request(toolAccess="all"))

    with pytest.raises(ValueError, match="host recipe is required"):
        await profile.prepare_request(replace(_request(), metadata={}))


@pytest.mark.asyncio
async def test_partial_policy_cannot_restore_screenplay_legacy_create(temp_db) -> None:
    composition = create_versioned_agent_composition(
        temp_db,
        agent_rollout_policy=AgentRolloutPolicy(frozenset({AgentKind.WRITING})),
    )
    try:
        assert SCREENPLAY_REPLACEMENT_PROFILE_ID in composition.agent_profile_ids
        assert (
            composition.agent_implementation_router.for_create(
                AgentKind.SCREENPLAY
            ).runtime_profile_id
            == SCREENPLAY_REPLACEMENT_PROFILE_ID
        )
    finally:
        await composition.shutdown()


@pytest.mark.asyncio
async def test_explicit_test_policy_routes_canonical_scope_to_replacement(temp_db) -> None:
    composition = create_versioned_agent_composition(
        temp_db,
        agent_rollout_policy=AgentRolloutPolicy(
            frozenset({AgentKind.SCREENPLAY})
        ),
    )
    try:
        prepared = await composition.prepare_request(_request())
        assert prepared.metadata[RUNTIME_PROFILE_METADATA_KEY] == (
            SCREENPLAY_REPLACEMENT_PROFILE_ID
        )
    finally:
        await composition.shutdown()


@pytest.mark.asyncio
async def test_profile_exposes_dispatcher_only_when_executor_is_supplied(temp_db) -> None:
    composition = create_versioned_agent_composition(temp_db)
    try:
        profile = composition.profile(SCREENPLAY_REPLACEMENT_PROFILE_ID)
        assert profile.create_long_task_dispatcher(
            long_task_repository=composition.long_task_repository,
            executor=None,
        ) is None
        dispatcher = profile.create_long_task_dispatcher(
            long_task_repository=composition.long_task_repository,
            executor=_Executor(),
        )
        assert isinstance(dispatcher, RecipeLongTaskDispatcher)
    finally:
        await composition.shutdown()


@pytest.mark.asyncio
async def test_descriptor_uses_project_command_and_terminal_budget() -> None:
    descriptor = await ScreenplayReplacementDescriptorResolver().resolve(
        None,
        None,
        SimpleNamespace(metadata={
            "projectId": "project-1",
            "commandId": "command-1",
            "modelAttemptBudget": 576,
            "failedResumeAttempts": 2,
        }),
    )

    assert descriptor.owner_id == "project-1"
    assert descriptor.idempotency_key == "command-1"
    assert descriptor.failed_resume_attempts == 2
    assert descriptor.budget_limits.max_invocation_attempts == 576
    assert descriptor.budget_exhaustion_disposition.value == "fail_permanent"


@pytest.mark.asyncio
async def test_bound_revision_tool_cannot_select_an_unfrozen_revision(temp_db) -> None:
    await temp_db.execute(
        "INSERT INTO screenplay_revisions "
        "(id, project_id, deliverable_id, revision_no, content_digest, created_by) "
        "VALUES ('revision-1', 'project-1', 'spdel:project-1:structure', 1, "
        "'digest-1', 'user')"
    )
    await temp_db.execute(
        "INSERT INTO screenplay_revision_parts "
        "(revision_id, part_type, part_key, position, content_text, content_digest) "
        "VALUES ('revision-1', 'document', 'main', 0, '结构正文', 'part-digest')"
    )
    profile = ScreenplayReplacementProfile(temp_db)
    tool = profile.adapter.tool_catalog.get("readScreenplayBoundRevisionV1")
    state = ExecutionState(domain={"screenplayOperationScope": {
        "projectId": "project-1",
        "deliverableRevisionScope": {"structure": "revision-1"},
    }})

    accepted = json.loads((await tool.handler(
        state,
        {"role": "structure"},
    )).content)
    rejected = await tool.handler(state, {"role": "screenplayDraft"})

    assert accepted["revision"]["id"] == "revision-1"
    assert accepted["parts"][0]["contentText"] == "结构正文"
    assert rejected.error_code == "screenplay_revision_outside_operation_scope"


@pytest.mark.asyncio
async def test_operation_project_inspection_exposes_only_frozen_revisions(
    temp_db,
) -> None:
    for role in ("creativeBrief", "structure"):
        revision_id = f"revision-{role}"
        await temp_db.execute(
            "INSERT INTO screenplay_revisions "
            "(id, project_id, deliverable_id, revision_no, content_digest, created_by) "
            "VALUES (?, 'project-1', ?, 1, ?, 'user')",
            [revision_id, f"spdel:project-1:{role}", f"digest:{role}"],
        )
        await temp_db.execute(
            "INSERT INTO screenplay_project_heads "
            "(project_id, deliverable_id, revision_id) VALUES ('project-1', ?, ?)",
            [f"spdel:project-1:{role}", revision_id],
        )
    profile = ScreenplayReplacementProfile(temp_db)
    state = ExecutionState(domain={"screenplayOperationScope": {
        "projectId": "project-1",
        "deliverableRevisionScope": {"structure": "revision-structure"},
    }})

    result = json.loads((await profile.adapter.tool_catalog.get(
        "inspectScreenplayProjectV1"
    ).handler(state, {})).content)

    assert result["acceptedRevisions"] == {"structure": "revision-structure"}


@pytest.mark.asyncio
async def test_bound_revision_tool_bounds_text_before_json_serialization(temp_db) -> None:
    await temp_db.execute(
        "INSERT INTO screenplay_revisions "
        "(id, project_id, deliverable_id, revision_no, content_digest, created_by) "
        "VALUES ('revision-large', 'project-1', 'spdel:project-1:structure', 1, "
        "'digest-large', 'user')"
    )
    await temp_db.execute(
        "INSERT INTO screenplay_revision_parts "
        "(revision_id, part_type, part_key, position, payload_json, content_text, "
        "content_digest) VALUES ('revision-large', 'document', 'main', 0, ?, ?, "
        "'part-large')",
        [json.dumps({"body": "元" * 9_000}), "文" * 15_000],
    )
    profile = ScreenplayReplacementProfile(temp_db)
    state = ExecutionState(domain={"screenplayOperationScope": {
        "projectId": "project-1",
        "deliverableRevisionScope": {"structure": "revision-large"},
    }})

    result = json.loads((await profile.adapter.tool_catalog.get(
        "readScreenplayBoundRevisionV1"
    ).handler(state, {"role": "structure"})).content)

    assert result["parts"][0]["payload"] == {
        "omitted": True,
        "reason": "payload_too_large",
    }
    assert len(result["parts"][0]["contentText"]) == 12_000
    assert result["parts"][0]["contentTruncated"] is True


@pytest.mark.asyncio
async def test_scene_scope_reads_only_host_bound_scene_and_revisions(temp_db) -> None:
    scene_episode = {
        "episodeNumber": 2,
        "title": "追踪",
        "scenes": [
            {"id": "scene-2a", "heading": "码头", "objective": "找到线索"},
            {"id": "scene-2b", "heading": "仓库", "objective": "面对阻拦"},
        ],
    }
    draft_episode = {
        "episodeNumber": 2,
        "sceneTexts": [
            {"sceneId": "scene-2a", "contentText": "冻结版本的码头场景"},
        ],
    }
    for revision_id, role, payload in (
        ("scene-list-frozen", "sceneList", scene_episode),
        ("draft-frozen", "screenplayDraft", draft_episode),
    ):
        await temp_db.execute(
            "INSERT INTO screenplay_revisions "
            "(id, project_id, deliverable_id, revision_no, content_digest, created_by) "
            "VALUES (?, 'project-1', ?, 1, ?, 'test')",
            [revision_id, f"spdel:project-1:{role}", f"digest:{revision_id}"],
        )
        await temp_db.execute(
            "INSERT INTO screenplay_revision_parts "
            "(revision_id, part_type, part_key, position, payload_json, "
            "content_text, content_digest) VALUES (?, 'episode', '2', 0, ?, '', ?)",
            [revision_id, json.dumps(payload, ensure_ascii=False), f"part:{revision_id}"],
        )
    scope = ScreenplayPartOperationScope(
        project_id="project-1",
        task_id="task-scene",
        unit_id="unit-scene-2a",
        attempt=1,
        part_kind="draft_scene",
        part_key="scene-2a",
        target_role="screenplayDraft",
        source_revision_refs=(),
        deliverable_revision_scope={
            "sceneList": "scene-list-frozen",
            "screenplayDraft": "draft-frozen",
        },
        episode_number=2,
        scene_id="scene-2a",
    )
    state = ExecutionState(domain={
        "screenplayOperationScope": scope.to_mapping(),
    }, run_id="run-scene-read")

    result = await ScreenplayReplacementProfile(temp_db).adapter.tool_catalog.get(
        "readScreenplaySceneScopeV1"
    ).handler(state, {})
    payload = json.loads(result.content)

    assert payload["sceneListRevisionId"] == "scene-list-frozen"
    assert payload["scenePlan"]["id"] == "scene-2a"
    assert payload["episodePlan"] == {"episodeNumber": 2, "title": "追踪"}
    assert payload["currentSceneDraft"]["contentText"] == "冻结版本的码头场景"
    receipts = await temp_db.fetch_all(
        "SELECT access_kind, resource_ref, content_digest "
        "FROM screenplay_operation_access_receipts "
        "WHERE operation_scope_id = ? ORDER BY resource_ref",
        [scope.operation_scope_id],
    )
    assert receipts == [{
        "access_kind": "revision",
        "resource_ref": "screenplay-revision-v1://draft-frozen",
        "content_digest": "digest:draft-frozen",
    }, {
        "access_kind": "revision",
        "resource_ref": "screenplay-revision-v1://scene-list-frozen",
        "content_digest": "digest:scene-list-frozen",
    }]


@pytest.mark.asyncio
async def test_unit_context_uses_operation_identity_without_part_run_binding(temp_db) -> None:
    profile = ScreenplayReplacementProfile(temp_db)
    request = _unit_request("validation")

    await profile.prepare_request(request)
    state = profile.adapter.execution_state_factory.create(request)

    assert state.domain["screenplayOperationScope"]["operationScopeId"] == (
        "task-1:unit-1:1"
    )
    assert "screenplayRootScope" not in profile.run_binding_attributes(request)
    assert profile.adapter.tool_catalog.enabled_names(request) == frozenset()


@pytest.mark.asyncio
async def test_draft_scene_enables_only_its_bounded_read_tools(
    temp_db,
) -> None:
    profile = ScreenplayReplacementProfile(temp_db)

    assert profile.adapter.tool_catalog.enabled_names(
        _unit_request("draft_scene")
    ) == frozenset({
        "readScreenplayPartDependenciesV1",
        "readScreenplaySceneScopeV1",
    })


@pytest.mark.asyncio
async def test_candidate_unit_enables_and_commits_attempt_scoped_writer(
    temp_db,
) -> None:
    await create_run(
        temp_db,
        run_id="run-screenplay-replacement",
        session_id=None,
        prompt="screenplay candidate",
        mode="screenplay",
    )
    profile = ScreenplayReplacementProfile(temp_db)
    request = _unit_request("review_dimension")
    scope = profile.adapter.execution_state_factory.create(request).domain[
        "screenplayOperationScope"
    ]
    state = ExecutionState(
        run_id="run-screenplay-replacement",
        domain={"screenplayOperationScope": scope},
    )

    enabled = profile.adapter.tool_catalog.enabled_names(request)
    result = await profile.adapter.tool_catalog.get(
        "writeScreenplayCandidatePartV1"
    ).handler(state, {"candidate": {"summary": "节奏问题明确"}})
    payload = json.loads(result.content)

    assert enabled == frozenset({
        "readScreenplayBoundRevisionV1",
        "readScreenplayPartDependenciesV1",
        "writeScreenplayCandidatePartV1",
    })
    assert result.effect_state.value == "committed"
    assert payload["artifactRef"].startswith("screenplay-candidate-v1://")


@pytest.mark.asyncio
async def test_dependency_tool_reads_only_declared_replacement_candidates(
    temp_db,
) -> None:
    await create_run(
        temp_db,
        run_id="run-dependency",
        session_id=None,
        prompt="dependency candidate",
        mode="screenplay",
    )
    from agents.screenplay.candidate_artifact import ScreenplayCandidateArtifactStore

    dependency_scope = ScreenplayPartOperationScope(
        project_id="project-1",
        task_id="task-dependencies",
        unit_id="unit-dependency",
        attempt=1,
        part_kind="document_section",
        part_key="dependency:one",
        target_role="creativeBrief",
        source_revision_refs=(),
        deliverable_revision_scope={},
    )
    receipt = await ScreenplayCandidateArtifactStore(temp_db).commit(
        scope=dependency_scope,
        run_id="run-dependency",
        payload={
            "schemaVersion": 1,
            "partKind": "document_section",
            "partKey": "dependency:one",
            "targetRole": "creativeBrief",
            "payload": {"content": "上游内容"},
        },
    )
    await temp_db.execute(
        "INSERT INTO ai_agent_long_task_units "
        "(task_id, unit_id, semantic_key, position, status, output_ref) "
        "VALUES ('task-dependencies', 'unit-dependency', 'dependency:one', 0, "
        "'completed', ?)",
        [receipt.resource_ref],
    )
    consumer_scope = ScreenplayPartOperationScope(
        project_id="project-1",
        task_id="task-dependencies",
        unit_id="unit-consumer",
        attempt=1,
        part_kind="expansion",
        part_key="expansion:one",
        target_role="structure",
        source_revision_refs=(),
        deliverable_revision_scope={},
        dependency_part_keys=("dependency:one",),
    )
    state = ExecutionState(domain={
        "screenplayOperationScope": consumer_scope.to_mapping(),
    }, run_id="run-dependency")

    result = await ScreenplayReplacementProfile(temp_db).adapter.tool_catalog.get(
        "readScreenplayPartDependenciesV1"
    ).handler(state, {})
    payload = json.loads(result.content)

    assert payload["dependencies"] == [{
        "partKey": "dependency:one",
        "candidate": {
            "schemaVersion": 1,
            "partKind": "document_section",
            "partKey": "dependency:one",
            "targetRole": "creativeBrief",
            "payload": {"content": "上游内容"},
        },
    }]
    assert await temp_db.fetch_one(
        "SELECT access_kind, resource_ref FROM screenplay_operation_access_receipts "
        "WHERE operation_scope_id = ?",
        [consumer_scope.operation_scope_id],
    ) == {
        "access_kind": "dependency",
        "resource_ref": receipt.resource_ref,
    }


@pytest.mark.asyncio
async def test_dependency_tool_reads_declared_host_captures(temp_db) -> None:
    await create_run(
        temp_db,
        run_id="run-capture-dependency",
        session_id=None,
        prompt="host-captured scene dependency",
        mode="screenplay",
    )
    from agents.screenplay.capture_artifact import ScreenplayCaptureArtifactStore

    dependency_scope = ScreenplayPartOperationScope(
        project_id="project-1",
        task_id="task-capture-dependencies",
        unit_id="scene-one",
        attempt=1,
        part_kind="draft_scene",
        part_key="ep01-sc01",
        target_role="screenplayDraft",
        source_revision_refs=(),
        deliverable_revision_scope={},
        episode_number=1,
        scene_id="ep01-sc01",
    )
    _, output_ref, _ = await ScreenplayCaptureArtifactStore(temp_db).commit(
        scope=dependency_scope,
        run_id="run-capture-dependency",
        payload={"sceneId": "ep01-sc01", "sceneText": "风暴逼近。"},
    )
    await temp_db.execute(
        "INSERT INTO ai_agent_long_task_units "
        "(task_id, unit_id, semantic_key, position, status, output_ref) "
        "VALUES ('task-capture-dependencies', 'scene-one', 'ep01-sc01', 0, "
        "'completed', ?)",
        [output_ref],
    )
    consumer_scope = ScreenplayPartOperationScope(
        project_id="project-1",
        task_id="task-capture-dependencies",
        unit_id="episode-metadata",
        attempt=1,
        part_kind="episode_metadata",
        part_key="episode-metadata:1",
        target_role="screenplayDraft",
        source_revision_refs=(),
        deliverable_revision_scope={},
        dependency_part_keys=("ep01-sc01",),
        episode_number=1,
    )
    state = ExecutionState(
        domain={"screenplayOperationScope": consumer_scope.to_mapping()},
        run_id="run-capture-dependency",
    )

    result = await ScreenplayReplacementProfile(temp_db).adapter.tool_catalog.get(
        "readScreenplayPartDependenciesV1"
    ).handler(state, {})

    assert json.loads(result.content)["dependencies"] == [{
        "partKey": "ep01-sc01",
        "candidate": {"sceneId": "ep01-sc01", "sceneText": "风暴逼近。"},
    }]


@pytest.mark.asyncio
async def test_source_tool_reads_only_digest_frozen_chapter(temp_db) -> None:
    content = "旧城潮声"
    digest = "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()
    await temp_db.execute("INSERT INTO books (id, title) VALUES ('book-1', '原作')")
    await temp_db.execute(
        "INSERT INTO outlines (id, title, book_id) VALUES ('outline-1', '正文', 'book-1')"
    )
    await temp_db.execute(
        "INSERT INTO outline_chapters (id, outline_id, title) "
        "VALUES ('chapter-1', 'outline-1', '第一章')"
    )
    await temp_db.execute(
        "INSERT INTO articles (chapter_id, content) VALUES ('chapter-1', ?)",
        [content],
    )
    scope = ScreenplayPartOperationScope(
        project_id="project-1",
        task_id="task-1",
        unit_id="source-1",
        attempt=1,
        part_kind="evidence",
        part_key="source:1",
        target_role="sourceAnalysis",
        source_revision_refs=(),
        deliverable_revision_scope={},
        source_book_id="book-1",
        source_items=(ScreenplaySourceItemRef("chapter", "chapter-1", digest),),
    )
    state = ExecutionState(
        domain={"screenplayOperationScope": scope.to_mapping()},
        run_id="run-source-read",
    )
    tool = ScreenplayReplacementProfile(temp_db).adapter.tool_catalog.get(
        "readScreenplaySourceItemV1"
    )

    accepted = json.loads((await tool.handler(
        state,
        {"sourceId": "chapter-1"},
    )).content)
    await temp_db.execute(
        "UPDATE articles SET content = '正文已变' WHERE chapter_id = 'chapter-1'"
    )
    changed = await tool.handler(state, {"sourceId": "chapter-1"})
    receipts = await temp_db.fetch_all(
        "SELECT access_kind, resource_ref, content_digest "
        "FROM screenplay_operation_access_receipts "
        "WHERE operation_scope_id = ?",
        [scope.operation_scope_id],
    )

    assert accepted["content"] == content
    assert accepted["contentDigest"] == digest
    assert changed.error_code == "screenplay_source_revision_changed"
    assert receipts == [{
        "access_kind": "source_item",
        "resource_ref": "novel-source-chapter-v1://book-1/chapter-1",
        "content_digest": digest,
    }]


@pytest.mark.asyncio
async def test_source_directory_is_empty_for_original_project_part(temp_db) -> None:
    scope = ScreenplayPartOperationScope(
        project_id="project-1",
        task_id="task-original",
        unit_id="creative-brief",
        attempt=1,
        part_kind="document_section",
        part_key="creativeBrief:main",
        target_role="creativeBrief",
        source_revision_refs=(),
        deliverable_revision_scope={},
    )
    state = ExecutionState(
        domain={"screenplayOperationScope": scope.to_mapping()},
        run_id="run-original-read",
    )
    tool = ScreenplayReplacementProfile(temp_db).adapter.tool_catalog.get(
        "listScreenplaySourceItemsV1"
    )

    result = json.loads((await tool.handler(state, {})).content)

    assert result == {"sourceBookId": None, "items": []}
