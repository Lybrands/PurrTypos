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
from domains.agent_policy import build_agent_public_progress_policy, build_agent_final_response_policy
from domains.novel_analysis import NovelAnalysisDomainContext
from domains.novel_analysis_prompts import build_novel_analysis_method_guidance
from purra.contracts import ExecutionState
from purra.errors import ModelGatewayError
from schemas.screenplay_agent import ScreenplayAgentRuntimeRequest
from tests.test_novel_analysis import _source


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("submit", "invalid_first"),
    [(True, False), (True, True), (False, False)],
)
async def test_analysis_unit_uses_real_tools_and_public_journal_without_json(
    tmp_path, monkeypatch, submit, invalid_first,
):
    db = DatabaseConnection(tmp_path)
    await db.init()
    composition = create_agent_composition(db)
    bindings = []
    calls = []
    input_payload = {"sourceEvidence": {"text": "PRIVATE_SOURCE_MARKER" + "a" * 70_000}}
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
        assert build_agent_final_response_policy() not in "\n".join(str(m.get("content")) for m in messages)
        assert build_novel_analysis_method_guidance() not in "\n".join(str(m.get("content")) for m in messages)
        assert options.get("response_format") is None
        tool_messages = [m for m in messages if m["role"] == "tool"]
        serialized = json.dumps(messages)
        assert "PRIVATE_SOURCE_MARKER" in serialized
        assert "a" * 70_000 in serialized
        assert "Untrusted context block 'novel_analysis_unit_input'" in serialized
        assert {item["function"]["name"] for item in options["tools"]} == {"submitNovelAnalysisResult"}
        if not tool_messages and submit:
            result = (
                {**candidate, "storyOverview": {"overview": "错误的概览字段"}}
                if invalid_first
                else candidate
            )
            tool, arguments, commentary = "submitNovelAnalysisResult", {"result": result}, "整理有原文依据的候选"
        elif len(tool_messages) == 1 and submit and invalid_first:
            assert "invalid_tool_arguments_schema" in tool_messages[-1]["content"]
            tool, arguments, commentary = "submitNovelAnalysisResult", {"result": candidate}, "修正分析候选格式"
        else:
            tool = None

        async def chunks():
            if tool:
                yield {"choices": [{"delta": {"content": "【公开说明】" + commentary}, "finish_reason": None}]}
                early = await db.fetch_all("SELECT payload_json FROM ai_agent_run_events WHERE run_id=? AND visibility='public' AND channel='commentary'", [bindings[0]])
                assert commentary in json.dumps(early, ensure_ascii=False)
                yield {"choices": [{"delta": {"content": "【说明结束】"}, "finish_reason": None}]}
                yield {"choices": [{"delta": {"tool_calls": [{
                    "index": 0, "id": f"call-{len(tool_messages)}", "type": "function",
                    "function": {"name": tool, "arguments": json.dumps(arguments)},
                }]}, "finish_reason": "tool_calls"}]}
            else:
                yield {"choices": [{"delta": {"content": "PRIVATE_FINAL_MARKER"}, "finish_reason": "stop"}]}
        return {"applied_generation_limit": options.get("max_tokens"), "stream": chunks(), "model": "model"}

    monkeypatch.setattr("infrastructure.models.provider_router.create_chat_stream", stream)
    runtime = ScreenplayAgentRuntimeRequest(
        apiKey="test-key", options={
            "model": "model", "model_profile": "deepseek:deepseek-v4-flash", "profile_binding": "compatible", "max_generation_tokens": 2048,
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
        assert len(calls) == 2 + int(invalid_first)
        events = await composition.output_repository.list_events(run_id, after_sequence=0, limit=500)
        public = [chunk for event in events if (chunk := canonical_output_to_sse_chunk(event))]
        starts = [chunk for chunk in public if chunk["kind"] == "operation.started" and chunk["payload"]["kind"] == "tool"]
        finishes = [chunk for chunk in public if chunk["kind"] == "operation.finished"]
        assert len(starts) == 1
        assert {chunk["payload"]["operationId"] for chunk in starts} <= {
            chunk["payload"]["operationId"] for chunk in finishes if chunk["payload"]["status"] == "succeeded"
        }
        public_text = json.dumps(public, ensure_ascii=False)
        assert "整理有原文依据的候选" in public_text
        assert "PRIVATE_SOURCE_MARKER" not in public_text
        assert "PRIVATE_FINAL_MARKER" not in public_text
        assert "PRIVATE_FACT_MARKER" not in public_text
        # Completed units are reused after recovery, without another provider call.
        repeated = await models.run_json(
            context=context, instruction="提取有依据的事实", payload=input_payload,
        )
        assert repeated == (run_id, candidate)
        assert len(calls) == 2 + int(invalid_first)
    finally:
        await composition.shutdown()
        await db.close()


@pytest.mark.asyncio
async def test_analysis_candidate_requires_host_provided_input_and_bound_scope(tmp_path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    try:
        tools = {item.schema.name: item for item in build_novel_analysis_tool_catalog(db).registrations()}
        submit = tools["submitNovelAnalysisResult"]
        state = ExecutionState(domain={"interactionKind": "unit", "unitInput": {
            "sourceBinding": {"startCharacter": 1200, "endCharacter": 2400},
            "sourceEvidence": {"title": "第一章 雨夜", "text": "PRIVATE"},
        }})
        state.run_id = "unit-run"
        submit_names = submit.operation_display_params(
            state,
            {"result": {"facts": []}},
            SimpleNamespace(name="submitNovelAnalysisResult"),
        )["displayNames"]
        assert submit_names["zh-CN"] == "提交《第一章 雨夜》第 1200–2400 字符的分析候选"
        assert "PRIVATE" not in json.dumps(submit_names, ensure_ascii=False)
        await db.execute(
            "INSERT INTO ai_agent_run_events "
            "(run_id, event_type, kind, source, visibility, payload_json) "
            "VALUES (?, 'stream.opened', 'stream.opened', 'provider', 'private', ?)",
            [state.run_id, json.dumps({"contextEvidence": [{
                "source": "purrtypos.prepared_read",
                "metadata": {
                    "complete": True,
                    "toolName": "readNovelAnalysisInput",
                },
            }]})],
        )
        result = await submit.handler(state, {"result": {"facts": [], "craftCards": []}}, None)
        assert result.error_code == "novel_analysis_input_not_provided"
        state.domain["interactionKind"] = "analysis"
        assert await submit.scope_validator(state, {}, None) == "novel_analysis_unit_scope_required"
    finally:
        await db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("reasoning_mode", [None, "disabled", "enabled"])
@pytest.mark.parametrize(("cancel", "single_source", "stream_failure"), [
    (False, False, False), (False, True, False), (True, False, False), (False, True, True),
])
async def test_durable_analysis_binds_unit_runs_and_recovers_public_process(
    tmp_path, monkeypatch, cancel, single_source, stream_failure, reasoning_mode,
):
    db = DatabaseConnection(tmp_path)
    await db.init()
    composition = create_agent_composition(db)
    started = asyncio.Event()
    plan_calls = 0
    final_summary = (
        "## 故事概览\n\n人物围绕红门展开行动。\n\n"
        "## 人物与冲突\n\n认知差推动了钥匙冲突。\n\n"
        "## 写作技法\n\n限制视角制造信息差。"
    )
    monkeypatch.setattr(
        "infrastructure.models.provider_model_gateway.DEV_DIAGNOSTICS_ENABLED",
        True,
    )

    async def plan(_key, messages, options, _provider, signal=None):
        nonlocal plan_calls
        plan_calls += 1
        assert signal is not None
        planner_payload = json.loads(messages[-1]["content"])
        method_context = next(
            block for block in planner_payload["planningContext"]
            if block["name"] == "novel_analysis_method"
        )
        assert method_context == {
            "name": "novel_analysis_method",
            "content": build_novel_analysis_method_guidance(),
            "untrusted": False,
        }
        policy_context = next(
            block for block in planner_payload["planningContext"]
            if block["name"] == "novel_analysis_policy"
        )
        assert "用户列出的交付维度只定义结果覆盖范围" in policy_context["content"]
        return {
            "applied_generation_limit": options.get("max_tokens"),
            "message": {
                "role": "assistant",
                "content": json.dumps({
                    "needsTodos": True,
                    "title": "核对红门因果",
                    "goal": "说明人物行动和信息差如何推动情节",
                    "taskSpec": {
                        "goal": "形成可审核的红门因果分析",
                        "operation": "analyze",
                        "instruction": "围绕红门事件核对人物行动与原文证据",
                        "deliverable": "带证据的因果分析",
                    },
                    "todos": [
                        {
                            "id": "trace-actions",
                            "title": "追踪人物行动",
                            "type": "analyze",
                            "executor": "model",
                            "expectedTools": [],
                            "riskLevel": "read",
                        },
                        {
                            "id": "review-causality",
                            "title": "复核因果证据",
                            "type": "review",
                            "executor": "model",
                            "expectedTools": [],
                            "dependsOn": ["trace-actions"],
                            "riskLevel": "read",
                        },
                        {
                            "id": "analyze-craft",
                            "title": "分析信息差的叙事作用",
                            "type": "analyze",
                            "executor": "model",
                            "expectedTools": [],
                            "dependsOn": ["review-causality"],
                            "riskLevel": "read",
                        },
                    ],
                }, ensure_ascii=False),
            },
            "model": "planner-model",
            "finish_reason": "stop",
        }

    async def stream(_key, messages, options, _provider, signal=None):
        if messages and "Write the final user-facing response" in str(
            messages[0].get("content") or ""
        ):
            async def final_chunks():
                yield {
                    "choices": [{
                        "delta": {"content": final_summary},
                        "finish_reason": "stop",
                    }],
                }
            return {
                "applied_generation_limit": options.get("max_tokens"),
                "stream": final_chunks(),
                "model": "model",
            }
        available_tools = {
            item["function"]["name"] for item in options.get("tools", [])
        }
        assert "request_plan" not in available_tools
        history = [message for message in messages if message["role"] == "tool"]
        if not history:
            source = json.loads(next(m["content"].split("\n", 1)[1] for m in messages
                if m.get("content", "").startswith("Untrusted context block 'novel_analysis_unit_input'")))
            if source.get("stage"):
                from tests.support.writing_distillation import stage_result
                if source["stage"] == "trial_skill":
                    assert set(source) == {"stage", "writingSkill", "submissionContract"}
                    assert source["submissionContract"]["tool"] == "submitWritingSkillTrials"
                    assert "甲" not in json.dumps(source, ensure_ascii=False)
                candidate = stage_result(source)
            elif "sourceEvidence" in source:
                excerpt = "甲看见一扇红门" if "甲看见" in source["sourceEvidence"]["text"] else "乙关上红门"
                candidate = {"facts": [{
                    "factKind": "event", "subjectKey": "人物", "predicate": "行动", "value": excerpt,
                    "evidence": [{"sectionId": source["sourceBinding"]["sectionId"], "excerpt": excerpt}],
                }], "craftCards": [{"cardKind": "信息释放", "title": "行动改变信息", "bodyMarkdown": "人物行动后获得可观察的新信息。", "evidence": [{"sectionId": source["sourceBinding"]["sectionId"], "excerpt": excerpt}]}]}
                if source.get("includeStoryOverview"):
                    candidate["storyOverview"] = {"summaryMarkdown": "人物围绕红门行动", "evidence": candidate["facts"][0]["evidence"]}
            else:
                candidate = source.get("normalizedCandidates") or source["sectionCandidates"][0]
                candidate = {"facts": candidate["facts"], "craftCards": candidate["craftCards"]}
                if "normalizedCandidates" in source:
                    candidate["storyOverview"] = {"summaryMarkdown": "人物围绕红门行动", "evidence": candidate["facts"][0]["evidence"]}
            tool, args, title = next(name for name in available_tools if name.startswith("submit")), {"result": candidate}, "核对事实的原文依据"
        else:
            tool = None

        async def chunks():
            started.set()
            if cancel:
                await signal.wait()
                raise asyncio.CancelledError
            if stream_failure:
                yield {"choices": [{"delta": {"content": "正在核对材料"}, "finish_reason": None}]}
                raise ModelGatewayError("interrupted", code="upstream_stream_interrupted", retryable=True)
            if tool:
                yield {"choices": [{"delta": {"content": title}, "finish_reason": None}]}
                yield {"choices": [{"delta": {"tool_calls": [{
                    "index": 0, "id": f"call-{len(history)}", "type": "function",
                    "function": {"name": tool, "arguments": json.dumps(args, ensure_ascii=False)},
                }]}, "finish_reason": "tool_calls"}]}
            else:
                yield {"choices": [{"delta": {"content": "private completion"}, "finish_reason": "stop"}]}
        return {"applied_generation_limit": options.get("max_tokens"), "stream": chunks(), "model": "model"}

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
            source_revision_id=revision["id"], section_ids=tuple(row["id"] for row in (revision["sections"][:1] if single_source else revision["sections"])),
            task_idempotency_key="conversation-test", run_command_id="conversation-test",
            prompt="核对事实脉络", failed_resume_attempts=0,
            runtime=ScreenplayAgentRuntimeRequest(apiKey="test-key", options={
                "model": "model", "model_profile": "deepseek:deepseek-v4-flash", "profile_binding": "compatible", "max_generation_tokens": 2048,
                **({"thinking": {"type": reasoning_mode}} if reasoning_mode else {}),
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
        assert plan_calls == 1
        assert len(planning_events) == 1
        assert planning_events[0]["payload"]["display"]["labelKey"] == "agent.operation.planning"
        if cancel:
            active = (await service.list_for_revision(revision["id"]))[0]
            assert active["relatedRuns"]
            await service.cancel(active["taskId"])
        await asyncio.wait_for(task, 10)
        view = (await service.list_for_revision(revision["id"]))[0]
        assert view["prompt"] == "核对事实脉络"
        recorded_calls = await db.fetch_all(
            "SELECT payload_json FROM ai_agent_run_events "
            "WHERE run_id = ? AND event_type = 'model.call_recorded' "
            "ORDER BY id",
            [view["runId"]],
        )
        recorded_payload = next(
            json.loads(row["payload_json"])
            for row in recorded_calls
            if any(
                "purra.planning-stream/v1" in str(message.get("content") or "")
                for message in json.loads(row["payload_json"])["parameters"]["inputMessages"]
            )
        )
        recorded_messages = recorded_payload["parameters"]["inputMessages"]
        assert json.loads(recorded_calls[0]["payload_json"]) == recorded_payload
        recorded_planner_payload = json.loads(recorded_messages[-1]["content"])
        assert "核对事实脉络" in json.dumps(
            recorded_planner_payload,
            ensure_ascii=False,
        )
        recorded_method_context = next(
            block for block in recorded_planner_payload["planningContext"]
            if block["name"] == "novel_analysis_method"
        )
        assert recorded_method_context["content"] == (
            build_novel_analysis_method_guidance()
        )
        assert view["taskStatus"] == ("canceled" if cancel else "failed" if stream_failure else "completed"), view
        if not stream_failure:
            assert view["finalResponse"] == ("" if cancel else final_summary)
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
        assert [row["payload"]["text"] for row in planning_progress] == [
            "正在核对原文范围。",
        ]
        assert "needsTodos" not in json.dumps(restored["chunks"])
        assert "novel-analysis-artifact://" not in json.dumps(
            restored["chunks"], ensure_ascii=False,
        )
        assert view["analysisPlan"]["title"] == "核对红门因果"
        assert [step["id"] for step in view["analysisPlan"]["steps"]] == [
            "trace-actions",
            "review-causality",
            "analyze-craft",
        ]
        if stream_failure:
            units = await db.fetch_all("SELECT attempt, status FROM ai_agent_long_task_units WHERE task_id=? ORDER BY position", [view["taskId"]])
            assert units[0]["attempt"] == 1
            assert units[0]["status"] == "failed"
            assert all(unit["attempt"] == 0 for unit in units[1:])
            assert len(view["relatedRuns"]) == 1
            child = await db.fetch_one("SELECT model_attempt_count FROM ai_agent_runs WHERE binding_namespace='novel_source_analysis.unit'")
            assert child["model_attempt_count"] == 2
        if not cancel and not stream_failure:
            assert len(view["relatedRuns"]) == (6 if single_source else 9)
            assert view["completedUnits"] == view["totalUnits"]
            if single_source:
                assert view["totalUnits"] == 9
                counts = await db.fetch_all("SELECT model_attempt_count FROM ai_agent_runs")
                assert sum(row["model_attempt_count"] for row in counts) == 14
            assert view["artifactRef"]
            assert view["providerOutputEvents"] > 0
            artifact = await service.get_artifact(view["artifactRef"])
            assert artifact["storyOverview"]["summaryMarkdown"] == "人物围绕红门行动"
            assert all(item["evidence"] for item in artifact["facts"])
            final_events = [
                row["chunk"] for row in restored["chunks"]
                if row["chunk"].get("kind") in {
                    "provider.content_delta", "provider.delta_batch",
                }
                and row["chunk"].get("channel") == "final"
            ]
            assert len(final_events) == 1
            final_payload = final_events[0]["payload"]
            final_text = (
                str(final_payload.get("delta") or "")
                if final_events[0]["kind"] == "provider.content_delta"
                else "".join(
                    str(entry.get("payload", {}).get("delta") or "")
                    for entry in final_payload.get("entries", [])
                    if entry.get("kind") == "provider.content_delta"
                )
            )
            assert final_text == final_summary
            root_terminal = next(
                row["chunk"] for row in restored["chunks"]
                if row["chunk"].get("runId") == view["runId"]
                and row["chunk"].get("kind") == "run.lifecycle"
                and row["chunk"].get("payload", {}).get("status") == "done"
            )
            assert final_events[0]["sequence"] < root_terminal["sequence"]
    finally:
        await composition.shutdown()
        await db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('effort', [None, 'low', 'high', 'max'])
async def test_analysis_profile_preserves_resolved_effort_without_model_defaults(tmp_path, effort):
    from application.novel_analysis_agent_profile import NovelAnalysisAgentProfile
    from infrastructure.models.profiles.glm5_3_flash import GLM5_3_FLASH_PROFILE
    from purra.contracts import AgentRunRequest, AgentMessage, ModelRequest
    request = AgentRunRequest(messages=(AgentMessage(role='user', content='test'),),
        model=ModelRequest(provider='zai', model='glm-5.3-flash',
            capability_snapshot=GLM5_3_FLASH_PROFILE.capability_snapshot(context_window_tokens=32000),
            options={} if effort is None else {'reasoning_effort': effort}),
        domain_context=NovelAnalysisDomainContext(source_revision_id='r', command_id='c',
            section_ids=('s',), interaction_kind='unit', unit_input={}).to_core_context())
    prepared = await NovelAnalysisAgentProfile(None).prepare_request(request)
    assert prepared.model.options.get('reasoning_effort') == effort


@pytest.mark.parametrize('stage', ['distill_skill', 'trial_skill', 'revise_skill', 'assess_skill'])
def test_analysis_unit_exposes_only_current_stage_result_schema(stage):
    from application.novel_analysis_tools import build_novel_analysis_tool_catalog, analysis_submit_tool
    from domains.writing_distillation import DISTILLATION_STAGES
    from purra.contracts import AgentRunRequest, AgentMessage, ModelRequest
    from purra.json_values import thaw_json_mapping
    catalog = build_novel_analysis_tool_catalog(None)
    request = AgentRunRequest(messages=(AgentMessage(role='user', content='test'),),
        model=ModelRequest(provider='test', model='test'),
        domain_context=NovelAnalysisDomainContext(source_revision_id='r', command_id='c',
            section_ids=('s',), interaction_kind='unit', unit_input={'stage': stage}).to_core_context())
    submit = analysis_submit_tool({'stage': stage})
    assert catalog.enabled_names(request) == {submit}
    schema = thaw_json_mapping(catalog.get(submit).schema.parameters)
    assert schema['properties']['result'] == DISTILLATION_STAGES[stage]


@pytest.mark.asyncio
async def test_merged_candidate_rejects_unbound_quote_before_persistence(tmp_path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    try:
        tools = {t.schema.name: t for t in build_novel_analysis_tool_catalog(db).registrations()}
        state = ExecutionState(domain={'interactionKind':'unit','analysisInputProvided':True,'unitInput':{
            'normalizedCandidates':{'facts':[{'evidence':[{'sectionId':'s1','excerpt':'逐字原文',
                'segmentId':'host','segmentStartCharacter':0,'segmentEndCharacter':4}]}]}}})
        state.run_id = 'test-unit'
        result = await tools['submitNovelAnalysisResult'].handler(state, {'result':{'facts':[], 'craftCards':[],
            'storyOverview':{'summaryMarkdown':'概览','evidence':[{'sectionId':'s1','excerpt':'改写引文'}]}}}, None)
        assert result.error_code == 'novel_analysis_structured_output_invalid'
        assert await db.fetch_all('SELECT id FROM ai_agent_artifacts') == []
    finally:
        await db.close()


def test_submission_contract_exposes_complete_nested_fields_from_same_schema():
    from application.novel_analysis_tools import analysis_submission_contract
    from domains.writing_distillation import SKILL_SCHEMA
    contract = analysis_submission_contract('distill_skill')
    assert contract['completeReplacement'] is True
    assert contract['requiredFields']['result'] == ['writingSkill','revisionNotes']
    assert contract['requiredFields']['result.writingSkill'] == list(SKILL_SCHEMA['properties'])
    assert contract['requiredFields']['result.writingSkill.procedure[]'] == ['action','rationale','check']
    assert 'result.writingSkill.revisionNotes' not in contract['requiredFields']
    assert analysis_submission_contract(None) is None


@pytest.mark.asyncio
@pytest.mark.parametrize('repair', [True, False])
async def test_silent_stage_cannot_submit_until_real_public_chunks_arrive(tmp_path, monkeypatch, repair):
    db = DatabaseConnection(tmp_path)
    await db.init()
    composition = create_agent_composition(db)
    calls = []
    bound = []

    async def bind(run_id):
        bound.append(run_id)

    async def stream(_key, messages, options, _provider, signal=None):
        calls.append(messages)
        history = [m for m in messages if m['role'] == 'tool']
        success = any('artifactRef' in m.get('content', '') for m in history)
        if history and not success:
            assert 'public_progress_required' in history[-1]['content']
            assert await db.fetch_all('SELECT id FROM ai_agent_artifacts') == []

        async def chunks():
            if success:
                yield {'choices':[{'delta':{'content':'PRIVATE_COMPLETION'}, 'finish_reason':'stop'}]}
                return
            if history and repair:
                yield {'choices':[{'delta':{'content':'【公开说明】正在核对当前片段'}, 'finish_reason':None}]}
                visible = await db.fetch_all("SELECT payload_json FROM ai_agent_run_events WHERE run_id=? AND visibility='public' AND channel='commentary'", [bound[0]])
                assert '正在核对当前片段' in json.dumps(visible, ensure_ascii=False)
                yield {'choices':[{'delta':{'content':'中的事实依据。【说明结束】'}, 'finish_reason':None}]}
            yield {'choices':[{'delta':{'tool_calls':[{
                'index':0, 'id':f'call-{len(calls)}', 'type':'function',
                'function':{'name':'submitNovelAnalysisResult', 'arguments':json.dumps({'result':{'facts':[], 'craftCards':[]}})},
            }]}, 'finish_reason':'tool_calls'}]}
        return {'applied_generation_limit':options.get('max_tokens'), 'stream':chunks(), 'model':'model'}

    monkeypatch.setattr('infrastructure.models.provider_router.create_chat_stream', stream)
    runtime = ScreenplayAgentRuntimeRequest(apiKey='test', options={
        'model':'model', 'model_profile':'deepseek:deepseek-v4-flash', 'profile_binding':'compatible',
        'max_generation_tokens':2048}, contextWindow='128k')
    context = SimpleNamespace(run_id='root', unit=SimpleNamespace(id='extract:1'), bind_run=bind,
        task=SimpleNamespace(id='task', metadata={'sourceRevisionId':'revision', 'sectionIds':['section']}))
    try:
        models = NovelAnalysisModelCalls(db, composition, runtime)
        if repair:
            _, result = await models.run_json(context=context, instruction='提取当前片段事实', payload={'sourceEvidence':{'text':'PRIVATE_SOURCE'}})
            assert result == {'facts':[], 'craftCards':[]}
            assert len(calls) == 3
        else:
            with pytest.raises(ModelGatewayError):
                await models.run_json(context=context, instruction='提取当前片段事实', payload={'sourceEvidence':{'text':'PRIVATE_SOURCE'}})
            assert len(calls) <= 6
            assert await db.fetch_all('SELECT id FROM ai_agent_artifacts') == []
        public = await db.fetch_all("SELECT payload_json FROM ai_agent_run_events WHERE visibility='public' AND channel='commentary'")
        assert 'PRIVATE_' not in json.dumps(public)
    finally:
        await composition.shutdown()
        await db.close()
