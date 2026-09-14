from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import pytest
import pytest_asyncio

from agents.novel_analysis.composition import (
    create_isolated_novel_analysis_replacement_composition,
)
from agents.novel_analysis.executor import NovelAnalysisReplacementUnitExecutor
from agents.novel_analysis.model_runner import (
    PurrANovelAnalysisModelUnitRunner,
    _result_contract,
)
from agents.novel_analysis.attempt_artifact import NovelAnalysisAttemptArtifactStore
from agents.novel_analysis.domain import (
    NOVEL_ANALYSIS_PUBLISHABLE_FACT_KINDS,
    NOVEL_ANALYSIS_REPLACEMENT_DOMAIN_NAMESPACE,
)
from agents.novel_analysis.profile import NOVEL_ANALYSIS_REPLACEMENT_PROFILE_ID
from agents.novel_analysis.recipe import AnalysisSegment, AnalysisUnitKind
from agents.novel_analysis.submission_tool import SUBMIT_NOVEL_ANALYSIS_UNIT_RESULT
from agents.shared.implementation import AgentKind, replacement_implementation
from agents.shared.implementation_store import SqliteAgentImplementationStore
from database.connection import DatabaseConnection
from infrastructure.persistence.run_store import create_run
from infrastructure.persistence.sqlite_long_task_repository import (
    SqliteLongTaskRepository,
)
from purra.contracts import (
    AgentMessage,
    AgentRunRequest,
    DomainContext,
    ExecutionPlan,
    MessageRole,
    ModelRequest,
    RunBinding,
    StepExecutor,
    StepType,
    TaskSpec,
    TaskStep,
)
from purra.long_tasks import (
    DurableUnitExecutionContext,
    LongTaskCreateCommand,
    LongTaskUnitSpec,
)
from purra.model_protocol import generic_capability_snapshot
from schemas.screenplay_agent import ScreenplayAgentRuntimeRequest


@pytest_asyncio.fixture
async def temp_db(tmp_path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    await db.execute(
        "INSERT INTO novel_source_works (id, title, source_type) "
        "VALUES ('work-1', '测试小说', 'text')"
    )
    await db.execute(
        "INSERT INTO novel_source_revisions "
        "(id, work_id, version_no, content_digest, parser_version, byte_count, character_count) "
        "VALUES ('revision-1', 'work-1', 1, 'revision-digest', 1, 100, 100)"
    )
    await db.execute(
        "INSERT INTO novel_source_sections "
        "(id, revision_id, ordinal, title, text_content, content_digest) "
        "VALUES ('section-1', 'revision-1', 0, '第一章', ?, 'section-digest')",
        ["潮水漫过旧城。" + "风" * 93],
    )
    try:
        yield db
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_simulated_provider_reads_source_submits_and_reloads_artifact(
    temp_db,
    monkeypatch,
) -> None:
    calls = 0
    seen_tools = []

    async def fake_stream(_key, messages, options, _provider, signal=None):
        nonlocal calls
        calls += 1
        assert signal is not None
        seen_tools.append({
            item["function"]["name"] for item in options.get("tools", ())
        })

        async def stream():
            if calls == 1:
                yield _tool_call(
                    "read-source",
                    "readAnalysisSourceSegment",
                    {"segmentId": "segment-1"},
                )
                return
            if calls == 2:
                tool_messages = [item for item in messages if item["role"] == "tool"]
                source = json.loads(tool_messages[-1]["content"])
                assert source["sourceSpans"][0]["sourceSpanId"] == "S0-100"
                yield _tool_call(
                    "submit-result",
                    SUBMIT_NOVEL_ANALYSIS_UNIT_RESULT,
                    {"result": {
                        "facts": [{
                            "factKind": "event",
                            "subjectKey": "旧城",
                            "predicate": "被潮水淹没",
                            "value": True,
                            "evidenceRefs": [{
                                "segmentId": "segment-1",
                                "sourceSpanId": "S0-100",
                            }],
                        }],
                        "observations": [],
                    }},
                )
                return
            tool_messages = [item for item in messages if item["role"] == "tool"]
            receipt = json.loads(tool_messages[-1]["content"])
            assert receipt["artifactRef"].startswith("novel-analysis-v1://")
            yield {
                "choices": [{
                    "delta": {"content": "已提交。"},
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
        run_id="analysis-root",
        session_id=None,
        prompt="分析这部小说",
        mode="novel_analysis",
        binding=RunBinding(
            namespace="purrtypos.novel_analysis.root",
            aggregate_id="revision-1",
            command_id="command-1",
        ),
    )
    await SqliteAgentImplementationStore(temp_db).bind(
        "analysis-root",
        replacement_implementation(AgentKind.NOVEL_ANALYSIS, recipe_version=1),
    )
    segment = AnalysisSegment(
        id="segment-1",
        section_id="section-1",
        section_digest="section-digest",
        section_ordinal=0,
        start_character=0,
        end_character=100,
    )
    tasks = SqliteLongTaskRepository(temp_db)
    task = await tasks.create("analysis-task", LongTaskCreateCommand(
        namespace="purrtypos.novel_analysis",
        kind="novel_analysis.v1",
        owner_id="revision-1",
        created_by_run_id="analysis-root",
        units=(LongTaskUnitSpec(
            id="extract:segment-1",
            position=0,
            max_attempts=2,
            metadata={
                "unitKind": "extract",
                "segment": segment.to_mapping(),
            },
        ),),
        metadata={
            "sourceRevisionId": "revision-1",
            "commandId": "command-1",
            "segments": [segment.to_mapping()],
        },
    ))
    task = await tasks.start(task.id, expected_revision=task.revision)
    unit = await tasks.claim_unit(
        task.id,
        "extract:segment-1",
        worker_id="test-worker",
        lease_duration_ms=30_000,
    )
    assert unit is not None
    composition = create_isolated_novel_analysis_replacement_composition(temp_db)
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
        executor = NovelAnalysisReplacementUnitExecutor(
            temp_db,
            model_runner=PurrANovelAnalysisModelUnitRunner(
                temp_db,
                composition,
                runtime,
            ),
        )
        result = await executor.execute(DurableUnitExecutionContext(
            task=task,
            unit=unit,
            run_id="analysis-root",
            dependency_outputs={},
        ), signal=asyncio.Event())
    finally:
        await composition.shutdown()

    assert calls == 3
    assert seen_tools[0] == {
        "listAnalysisSourceSegments",
        "readAnalysisSourceSegment",
        SUBMIT_NOVEL_ANALYSIS_UNIT_RESULT,
    }
    assert result.output_ref.startswith("novel-analysis-v1://")
    assert result.validation_receipt["artifactReplayed"] is True


def test_extract_contract_names_every_publishable_fact_kind() -> None:
    contract = _result_contract(AnalysisUnitKind.EXTRACT)
    fact_kind_instruction = contract["facts"][0]["factKind"]

    assert all(
        kind in fact_kind_instruction
        for kind in NOVEL_ANALYSIS_PUBLISHABLE_FACT_KINDS
    )


def test_technique_contract_has_only_the_submittable_root_shape() -> None:
    contract = _result_contract(AnalysisUnitKind.DISTILL_TECHNIQUE)

    assert set(contract) == {"techniqueResult"}
    assert contract["techniqueResult"]["status"] == "generated"


def _tool_call(call_id: str, name: str, arguments: dict[str, object]):
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


@pytest.mark.asyncio
async def test_simulated_provider_completes_full_operation_recipe(
    temp_db,
    monkeypatch,
) -> None:
    calls_by_kind: dict[str, int] = {}
    schemas_by_kind: dict[str, set[str]] = {}

    async def fake_stream(_key, messages, options, _provider, signal=None):
        assert signal is not None
        payload = _unit_payload(messages)
        kind = payload["unitKind"]
        calls_by_kind[kind] = calls_by_kind.get(kind, 0) + 1
        schemas_by_kind[kind] = {
            item["function"]["name"] for item in options.get("tools", ())
        }
        tool_messages = [item for item in messages if item["role"] == "tool"]

        async def stream():
            if not tool_messages:
                read_name = (
                    "readAnalysisSourceSegment"
                    if kind == "extract"
                    else "listAnalysisObservations"
                )
                arguments = (
                    {"segmentId": "segment-1"}
                    if kind == "extract"
                    else {"limit": 20}
                )
                yield _tool_call(
                    f"read-{kind}",
                    read_name,
                    arguments,
                )
                return
            last_payload = json.loads(tool_messages[-1]["content"])
            if "artifactRef" not in last_payload:
                yield _tool_call(
                    f"submit-{kind}",
                    SUBMIT_NOVEL_ANALYSIS_UNIT_RESULT,
                    {"result": _model_result(kind, payload)},
                )
                return
            yield {
                "choices": [{
                    "delta": {"content": "已提交。"},
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
        run_id="full-analysis-root",
        session_id=None,
        prompt="分析这部小说",
        mode="novel_analysis",
        binding=RunBinding(
            namespace="purrtypos.novel_analysis.root",
            aggregate_id="revision-1",
            command_id="full-command",
        ),
    )
    await SqliteAgentImplementationStore(temp_db).bind(
        "full-analysis-root",
        replacement_implementation(AgentKind.NOVEL_ANALYSIS, recipe_version=1),
    )
    composition = create_isolated_novel_analysis_replacement_composition(temp_db)
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
        profile = composition.profile(NOVEL_ANALYSIS_REPLACEMENT_PROFILE_ID)
        request = await profile.prepare_request(_root_request("full-command"))
        plan = ExecutionPlan(
            title="分析并复核",
            task_spec=TaskSpec(goal="形成可审核分析", operation="analyze"),
            steps=(TaskStep(
                id="analysis",
                title="分析小说",
                type=StepType.ANALYZE,
                executor=StepExecutor.MODEL,
            ),),
        )
        decision = await profile.evaluate(request, plan)
        dispatcher = profile.create_long_task_dispatcher(
            long_task_repository=composition.long_task_repository,
            executor=NovelAnalysisReplacementUnitExecutor(
                temp_db,
                model_runner=PurrANovelAnalysisModelUnitRunner(
                    temp_db,
                    composition,
                    runtime,
                ),
            ),
        )
        receipt = await dispatcher.dispatch(
            request,
            plan,
            decision,
            run_id="full-analysis-root",
        )

        async def observe(_update):
            return None

        execution = await dispatcher.execute(
            receipt.task_id,
            run_id="full-analysis-root",
            observer=observe,
            signal=asyncio.Event(),
        )
        if execution.status.value != "completed":
            units = await composition.long_task_repository.list_units(
                receipt.task_id
            )
            raise AssertionError(json.dumps([
                [item.id, item.status.value, item.attempt, item.error_code]
                for item in units
            ]))
        final_id = execution.final_response.split("://", 1)[1]
        final = await NovelAnalysisAttemptArtifactStore(temp_db).load_payload(
            final_id
        )
    finally:
        await composition.shutdown()

    assert execution.status.value == "completed"
    assert final["kind"] == "review"
    assert final["reviewStatus"] == "pending_review"
    assert final["coverageReport"]["missingSegmentIds"] == []
    assert final["storyOverview"]["summaryMarkdown"] == "潮水淹没旧城。"
    assert calls_by_kind == {
        "extract": 3,
        "normalize": 3,
        "overview": 3,
        "distill_technique": 3,
    }
    assert schemas_by_kind["extract"] == {
        "listAnalysisSourceSegments",
        "readAnalysisSourceSegment",
        SUBMIT_NOVEL_ANALYSIS_UNIT_RESULT,
    }
    assert schemas_by_kind["normalize"] == {
        "listAnalysisObservations",
        "readAnalysisObservations",
        SUBMIT_NOVEL_ANALYSIS_UNIT_RESULT,
    }


def _root_request(command_id: str) -> AgentRunRequest:
    return AgentRunRequest(
        messages=(AgentMessage(MessageRole.USER, "分析这部小说"),),
        model=ModelRequest(
            provider="openai",
            model="fixture",
            capability_snapshot=replace(
                generic_capability_snapshot(),
                profile_id="test:fixture",
            ),
        ),
        domain_context=DomainContext(
            namespace=NOVEL_ANALYSIS_REPLACEMENT_DOMAIN_NAMESPACE,
            payload={
                "sourceRevisionId": "revision-1",
                "commandId": command_id,
                "segments": [{
                    "id": "segment-1",
                    "sectionId": "section-1",
                    "sectionDigest": "section-digest",
                    "sectionOrdinal": 0,
                    "startCharacter": 0,
                    "endCharacter": 100,
                }],
            },
        ),
    )


def _unit_payload(messages) -> dict[str, object]:
    for message in messages:
        content = message.get("content")
        if isinstance(content, str) and '"unitKind"' in content:
            return json.loads(content.rsplit("\n", 1)[-1])
    raise AssertionError("unit payload is unavailable")


def _model_result(kind: str, payload: dict[str, object]) -> dict[str, object]:
    reference = {"segmentId": "segment-1", "sourceSpanId": "S0-100"}
    if kind == "extract":
        return {
            "facts": [{
                "factKind": "event",
                "subjectKey": "旧城",
                "predicate": "被潮水淹没",
                "value": True,
                "evidenceRefs": [reference],
            }],
            "observations": [{
                "cardKind": "pacing",
                "title": "潮水倒计时",
                "bodyMarkdown": "用潮位形成时间压力。",
                "evidenceRefs": [reference],
            }],
        }
    dependency = payload["dependencyResults"][0]
    if kind == "normalize":
        fact = dict(dependency["facts"][0])
        fact.pop("factId")
        observation = dict(dependency["observations"][0])
        observation_id = observation.pop("observationId")
        return {
            "facts": [fact],
            "observations": [{
                **observation,
                "mergedObservationIds": [observation_id],
            }],
        }
    if kind == "overview":
        return {"storyOverview": {
            "summaryMarkdown": "潮水淹没旧城。",
            "evidenceRefs": [reference],
        }}
    if kind == "distill_technique":
        return {"techniqueResult": {
            "status": "empty",
            "reason": "材料不足以形成独立技法。",
        }}
    raise AssertionError(f"unexpected model Unit: {kind}")
