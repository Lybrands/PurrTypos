from __future__ import annotations

import asyncio
import hashlib
import json
from types import SimpleNamespace

import pytest
import pytest_asyncio

from agents.screenplay.composition import (
    create_isolated_screenplay_replacement_composition,
)
from agents.screenplay.executor import ScreenplayReplacementUnitExecutor
from agents.screenplay.executor import _operation_scope
from agents.screenplay.model_runner import (
    PurrAScreenplayModelPartRunner,
    SubmittedScreenplayCandidateValidator,
    _candidate_contract,
    _required_revision_roles,
)
from agents.screenplay.contracts import ScreenplayPartOperationScope
from agents.screenplay.access_contract import (
    screenplay_part_tool_guidance,
    screenplay_part_tool_names,
)
from agents.shared.implementation import AgentKind, replacement_implementation
from agents.shared.implementation_store import SqliteAgentImplementationStore
from database.connection import DatabaseConnection
from infrastructure.persistence.run_store import create_run
from infrastructure.persistence.sqlite_long_task_repository import (
    SqliteLongTaskRepository,
)
from purra.contracts import AgentMessage, MessageRole, RunBinding, ToolCall
from purra.errors import ModelGatewayError
from purra.long_tasks import (
    DurableUnitExecutionContext,
    LongTaskCreateCommand,
    LongTaskUnitSpec,
)
from schemas.screenplay_agent import ScreenplayAgentRuntimeRequest


@pytest_asyncio.fixture
async def temp_db(tmp_path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    await db.execute(
        "INSERT INTO screenplay_projects (id, title) "
        "VALUES ('project-1', 'Operation 剧本')"
    )
    try:
        yield db
    finally:
        await db.close()


def _tool_call(call_id, name, arguments):
    return {
        "choices": [{
            "delta": {
                "tool_calls": [{
                    "index": 0,
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": json.dumps(arguments, ensure_ascii=False),
                    },
                }],
            },
            "finish_reason": "tool_calls",
        }],
    }


def test_candidate_validator_requires_every_frozen_upstream_revision_read() -> None:
    scope = ScreenplayPartOperationScope(
        project_id="project-1",
        task_id="task-1",
        unit_id="unit-1",
        attempt=1,
        part_kind="document_section",
        part_key="scene-list:episode:1",
        target_role="sceneList",
        source_revision_refs=("structure-1",),
        deliverable_revision_scope={
            "structure": "structure-1",
            "sceneList": "scene-list-base",
        },
        episode_number=1,
    )
    submit = ToolCall(
        id="submit",
        name="writeScreenplayCandidatePartV1",
        arguments_json="{}",
    )
    messages = (
        AgentMessage(MessageRole.ASSISTANT, "", tool_calls=(submit,)),
        AgentMessage(
            MessageRole.TOOL,
            '{"artifactRef":"screenplay-candidate-v1://candidate"}',
            tool_call_id="submit",
        ),
    )

    missing = SubmittedScreenplayCandidateValidator(scope).validate(
        content="", messages=messages
    )
    read = ToolCall(
        id="read",
        name="readScreenplayBoundRevisionV1",
        arguments_json='{"role":"structure"}',
    )
    complete = SubmittedScreenplayCandidateValidator(scope).validate(
        content="",
        messages=(
            AgentMessage(MessageRole.ASSISTANT, "", tool_calls=(read, submit)),
            *messages[1:],
        ),
    )

    assert missing.violation_code == "screenplay_required_revision_not_read"
    assert complete.violation_code is None
    assert _required_revision_roles(scope) == ("structure",)


def test_review_part_requires_each_frozen_input_role() -> None:
    scope = ScreenplayPartOperationScope(
        project_id="project-1",
        task_id="task-review",
        unit_id="review-main",
        attempt=1,
        part_kind="review_dimension",
        part_key="review:main",
        target_role="review",
        source_revision_refs=("scene-list-1", "draft-1"),
        deliverable_revision_scope={
            "screenplayDraft": "draft-1",
            "sceneList": "scene-list-1",
        },
    )

    assert _required_revision_roles(scope) == ("sceneList", "screenplayDraft")
    assert _candidate_contract(scope) == {
        "verdict": "<ready | revise | major_rework>",
        "issues": [{
            "id": "<stable unique issue id>",
            "severity": "<minor | major | critical>",
            "description": "<specific problem, impact, and revision direction>",
            "sceneIds": ["<affected scene id>"],
        }],
    }


@pytest.mark.asyncio
async def test_simulated_provider_submits_candidate_inside_owning_root_operation(
    temp_db,
    monkeypatch,
) -> None:
    calls = 0
    tool_names = set()

    async def fake_stream(_key, messages, options, _provider, signal=None):
        nonlocal calls, tool_names
        calls += 1
        assert signal is not None
        tool_names = {
            item["function"]["name"] for item in options.get("tools", ())
        }

        async def stream():
            if calls == 1:
                yield _tool_call(
                    "read-source",
                    "readScreenplaySourceItemV1",
                    {"sourceId": "chapter-1"},
                )
                return
            if calls == 2:
                receipts = [item for item in messages if item["role"] == "tool"]
                source = json.loads(receipts[-1]["content"])
                assert source["content"] == "雨夜里，两名陌生人被困在旧城。"
                yield _tool_call(
                    "submit-candidate",
                    "writeScreenplayCandidatePartV1",
                    {"candidate": {"content": "被雨困住的两人必须合作。"}},
                )
                return
            receipts = [item for item in messages if item["role"] == "tool"]
            assert json.loads(receipts[-1]["content"])["artifactRef"].startswith(
                "screenplay-candidate-v1://"
            )
            yield {
                "choices": [{
                    "delta": {"content": "候选已提交。"},
                    "finish_reason": "stop",
                }],
            }

        return {
            "applied_generation_limit": options.get("max_tokens"),
            "stream": stream(),
            "model": "fixture",
        }

    monkeypatch.setattr(
        "infrastructure.models.provider_router.create_chat_stream",
        fake_stream,
    )
    await create_run(
        temp_db,
        run_id="screenplay-root",
        session_id=None,
        prompt="生成创作简报",
        mode="screenplay",
        binding=RunBinding(
            namespace="purrtypos.screenplay.root",
            aggregate_id="project-1",
            command_id="command-1",
        ),
    )
    await SqliteAgentImplementationStore(temp_db).bind(
        "screenplay-root",
        replacement_implementation(AgentKind.SCREENPLAY, recipe_version=1),
    )
    source_content = "雨夜里，两名陌生人被困在旧城。"
    source_digest = "sha256:" + hashlib.sha256(
        source_content.encode("utf-8")
    ).hexdigest()
    await temp_db.execute("INSERT INTO books (id, title) VALUES ('book-1', '原作')")
    await temp_db.execute(
        "INSERT INTO outlines (id, title, book_id) "
        "VALUES ('outline-1', '正文', 'book-1')"
    )
    await temp_db.execute(
        "INSERT INTO outline_chapters (id, outline_id, title) "
        "VALUES ('chapter-1', 'outline-1', '第一章')"
    )
    await temp_db.execute(
        "INSERT INTO articles (chapter_id, content) VALUES ('chapter-1', ?)",
        [source_content],
    )
    tasks = SqliteLongTaskRepository(temp_db)
    task = await tasks.create("screenplay-task", LongTaskCreateCommand(
        namespace="purrtypos.screenplay",
        kind="screenplay.purra-native",
        owner_id="project-1",
        created_by_run_id="screenplay-root",
        units=(LongTaskUnitSpec(
            id="unit-premise",
            semantic_key="section:premise",
            position=0,
            max_attempts=2,
            metadata={
                "partKind": "document_section",
                "semanticKey": "section:premise",
                "sourceRevisionRefs": [],
                "deliverableRevisionScope": {},
                "dependencyPartKeys": [],
                "sourceBookId": "book-1",
                "sourceItems": [{
                    "sourceType": "chapter",
                    "sourceId": "chapter-1",
                    "contentDigest": source_digest,
                }],
                "episodeNumber": None,
                "sceneId": None,
            },
        ),),
        metadata={
            "projectId": "project-1",
            "targetRole": "creativeBrief",
            "commandId": "command-1",
        },
    ))
    task = await tasks.start(task.id, expected_revision=task.revision)
    unit = await tasks.claim_unit(
        task.id,
        "unit-premise",
        worker_id="test-worker",
        lease_duration_ms=30_000,
    )
    assert unit is not None
    composition = create_isolated_screenplay_replacement_composition(temp_db)
    try:
        runtime = ScreenplayAgentRuntimeRequest(
            apiKey="fixture-key",
            apiProvider="openai",
            contextWindow="128k",
            options={
                "model": "fixture",
                "supports_thinking": False,
                "thinking_only": False,
                "max_generation_tokens": 4_096,
                "profile_max_generation_tokens": 8_192,
            },
        )
        context = DurableUnitExecutionContext(
            task=task,
            unit=unit,
            run_id="screenplay-root",
            dependency_outputs={},
        )
        model_runner = PurrAScreenplayModelPartRunner(
            temp_db,
            composition,
            runtime,
        )
        result = await ScreenplayReplacementUnitExecutor(
            temp_db,
            model_runner=model_runner,
        ).execute(context, signal=asyncio.Event())

        class FailingRuns:
            async def run_operation(self, **kwargs):
                raise ModelGatewayError(
                    "provider unavailable",
                    code="provider_unavailable",
                    retryable=True,
                )

        class FailingUsage:
            async def record_from_events(self, **kwargs):
                raise RuntimeError("usage settlement failed")

        model_runner._runs = FailingRuns()
        model_runner._usage = FailingUsage()
        scope = _operation_scope(context)
        with pytest.raises(ModelGatewayError, match="provider unavailable"):
            await model_runner.run(
                scope=scope,
                enabled_tool_names=screenplay_part_tool_names(scope.part_kind),
                instruction=screenplay_part_tool_guidance(scope.part_kind),
                context=context,
                signal=asyncio.Event(),
            )
    finally:
        await composition.shutdown()

    root = await temp_db.fetch_one(
        "SELECT binding_namespace, binding_aggregate_id, binding_command_id "
        "FROM ai_agent_runs WHERE id = 'screenplay-root'"
    )
    assert calls == 3
    assert tool_names == {
        "inspectScreenplayProjectV1",
        "listScreenplaySourceItemsV1",
        "readScreenplaySourceItemV1",
        "readScreenplayBoundRevisionV1",
        "readScreenplayPartDependenciesV1",
        "writeScreenplayCandidatePartV1",
    }
    assert result.output_ref.startswith("screenplay-candidate-v1://")
    assert result.validation_receipt["operationUsage"]["usage"] == {
        "invocationCount": 3,
        "unreportedUsageAttempts": 3,
        "inputTokens": 0,
        "generationTokens": 0,
        "reasoningTokens": None,
    }
    assert await temp_db.fetch_one(
        "SELECT invocation_count, unreported_usage_attempts, root_run_id "
        "FROM screenplay_operation_usage_receipts "
        "WHERE operation_scope_id = ?",
        ["screenplay-task:unit-premise:1"],
    ) == {
        "invocation_count": 3,
        "unreported_usage_attempts": 3,
        "root_run_id": "screenplay-root",
    }
    assert result.validation_receipt["accessReceipts"] == [{
        "operationScopeId": "screenplay-task:unit-premise:1",
        "accessKind": "source_item",
        "resourceRef": "novel-source-chapter-v1://book-1/chapter-1",
        "contentDigest": source_digest,
        "metadata": {"sourceType": "chapter", "sourceId": "chapter-1"},
    }]
    assert await temp_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_provider_call_leases"
    ) == {"count": 0}
    assert root == {
        "binding_namespace": "purrtypos.screenplay.root",
        "binding_aggregate_id": "project-1",
        "binding_command_id": "command-1",
    }


@pytest.mark.asyncio
async def test_host_capture_runner_requires_and_returns_exact_structured_output(
    temp_db,
    monkeypatch,
) -> None:
    scope = ScreenplayPartOperationScope(
        project_id="project-1",
        task_id="task-host-capture",
        unit_id="unit-metadata",
        attempt=1,
        part_kind="episode_metadata",
        part_key="episode:3:metadata",
        target_role="screenplayDraft",
        source_revision_refs=(),
        deliverable_revision_scope={},
        dependency_part_keys=("scene-3a",),
        episode_number=3,
    )
    seen = {}

    async def fake_operation(**kwargs):
        seen["request"] = kwargs["request"]
        seen["options"] = kwargs["options"]
        validator = kwargs["options"].response_validators[0]
        invalid = validator.validate(content='{"episodeNumber":4}', messages=())
        assert invalid.violation_code == "screenplay_structured_output_invalid"
        return SimpleNamespace(
            outcome=SimpleNamespace(value="completed"),
            final_response=json.dumps({
                "episodeNumber": 3,
                "title": "第三夜",
                "continuitySummary": "线索指向仓库，追踪尚未结束。",
            }, ensure_ascii=False),
        ), scope.operation_scope_id

    monkeypatch.setattr(
        "agents.screenplay.model_runner.run_durable_operation",
        fake_operation,
    )
    runtime = ScreenplayAgentRuntimeRequest(
        apiKey="fixture-key",
        apiProvider="zai",
        baseURL="https://open.bigmodel.cn/api/paas/v4",
        contextWindow="128k",
        options={
            "model": "glm-5.3-flash",
            "model_profile": "zai:glm-5.3-flash",
            "max_generation_tokens": 4_096,
        },
    )
    context = SimpleNamespace(
        run_id="screenplay-root",
        task=SimpleNamespace(id=scope.task_id),
        unit=SimpleNamespace(id=scope.unit_id),
    )

    evidence = await PurrAScreenplayModelPartRunner(
        temp_db,
        object(),
        runtime,
    ).run(
        scope=scope,
        enabled_tool_names=("readScreenplayPartDependenciesV1",),
        instruction="读取依赖并生成集元数据。",
        context=context,
        signal=asyncio.Event(),
    )

    assert evidence.host_capture == {
        "episodeNumber": 3,
        "title": "第三夜",
        "continuitySummary": "线索指向仓库，追踪尚未结束。",
    }
    assert seen["request"].model.options["response_format"] == {
        "type": "json_object"
    }
    assert seen["options"].require_tool_call is True
