from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from tests.support.planning_stream import route_planning_stream

from application.composition_factory import create_agent_composition
from application.novel_analysis_executor import NovelAnalysisModelCalls
from application.novel_analysis_tools import build_novel_analysis_tool_catalog
from application.novel_analysis_service import NovelAnalysisService
from application.novel_analysis_stream import NovelAnalysisStreamQuery
from application.sse_mapping import canonical_output_to_sse_chunk
from database.connection import DatabaseConnection
from domains.agent_output_policy import build_agent_public_progress_policy
from purra.contracts import ExecutionState
from purra.errors import ModelGatewayError
from schemas.screenplay_agent import ScreenplayAgentRuntimeRequest
from tests.test_novel_analysis import _source


@pytest.mark.asyncio
@pytest.mark.parametrize("submit", [True, False])
async def test_analysis_unit_uses_real_tools_and_public_journal_without_json(tmp_path, monkeypatch, submit):
    db = DatabaseConnection(tmp_path)
    await db.init()
    composition = create_agent_composition(db)
    bindings = []
    calls = []
    input_payload = {"untrustedSource": {"text": "PRIVATE_SOURCE_MARKER" + "a" * 70_000}}
    candidate = {"facts": [{
        "factKind": "event", "subjectKey": "甲", "predicate": "看见",
        "value": "PRIVATE_FACT_MARKER", "lifecycleStatus": "active",
        "evidence": [{"sectionId": "section-1", "excerpt": "PRIVATE_SOURCE_MARKER"}],
    }], "craftCards": []}

    async def bind(run_id):
        bindings.append(run_id)

    async def stream(_key, messages, options, _provider, signal=None):
        calls.append(messages)
        assert build_agent_public_progress_policy() in "\n".join(str(m.get("content")) for m in messages)
        assert options.get("response_format") is None
        tool_messages = [m for m in messages if m["role"] == "tool"]
        if not tool_messages:
            assert "PRIVATE_SOURCE_MARKER" not in json.dumps(messages)
            tool, arguments, commentary = "readNovelAnalysisInput", {}, "核对当前分析材料"
        elif len(tool_messages) == 1 and submit:
            assert "PRIVATE_SOURCE_MARKER" in tool_messages[0]["content"]
            assert len(tool_messages[0]["content"]) > 64_000
            tool, arguments, commentary = "submitNovelAnalysisResult", {"result": candidate}, "整理有原文依据的候选"
        else:
            tool = None

        async def chunks():
            if tool:
                yield {"choices": [{"delta": {"content": commentary}, "finish_reason": None}]}
                yield {"choices": [{"delta": {"tool_calls": [{
                    "index": 0, "id": f"call-{len(tool_messages)}", "type": "function",
                    "function": {"name": tool, "arguments": json.dumps(arguments)},
                }]}, "finish_reason": "tool_calls"}]}
            else:
                yield {"choices": [{"delta": {"content": "PRIVATE_FINAL_MARKER"}, "finish_reason": "stop"}]}
        return {"applied_output_limit": options.get("max_tokens"), "stream": chunks(), "model": "model"}

    monkeypatch.setattr("infrastructure.models.provider_router.create_chat_stream", stream)
    runtime = ScreenplayAgentRuntimeRequest(
        apiKey="test-key", options={
            "model": "model", "model_profile": "deepseek:deepseek-v4-flash", "max_tokens": 2048,
        }, contextWindow="128k",
    )
    context = SimpleNamespace(
        run_id="parent-run", unit=SimpleNamespace(id="extract:1"),
        task=SimpleNamespace(id="task-1", metadata={"sourceRevisionId": "revision-1", "sectionIds": ["section-1"]}),
        bind_run=bind,
    )
    try:
        models = NovelAnalysisModelCalls(db, composition, runtime)
        if not submit:
            with pytest.raises(ModelGatewayError):
                await models.run_json(
                    context=context, instruction="提取有依据的事实", payload=input_payload,
                    signal=asyncio.Event(),
                )
            failed = await db.fetch_one("SELECT status FROM ai_agent_runs WHERE id = ?", [bindings[0]])
            assert failed["status"] == "failed"
            assert len(calls) <= 4
            return
        run_id, result = await models.run_json(
            context=context, instruction="提取有依据的事实", payload=input_payload,
            signal=asyncio.Event(),
        )
        assert result == candidate
        assert bindings == [run_id]
        assert len(calls) == 3
        events = await composition.output_repository.list_events(run_id, after_sequence=0, limit=500)
        public = [chunk for event in events if (chunk := canonical_output_to_sse_chunk(event))]
        starts = [chunk for chunk in public if chunk["kind"] == "operation.started" and chunk["payload"]["kind"] == "tool"]
        finishes = [chunk for chunk in public if chunk["kind"] == "operation.finished"]
        assert len(starts) == 2
        assert {chunk["payload"]["operationId"] for chunk in starts} <= {
            chunk["payload"]["operationId"] for chunk in finishes if chunk["payload"]["status"] == "succeeded"
        }
        public_text = json.dumps(public, ensure_ascii=False)
        assert "核对当前分析材料" in public_text
        assert "整理有原文依据的候选" in public_text
        assert "PRIVATE_SOURCE_MARKER" not in public_text
        assert "PRIVATE_FINAL_MARKER" not in public_text
        assert "PRIVATE_FACT_MARKER" not in public_text
        # Completed units are reused after recovery, without another provider call.
        repeated = await models.run_json(
            context=context, instruction="提取有依据的事实", payload=input_payload,
        )
        assert repeated == (run_id, candidate)
        assert len(calls) == 3
    finally:
        await composition.shutdown()
        await db.close()


@pytest.mark.asyncio
async def test_analysis_candidate_requires_a_real_read_and_bound_scope(tmp_path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    try:
        tools = {item.schema.name: item for item in build_novel_analysis_tool_catalog(db).registrations()}
        submit = tools["submitNovelAnalysisResult"]
        state = ExecutionState(domain={"interactionKind": "unit", "unitInput": {}})
        state.run_id = "unit-run"
        result = await submit.handler(state, {"result": {"facts": [], "craftCards": []}}, None)
        assert result.error_code == "novel_analysis_input_not_read"
        state.domain["interactionKind"] = "analysis"
        assert await submit.scope_validator(state, {}, None) == "novel_analysis_unit_scope_required"
    finally:
        await db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_durable_analysis_binds_unit_runs_and_recovers_public_process(tmp_path, monkeypatch, cancel):
    db = DatabaseConnection(tmp_path)
    await db.init()
    composition = create_agent_composition(db)
    started = asyncio.Event()
    async def plan(*_args, **_kwargs):
        raise AssertionError("deterministic novel analysis planner must not call Provider")

    async def stream(_key, messages, options, _provider, signal=None):
        history = [message for message in messages if message["role"] == "tool"]
        if not history:
            tool, args, title = "readNovelAnalysisInput", {}, "读取当前分析材料"
        elif len(history) == 1:
            source = json.loads(history[0]["content"])
            if "untrustedSource" in source:
                excerpt = "甲看见一扇红门" if "甲看见" in source["untrustedSource"]["text"] else "乙关上红门"
                candidate = {"facts": [{
                    "factKind": "event", "subjectKey": "人物", "predicate": "行动", "value": excerpt,
                    "evidence": [{"sectionId": source["sourceBinding"]["sectionId"], "excerpt": excerpt}],
                }], "craftCards": []}
            else:
                candidate = source.get("normalizedCandidates") or source["sectionCandidates"][0]
                candidate = {"facts": candidate["facts"], "craftCards": candidate["craftCards"]}
                if "normalizedCandidates" in source:
                    candidate["storyOverview"] = {"summaryMarkdown": "人物围绕红门行动", "evidence": candidate["facts"][0]["evidence"]}
            tool, args, title = "submitNovelAnalysisResult", {"result": candidate}, "核对事实的原文依据"
        else:
            tool = None

        async def chunks():
            started.set()
            if cancel:
                await signal.wait()
                raise asyncio.CancelledError
            if tool:
                yield {"choices": [{"delta": {"content": title}, "finish_reason": None}]}
                yield {"choices": [{"delta": {"tool_calls": [{
                    "index": 0, "id": f"call-{len(history)}", "type": "function",
                    "function": {"name": tool, "arguments": json.dumps(args, ensure_ascii=False)},
                }]}, "finish_reason": "tool_calls"}]}
            else:
                yield {"choices": [{"delta": {"content": "private completion"}, "finish_reason": "stop"}]}
        return {"applied_output_limit": options.get("max_tokens"), "stream": chunks(), "model": "model"}

    monkeypatch.setattr(
        "infrastructure.models.provider_router.create_chat_no_stream",
        plan,
    )
    monkeypatch.setattr(
        "infrastructure.models.provider_router.create_chat_stream",
        route_planning_stream(plan, stream, progress="正在核对原文范围。"),
    )
    try:
        revision = await _source(db)
        service = NovelAnalysisService(db, composition)
        task = service.dispatch(
            source_revision_id=revision["id"], section_ids=tuple(row["id"] for row in revision["sections"]),
            task_idempotency_key="conversation-test", run_command_id="conversation-test",
            prompt="核对事实脉络", failed_resume_attempts=0,
            runtime=ScreenplayAgentRuntimeRequest(apiKey="test-key", options={
                "model": "model", "model_profile": "deepseek:deepseek-v4-flash", "max_tokens": 2048,
            }, contextWindow="128k"),
        )
        await asyncio.wait_for(started.wait(), 5)
        query = NovelAnalysisStreamQuery(
            db, output_repository=composition.output_journal, analysis_service=service,
        )
        before_plan = await query.read_page(revision["id"])
        planning_events = [
            row["chunk"]
            for row in before_plan["chunks"]
            if row["chunk"]["kind"] == "operation.started"
            and row["chunk"]["payload"].get("kind") == "planning"
        ]
        assert len(planning_events) == 1
        assert planning_events[0]["payload"]["display"]["labelKey"] == "agent.operation.planning"
        if cancel:
            active = (await service.list_for_revision(revision["id"]))[0]
            assert active["relatedRuns"]
            await service.cancel(active["taskId"])
        await asyncio.wait_for(task, 10)
        view = (await service.list_for_revision(revision["id"]))[0]
        assert view["taskStatus"] == ("canceled" if cancel else "completed"), view
        assert view["finalResponse"] == ""
        assert view["relatedRuns"]
        assert all(row["status"] != "running" for row in view["relatedRuns"])
        restored = await query.read_page(revision["id"])
        plan_id = planning_events[0]["payload"]["operationId"]
        terminals = [row["chunk"] for row in restored["chunks"]
                     if row["chunk"]["kind"] == "operation.finished"
                     and row["chunk"]["payload"]["operationId"] == plan_id]
        assert len(terminals) == 1
        assert terminals[0]["payload"]["status"] == "succeeded"
        planning_progress = [
            row["chunk"]
            for row in restored["chunks"]
            if row["chunk"]["kind"] == "planning.progress"
            and row["chunk"]["payload"]["operationId"] == plan_id
        ]
        # A deterministic Host Planner has a canonical planning operation but
        # no Provider invocation, so it must not fabricate public narration.
        assert planning_progress == []
        assert "needsTodos" not in json.dumps(restored["chunks"])
        if not cancel:
            assert len(view["relatedRuns"]) == 4
            assert view["completedUnits"] == view["totalUnits"]
            assert view["artifactRef"]
            assert view["providerOutputEvents"] > 0
            artifact = await service.get_artifact(view["artifactRef"])
            assert artifact["storyOverview"]["summaryMarkdown"] == "人物围绕红门行动"
            assert all(item["evidence"] for item in artifact["facts"])
    finally:
        await composition.shutdown()
        await db.close()
