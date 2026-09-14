from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from types import SimpleNamespace

import pytest
import pytest_asyncio

from agents.screenplay.candidate_artifact import ScreenplayCandidateArtifactStore
from agents.screenplay.access_receipts import (
    ScreenplayAccessKind,
    ScreenplayOperationAccessReceiptStore,
)
from agents.screenplay.composition import (
    create_isolated_screenplay_replacement_composition,
)
from agents.screenplay.executor import ScreenplayReplacementUnitExecutor
from agents.screenplay.host_result_artifact import ScreenplayHostResultArtifactStore
from agents.screenplay.output_contract import ScreenplayPartOutputEvidence
from agents.screenplay.contracts import ScreenplayPartOperationScope
from agents.screenplay.profile import SCREENPLAY_REPLACEMENT_PROFILE_ID
from database.connection import DatabaseConnection
from infrastructure.persistence.run_store import create_run
from purra.contracts import (
    AgentMessage,
    AgentRunRequest,
    DomainContext,
    ExecutionPlan,
    MessageRole,
    ModelRequest,
    StepExecutor,
    StepType,
    TaskSpec,
    TaskStep,
)
from purra.model_protocol import generic_capability_snapshot
from purra.cancellation import ExecutionStopSignal
from purra.errors import ModelGatewayError


SOURCE_CONTENT = "崩溃恢复测试原作。"
SOURCE_DIGEST = "sha256:" + hashlib.sha256(SOURCE_CONTENT.encode("utf-8")).hexdigest()


@pytest_asyncio.fixture
async def temp_db(tmp_path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    await db.execute(
        "INSERT INTO screenplay_projects (id, title) VALUES (?, ?)",
        ["project-full-recipe", "完整配方测试"],
    )
    await db.execute("INSERT INTO books (id, title) VALUES ('book-1', '原作')")
    await db.execute(
        "INSERT INTO outlines (id, title, book_id) "
        "VALUES ('outline-1', '正文', 'book-1')"
    )
    await db.execute(
        "INSERT INTO outline_chapters (id, outline_id, title) "
        "VALUES ('chapter-1', 'outline-1', '第一章')"
    )
    await db.execute(
        "INSERT INTO articles (chapter_id, content) VALUES ('chapter-1', ?)",
        [SOURCE_CONTENT],
    )
    await create_run(
        db,
        run_id="screenplay-root",
        session_id=7,
        prompt="生成第一集剧本",
        mode="screenplay",
    )
    try:
        yield db
    finally:
        await db.close()


class DeterministicScreenplayRunner:
    def __init__(self, db) -> None:
        self._candidates = ScreenplayCandidateArtifactStore(db)
        self.calls: list[str] = []

    async def run(self, *, scope, **_kwargs):
        self.calls.append(scope.part_kind.value)
        if scope.part_kind.value == "document_section":
            receipt = await self._candidates.commit(
                scope=scope,
                run_id="screenplay-root",
                payload={
                    "schemaVersion": 1,
                    "partKind": "document_section",
                    "partKey": scope.part_key,
                    "targetRole": scope.target_role,
                    "payload": {"content": "第一集围绕雨夜追踪展开。"},
                },
            )
            return ScreenplayPartOutputEvidence(
                candidate_artifact_id=receipt.artifact_id
            )
        if scope.part_kind.value == "draft_scene":
            return ScreenplayPartOutputEvidence(host_capture={
                "sceneId": scope.scene_id,
                "sceneText": "外景。雨夜。两人沿河岸追踪失踪车辆。",
            })
        if scope.part_kind.value == "episode_metadata":
            return ScreenplayPartOutputEvidence(host_capture={
                "episodeNumber": scope.episode_number,
                "title": "雨夜追踪",
                "continuitySummary": "两名主角被迫合作，线索指向旧码头。",
            })
        raise AssertionError(f"host-only Part called model: {scope.part_kind.value}")


@pytest.mark.asyncio
async def test_host_admission_dispatches_and_executes_complete_recipe(temp_db) -> None:
    composition = create_isolated_screenplay_replacement_composition(temp_db)
    runner = DeterministicScreenplayRunner(temp_db)
    try:
        profile = composition.profile(SCREENPLAY_REPLACEMENT_PROFILE_ID)
        request = await profile.prepare_request(_request())
        plan = _plan()
        decision = await profile.evaluate(request, plan)
        assert decision.estimated_units == 6
        assert decision.estimated_model_calls == 3
        assert decision.metadata["plannerAuthority"] == "presentation_mapping_only"

        dispatcher = profile.create_long_task_dispatcher(
            long_task_repository=composition.long_task_repository,
            executor=ScreenplayReplacementUnitExecutor(
                temp_db,
                model_runner=runner,
            ),
        )
        receipt = await dispatcher.dispatch(
            request,
            plan,
            decision,
            run_id="screenplay-root",
        )

        async def observe(_update):
            return None

        result = await dispatcher.execute(
            receipt.task_id,
            run_id="screenplay-root",
            observer=observe,
        )
        units = await composition.long_task_repository.list_units(receipt.task_id)

        assert result.status.value == "completed", [
            (item.id, item.status.value, item.error_code) for item in units
        ]
        assert result.final_response.startswith("screenplay-host-result-v1://")
        assert runner.calls == [
            "document_section",
            "draft_scene",
            "episode_metadata",
        ]
        assert [item.attempt for item in units] == [1, 1, 1, 1, 1, 1]
        assert [item.status.value for item in units] == ["completed"] * 6
        assert units[0].output_ref.startswith("screenplay-candidate-v1://")
        assert units[1].output_ref.startswith("screenplay-capture-v1://")
        assert units[2].output_ref.startswith("screenplay-capture-v1://")
        assert all(
            item.output_ref.startswith("screenplay-host-result-v1://")
            for item in units[3:]
        )
        assert [
            len(item.validation_receipt["accessReceipts"]) for item in units
        ] == [0, 0, 0, 3, 1, 1]
        assert all(
            item.validation_receipt["accessReceiptDigest"]
            for item in units
        )
        projection = await temp_db.fetch_one(
            "SELECT revision_id, target_role FROM "
            "screenplay_replacement_projection_receipts WHERE task_id = ?",
            [receipt.task_id],
        )
        revision = await temp_db.fetch_one(
            "SELECT agent_task_id, created_by, summary_json FROM screenplay_revisions "
            "WHERE id = ?",
            [projection["revision_id"]],
        )
        revision_parts = await temp_db.fetch_all(
            "SELECT part_type, part_key, content_text "
            "FROM screenplay_revision_parts WHERE revision_id = ? "
            "ORDER BY part_type, position",
            [projection["revision_id"]],
        )
        assert projection["target_role"] == "screenplayDraft"
        assert revision["agent_task_id"] == receipt.task_id
        assert revision["created_by"] == "screenplay_agent_task"
        assert json.loads(revision["summary_json"])["proposalKind"] == "scene_draft"
        assert {item["part_type"] for item in revision_parts} == {
            "document",
            "episode",
        }
        assert any("雨夜" in item["content_text"] for item in revision_parts)
        assert await temp_db.fetch_one(
            "SELECT revision_id FROM screenplay_project_heads "
            "WHERE project_id = ?",
            ["project-full-recipe"],
        ) is None

        by_id = {item.id: item for item in units}
        projection_unit = by_id["projection:screenplayDraft"]
        replay = await ScreenplayReplacementUnitExecutor(
            temp_db,
            model_runner=None,
        ).execute(SimpleNamespace(
            run_id="screenplay-root",
            task=await composition.long_task_repository.load(receipt.task_id),
            unit=replace(projection_unit, attempt=2),
            dependency_outputs={
                dependency_id: by_id[dependency_id].output_ref
                for dependency_id in projection_unit.dependencies
            },
        ))
        replay_payload = await ScreenplayHostResultArtifactStore(
            temp_db
        ).load_dependency(
            project_id="project-full-recipe",
            task_id=receipt.task_id,
            output_ref=replay.output_ref,
        )
        assert replay_payload["result"]["replayed"] is True
        assert replay_payload["result"]["revisionId"] == projection["revision_id"]
        assert await temp_db.fetch_one(
            "SELECT COUNT(*) AS count FROM screenplay_revisions "
            "WHERE agent_task_id = ?",
            [receipt.task_id],
        ) == {"count": 1}
    finally:
        await composition.shutdown()


@pytest.mark.asyncio
async def test_missing_candidate_and_transient_provider_are_retried(temp_db) -> None:
    class RecoverableRunner(DeterministicScreenplayRunner):
        def __init__(self, db) -> None:
            super().__init__(db)
            self.document_attempts = 0
            self.scene_attempts = 0

        async def run(self, *, scope, **kwargs):
            if scope.part_kind.value == "document_section":
                self.document_attempts += 1
                if self.document_attempts == 1:
                    self.calls.append(scope.part_kind.value)
                    return ScreenplayPartOutputEvidence()
            if scope.part_kind.value == "draft_scene":
                self.scene_attempts += 1
                if self.scene_attempts == 1:
                    self.calls.append(scope.part_kind.value)
                    raise ModelGatewayError(
                        "provider is busy",
                        code="provider_capacity_limited",
                        retryable=True,
                    )
            return await super().run(scope=scope, **kwargs)

    composition = create_isolated_screenplay_replacement_composition(temp_db)
    runner = RecoverableRunner(temp_db)
    try:
        dispatcher, task_id = await _dispatch(composition, temp_db, runner)

        async def observe(_update):
            return None

        result = await dispatcher.execute(
            task_id,
            run_id="screenplay-root",
            observer=observe,
        )
        units = await composition.long_task_repository.list_units(task_id)

        assert result.status.value == "completed", [
            (item.id, item.status.value, item.error_code) for item in units
        ]
        assert runner.document_attempts == 2
        assert runner.scene_attempts == 2
        assert units[0].attempt == 2
        assert units[1].attempt == 2
    finally:
        await composition.shutdown()


@pytest.mark.asyncio
async def test_durable_cancellation_before_execution_commits_no_part_artifacts(temp_db) -> None:
    composition = create_isolated_screenplay_replacement_composition(temp_db)
    runner = DeterministicScreenplayRunner(temp_db)
    try:
        dispatcher, task_id = await _dispatch(composition, temp_db, runner)
        await composition.long_task_repository.cancel(task_id)
        signal = ExecutionStopSignal()
        signal.set("request_canceled")

        async def observe(_update):
            return None

        result = await dispatcher.execute(
            task_id,
            run_id="screenplay-root",
            observer=observe,
            signal=signal,
        )
        units = await composition.long_task_repository.list_units(task_id)
        artifacts = await temp_db.fetch_all(
            "SELECT id FROM ai_agent_artifacts WHERE namespace = ?",
            ["purrtypos.screenplay.v1"],
        )

        assert result.status.value == "canceled"
        assert runner.calls == []
        assert artifacts == []
        assert all(item.status.value == "canceled" for item in units)
    finally:
        await composition.shutdown()


@pytest.mark.asyncio
async def test_restart_reconciles_finalized_candidate_before_continuing_dag(
    temp_db,
) -> None:
    composition = create_isolated_screenplay_replacement_composition(temp_db)
    runner = DeterministicScreenplayRunner(temp_db)
    try:
        dispatcher, task_id = await _dispatch(composition, temp_db, runner)
        repository = composition.long_task_repository
        task = await repository.load(task_id)
        await repository.start(task_id, expected_revision=task.revision)
        unit = await repository.claim_ready_unit(
            task_id,
            worker_id="crashed-worker",
            lease_duration_ms=30_000,
        )
        assert unit is not None
        scope = ScreenplayPartOperationScope(
            project_id="project-full-recipe",
            task_id=task_id,
            unit_id=unit.id,
            attempt=unit.attempt,
            part_kind="document_section",
            part_key="section:premise",
            target_role="screenplayDraft",
            source_revision_refs=(),
            deliverable_revision_scope={},
        )
        await ScreenplayCandidateArtifactStore(temp_db).commit(
            scope=scope,
            run_id="screenplay-root",
            payload={
                "schemaVersion": 1,
                "partKind": "document_section",
                "partKey": "section:premise",
                "targetRole": "screenplayDraft",
                "payload": {"content": "崩溃前已完成的故事前提。"},
            },
        )
        access_store = ScreenplayOperationAccessReceiptStore(temp_db)
        await access_store.record(
            scope=scope,
            run_id="screenplay-root",
            access_kind=ScreenplayAccessKind.SOURCE_ITEM,
            resource_ref="novel-source-chapter-v1://book-1/chapter-1",
            content_digest=SOURCE_DIGEST,
            metadata={"sourceType": "chapter", "sourceId": "chapter-1"},
        )

        assert await repository.recover_after_restart() == (task_id,)
        recovered = (await repository.list_units(task_id))[0]
        assert recovered.error_code == "execution_recovery_after_restart"
        await repository.resume(task_id)

        async def observe(_update):
            return None

        result = await dispatcher.execute(
            task_id,
            run_id="screenplay-root",
            observer=observe,
        )
        units = await repository.list_units(task_id)

        assert result.status.value == "completed", [
            (item.id, item.status.value, item.error_code) for item in units
        ]
        assert "document_section" not in runner.calls
        assert units[0].attempt == 2
        assert units[0].validation_receipt["operationScopeId"].endswith(":2")
        recovered_scope = replace(scope, attempt=2)
        recovered_receipts = await access_store.list_for_scope(recovered_scope)
        assert len(recovered_receipts) == 1
        assert recovered_receipts[0].resource_ref.endswith("chapter-1")
        assert recovered_receipts[0].metadata[
            "recoveredFromOperationScopeId"
        ] == scope.operation_scope_id
    finally:
        await composition.shutdown()


@pytest.mark.asyncio
async def test_projection_rejects_revision_head_changed_after_model_read(
    temp_db,
) -> None:
    deliverable_id = "spdel:project-full-recipe:structure"
    for revision_id, revision_no, digest in (
        ("revision-structure-1", 1, "digest:structure:1"),
        ("revision-structure-2", 2, "digest:structure:2"),
    ):
        await temp_db.execute(
            "INSERT INTO screenplay_revisions "
            "(id, project_id, deliverable_id, revision_no, content_digest, created_by) "
            "VALUES (?, 'project-full-recipe', ?, ?, ?, 'user')",
            [revision_id, deliverable_id, revision_no, digest],
        )
    await temp_db.execute(
        "INSERT INTO screenplay_project_heads "
        "(project_id, deliverable_id, revision_id) VALUES (?, ?, ?)",
        ["project-full-recipe", deliverable_id, "revision-structure-1"],
    )

    class StaleRevisionRunner(DeterministicScreenplayRunner):
        async def run(self, *, scope, **kwargs):
            await ScreenplayOperationAccessReceiptStore(temp_db).record(
                scope=scope,
                run_id="screenplay-root",
                access_kind=ScreenplayAccessKind.REVISION,
                resource_ref="screenplay-revision-v1://revision-structure-1",
                content_digest="digest:structure:1",
                metadata={"role": "structure"},
            )
            result = await super().run(scope=scope, **kwargs)
            await temp_db.execute(
                "UPDATE screenplay_project_heads SET revision_id = ? "
                "WHERE project_id = ? AND deliverable_id = ?",
                ["revision-structure-2", "project-full-recipe", deliverable_id],
            )
            return result

    request = replace(_request(), metadata={"screenplayRecipe": {
        "recipeVersion": 1,
        "operation": "revise",
        "targetRole": "structure",
        "maxParallelism": 1,
        "parts": [
            {
                "id": "structure-update",
                "kind": "document_section",
                "semanticKey": "section:structure-update",
                "dependsOn": [],
                "sourceRevisionRefs": ["revision-structure-1"],
                "deliverableRevisionScope": {
                    "structure": "revision-structure-1",
                },
                "sourceBookId": None,
                "sourceItems": [],
                "episodeNumber": None,
                "sceneId": None,
            },
            {
                "id": "projection",
                "kind": "host_projection",
                "semanticKey": "projection:structure",
                "dependsOn": ["structure-update"],
                "sourceRevisionRefs": ["revision-structure-1"],
                "deliverableRevisionScope": {
                    "structure": "revision-structure-1",
                },
                "sourceBookId": None,
                "sourceItems": [],
                "episodeNumber": None,
                "sceneId": None,
            },
            {
                "id": "validation",
                "kind": "validation",
                "semanticKey": "validation:structure",
                "dependsOn": ["projection"],
                "sourceRevisionRefs": ["revision-structure-1"],
                "deliverableRevisionScope": {
                    "structure": "revision-structure-1",
                },
                "sourceBookId": None,
                "sourceItems": [],
                "episodeNumber": None,
                "sceneId": None,
            },
            {
                "id": "final",
                "kind": "final_response",
                "semanticKey": "final:structure",
                "dependsOn": ["validation"],
                "sourceRevisionRefs": ["revision-structure-1"],
                "deliverableRevisionScope": {
                    "structure": "revision-structure-1",
                },
                "sourceBookId": None,
                "sourceItems": [],
                "episodeNumber": None,
                "sceneId": None,
            },
        ],
    }})
    plan = replace(
        _plan(),
        task_spec=TaskSpec(
            goal="修订结构",
            operation="revise",
            deliverable="structure",
        ),
    )
    composition = create_isolated_screenplay_replacement_composition(temp_db)
    try:
        profile = composition.profile(SCREENPLAY_REPLACEMENT_PROFILE_ID)
        prepared = await profile.prepare_request(request)
        decision = await profile.evaluate(prepared, plan)
        dispatcher = profile.create_long_task_dispatcher(
            long_task_repository=composition.long_task_repository,
            executor=ScreenplayReplacementUnitExecutor(
                temp_db,
                model_runner=StaleRevisionRunner(temp_db),
            ),
        )
        receipt = await dispatcher.dispatch(
            prepared,
            plan,
            decision,
            run_id="screenplay-root",
        )

        async def observe(_update):
            return None

        result = await dispatcher.execute(
            receipt.task_id,
            run_id="screenplay-root",
            observer=observe,
        )
        units = await composition.long_task_repository.list_units(receipt.task_id)

        assert result.status.value == "failed"
        assert units[0].status.value == "completed"
        assert units[1].status.value == "failed"
        assert await temp_db.fetch_one(
            "SELECT id FROM screenplay_revisions WHERE agent_task_id = ?",
            [receipt.task_id],
        ) is None
    finally:
        await composition.shutdown()


async def _dispatch(composition, db, runner):
    profile = composition.profile(SCREENPLAY_REPLACEMENT_PROFILE_ID)
    request = await profile.prepare_request(_request())
    plan = _plan()
    decision = await profile.evaluate(request, plan)
    dispatcher = profile.create_long_task_dispatcher(
        long_task_repository=composition.long_task_repository,
        executor=ScreenplayReplacementUnitExecutor(db, model_runner=runner),
    )
    receipt = await dispatcher.dispatch(
        request,
        plan,
        decision,
        run_id="screenplay-root",
    )
    return dispatcher, receipt.task_id


def _request() -> AgentRunRequest:
    return AgentRunRequest(
        messages=(AgentMessage(MessageRole.USER, "生成第一集剧本"),),
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
            namespace="purrtypos.screenplay",
            payload={
                "schemaVersion": 1,
                "projectId": "project-full-recipe",
                "turnId": "turn-full-recipe",
                "commandId": "command-full-recipe",
            },
        ),
        metadata={"screenplayRecipe": {
            "recipeVersion": 1,
            "operation": "create",
            "targetRole": "screenplayDraft",
            "maxParallelism": 2,
            "parts": _parts(),
        }},
    )


def _parts() -> list[dict[str, object]]:
    common = {
        "sourceRevisionRefs": [],
        "deliverableRevisionScope": {},
        "sourceBookId": None,
        "sourceItems": [],
        "episodeNumber": None,
        "sceneId": None,
    }
    return [
        {
            **common,
            "id": "premise",
            "kind": "document_section",
            "semanticKey": "section:premise",
            "dependsOn": [],
            "sourceBookId": "book-1",
            "sourceItems": [{
                "sourceType": "chapter",
                "sourceId": "chapter-1",
                "contentDigest": SOURCE_DIGEST,
            }],
        },
        {
            **common,
            "id": "scene-1",
            "kind": "draft_scene",
            "semanticKey": "scene:1",
            "dependsOn": ["premise"],
            "episodeNumber": 1,
            "sceneId": "scene:1",
        },
        {
            **common,
            "id": "episode-1-metadata",
            "kind": "episode_metadata",
            "semanticKey": "episode:1:metadata",
            "dependsOn": ["scene-1"],
            "episodeNumber": 1,
        },
        {
            **common,
            "id": "projection",
            "kind": "host_projection",
            "semanticKey": "projection:screenplayDraft",
            "dependsOn": ["premise", "scene-1", "episode-1-metadata"],
        },
        {
            **common,
            "id": "validation",
            "kind": "validation",
            "semanticKey": "validation:screenplayDraft",
            "dependsOn": ["projection"],
        },
        {
            **common,
            "id": "final",
            "kind": "final_response",
            "semanticKey": "final:screenplayDraft",
            "dependsOn": ["validation"],
        },
    ]


def _plan() -> ExecutionPlan:
    return ExecutionPlan(
        title="生成并校验第一集",
        task_spec=TaskSpec(
            goal="生成第一集剧本",
            operation="create",
            deliverable="screenplayDraft",
        ),
        steps=(TaskStep(
            id="screenplay-work",
            title="生成并校验剧本",
            type=StepType.WRITE,
            executor=StepExecutor.MODEL,
        ),),
    )
