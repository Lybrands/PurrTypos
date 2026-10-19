from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest

from application.web_research import WebResearchService
from domains.web_research import SearchHit, WebPage, WebResearchError
from purra.cancellation import OperationCanceled
from tests.test_web_research import setup, install_http  # shared temporary DB and HTTP doubles


async def sources(service, count):
    return [await service.repository.add_source("web-run", SearchHit(
        f"来源{i + 1}", f"https://example.com/{i}", "公开摘要", "fixture",
    )) for i in range(count)]


def pages(rows):
    return {"pages": [{"sourceId": row["id"]} for row in rows]}


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [1, 2, 3, 4, 5])
async def test_batch_bounds_concurrency_body_and_preserves_input_order(setup, count):
    _, service = setup
    rows = await sources(service, count)
    release, started, second_done = asyncio.Event(), asyncio.Event(), asyncio.Event()

    class Reader:
        active = peak = 0
        calls, completed = [], []

        async def read(self, url):
            self.calls.append(url)
            self.active += 1
            self.peak = max(self.peak, self.active)
            if self.active == min(count, 2):
                started.set()
            try:
                await release.wait()
                if count > 1 and url.endswith("/0"):
                    await second_done.wait()
                if url.endswith("/1"):
                    second_done.set()
                self.completed.append(url)
                return WebPage(url, (url + "正文") * 1_000, truncated=True)
            finally:
                self.active -= 1

    service.reader = reader = Reader()
    task = asyncio.create_task(service.read_pages("web-run", "bounded", pages(rows)))
    try:
        await asyncio.wait_for(started.wait(), 2)
        assert len(reader.calls) == min(count, 2)
    finally:
        release.set()
    result = await asyncio.wait_for(task, 3)
    assert reader.peak == min(count, 2)
    assert reader.active == 0
    assert len(reader.calls) == count
    if count > 1:
        assert reader.completed[0].endswith("/1")
    assert [item["sourceId"] for item in result["results"]] == [r["id"] for r in rows]
    assert sum(len(item["content"]) for item in result["results"]) <= 8_000
    window = min(4_000, 8_000 // count)
    assert result["bodyBudget"] == {
        "pageCount": count, "maxTotalCharacters": 8_000, "maxCharactersPerPage": window,
    }
    for item, row in zip(result["results"], rows):
        assert item["success"] and item["untrusted"]
        assert item["title"] == row["title"] and item["url"] == row["url"]
        assert item["content"].startswith(row["url"])
        assert item["offset"] == 0
        assert item["nextOffset"] == window
        assert item["truncated"] and item["contentRevisionId"] and item["retrievedAt"]


@pytest.mark.asyncio
@pytest.mark.parametrize("arguments", [
    {}, {"pages": []}, {"pages": None}, {"pages": [{"sourceId": "a"}] * 6},
    {"pages": [None]}, {"pages": [{}]}, {"pages": [{"sourceId": ""}]},
    {"pages": [{"sourceId": "a", "offset": -1}]},
    {"pages": [{"sourceId": "a", "offset": True}]},
    {"pages": [{"sourceId": "a", "offset": 1.5}]},
    {"pages": [{"sourceId": "a", "url": "https://example.com"}]},
])
async def test_invalid_batch_rejected_before_any_receipt_or_http(setup, arguments):
    db, service = setup
    with pytest.raises(WebResearchError) as error:
        await service.read_pages("web-run", "invalid", arguments)
    assert error.value.code == "tool_input_invalid"
    assert service.reader.calls == 0
    assert await db.fetch_all("SELECT * FROM web_research_attempts") == []


@pytest.mark.asyncio
async def test_duplicate_sources_share_download_and_repeat_results_with_cached_offsets(setup):
    db, service = setup
    row = (await sources(service, 1))[0]
    args = {"pages": [{"sourceId": row["id"]}, {"sourceId": row["id"], "offset": 0}, {"sourceId": row["id"], "offset": 4_000}]}
    result = await service.read_pages("web-run", "duplicates", args)
    a, b, c = result["results"]
    assert a == b and c["success"]
    assert c["offset"] == 4_000
    assert c["contentRevisionId"] == a["contentRevisionId"]
    assert service.reader.calls == 1
    assert sum(len(item["content"]) for item in result["results"]) <= 8_000
    assert (await db.fetch_one("SELECT SUM(external_request) AS n FROM web_research_attempts WHERE kind = 'read'"))["n"] == 1


@pytest.mark.asyncio
async def test_partial_failure_is_ordered_scoped_and_replayed_without_retry(setup):
    db, service = setup
    rows = await sources(service, 4)
    await db.execute("UPDATE web_research_sources SET run_id = 'foreign' WHERE id = ?", [rows[2]["id"]])
    calls = []

    async def read(url):
        calls.append(url)
        if url.endswith("/0"):
            raise WebResearchError("web_extraction_failed", "没有可提取的正文")
        if url.endswith("/1"):
            raise RuntimeError("private transport detail")
        return WebPage(url, "成功正文" * 2_000)

    service.reader.read = read
    args = {"pages": [*pages(rows)["pages"], {"sourceId": "unknown"}]}
    result = await service.read_pages("web-run", "partial", args)
    assert [r["success"] for r in result["results"]] == [False, False, False, True, False]
    assert [r.get("code") for r in result["results"]] == ["web_extraction_failed", "web_page_unavailable", "web_source_not_found", None, "web_source_not_found"]
    assert "private transport detail" not in json.dumps(result)
    assert "title" not in result["results"][2] and "url" not in result["results"][2]
    assert result["results"][0]["title"] == "来源1"
    assert len(calls) == 3
    assert result["bodyBudget"]["maxCharactersPerPage"] == 1_600
    assert all(r["result_json"] is not None for r in await db.fetch_all("SELECT result_json FROM web_research_attempts"))
    assert await service.read_pages("web-run", "partial", args) == result
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_public_facts_use_persisted_run_scoped_batch_budgets(setup):
    from application.web_research import WebResearchPublicFactsProvider
    from purra.output import PublicFact, PublicFactBundle

    db, service = setup
    class Facts:
        async def facts_for(self, run_id, result):
            return PublicFactBundle(facts=(PublicFact("assistantAnswer", "候选答复"),))
    provider = WebResearchPublicFactsProvider(Facts(), service.repository)
    assert len((await provider.facts_for("web-run", None)).facts) == 1
    rows = await sources(service, 5)
    five = await service.read_pages("web-run", "five-budget", pages(rows))
    assert await service.read_pages("web-run", "five-budget", pages(rows)) == five
    await service.read_pages("web-run", "same-window", pages(rows))
    await service.read_pages("web-run", "one-budget", pages(rows[:1]))
    facts = (await provider.facts_for("web-run", None)).facts
    assert facts[0].key == "assistantAnswer"
    assert facts[1].key == "webPageReadingBudgets"
    assert {item["pageCount"]: item["maxCharactersPerPage"] for item in facts[1].value} == {1: 4_000, 5: 1_600}
    assert all(item["maxTotalCharacters"] == 8_000 for item in facts[1].value)
    assert len((await provider.facts_for("foreign", None)).facts) == 1
    assert service.reader.calls == 5


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [0, 6])
async def test_tool_gateway_rejects_batch_size_before_invoking_service(setup, count):
    from agents.shared.web_research_tools import with_web_research_tools
    from purra.contracts import ExecutionState, ToolBatchRequest, ToolBatchOutcome, ToolCall
    from purra.tools import InMemoryToolCatalog
    from purra.tools.executor import CoreToolExecutor
    db, service = setup
    class Sink:
        async def emit(self, event):
            pass
    result = await CoreToolExecutor(with_web_research_tools(InMemoryToolCatalog(()), service)).execute_batch(ToolBatchRequest(
        run_id="web-run", state=ExecutionState(run_id="web-run"), allowed_tool_names=frozenset({"readWebPages"}),
        calls=(ToolCall(id="invalid-size", name="readWebPages", arguments_json=json.dumps({"pages": [{"sourceId": "a"}] * count})),),
    ), Sink())
    assert result.outcome is ToolBatchOutcome.FAILED
    assert result.results[0].error
    assert service.reader.calls == 0
    assert await db.fetch_all("SELECT * FROM web_research_attempts") == []


@pytest.mark.asyncio
async def test_offset_errors_are_per_page_and_do_not_consume_new_page_quota(setup):
    db, service = setup
    rows = await sources(service, 2)
    first = await service.read_pages("web-run", "read-first", pages(rows[:1]))
    result = await service.read_pages("web-run", "bad-offsets", {"pages": [
        {"sourceId": rows[0]["id"], "offset": 99_999},
        {"sourceId": rows[1]["id"], "offset": 10},
        {"sourceId": rows[0]["id"], "offset": first["results"][0]["nextOffset"]},
    ]})
    assert [item["success"] for item in result["results"]] == [False, False, True]
    assert all(item["code"] == "tool_input_invalid" for item in result["results"][:2])
    assert service.reader.calls == 1
    assert (await db.fetch_one("SELECT SUM(external_request) AS n FROM web_research_attempts WHERE kind = 'read'"))["n"] == 1


@pytest.mark.asyncio
async def test_batch_replay_survives_restart_and_checks_entire_ordered_input(setup):
    db, service = setup
    rows = await sources(service, 3)
    args = pages(rows)
    first = await service.read_pages("web-run", "stable", args)
    other = WebResearchService(db)
    async def no_network(*args):
        raise AssertionError("replay must use its durable receipt")
    other.reader.read = no_network
    explicit_offsets = {"pages": [{**item, "offset": 0} for item in args["pages"]]}
    assert await other.read_pages("web-run", "stable", explicit_offsets) == first
    for changed in (pages(rows[:1]), pages(list(reversed(rows))), {"pages": [{"sourceId": rows[0]["id"], "offset": 1}]}):
        with pytest.raises(WebResearchError, match="不一致"):
            await other.read_pages("web-run", "stable", changed)
    with pytest.raises(WebResearchError, match="不一致"):
        await other.search("web-run", "stable", {"query": "公开资料"})


@pytest.mark.asyncio
async def test_quota_counts_new_page_attempts_and_allows_cached_pagination_at_limit(setup):
    db, service = setup
    rows = await sources(service, 8)
    await service.read_pages("web-run", "first-five", pages(rows[:5]))
    other = WebResearchService(db, reader=service.reader)
    mixed = await other.read_pages("web-run", "remaining", pages([rows[5], rows[6], rows[0]]))
    assert sum(r.get("code") == "web_budget_exhausted" for r in mixed["results"]) == 1
    assert mixed["results"][2]["success"]
    assert service.reader.calls == 6
    continuation = await other.read_pages("web-run", "cached", {"pages": [{"sourceId": rows[0]["id"], "offset": 4_000}]})
    assert continuation["results"][0]["success"]
    assert service.reader.calls == 6
    assert (await db.fetch_one("SELECT SUM(external_request) AS n FROM web_research_attempts WHERE kind = 'read'"))["n"] == 6
    assert not any(r["external_request"] for r in await db.fetch_all("SELECT external_request FROM web_research_attempts WHERE kind = 'read_batch'"))


@pytest.mark.asyncio
async def test_concurrent_service_instances_cannot_bypass_run_read_limit(setup):
    db, service = setup
    rows = await sources(service, 10)
    other = WebResearchService(db, reader=service.reader)
    batches = await asyncio.gather(service.read_pages("web-run", "a", pages(rows[:5])), other.read_pages("web-run", "b", pages(rows[5:])))
    results = [r for batch in batches for r in batch["results"]]
    assert sum(r["success"] for r in results) == 6
    assert sum(r.get("code") == "web_budget_exhausted" for r in results) == 4
    assert service.reader.calls == 6


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_mode", ["signal", "task"])
async def test_cancellation_drains_workers_does_not_start_queued_sources_or_replay(setup, cancel_mode):
    db, service = setup
    rows = await sources(service, 5)
    started, stop = asyncio.Event(), asyncio.Event()
    calls, active = [], set()

    async def read(url):
        calls.append(url)
        active.add(url)
        if len(active) == 2:
            started.set()
        try:
            await asyncio.Event().wait()
        finally:
            active.remove(url)

    service.reader.read = read
    task = asyncio.create_task(service.read_pages("web-run", "cancel-batch", pages(rows), stop))
    await asyncio.wait_for(started.wait(), 2)
    if cancel_mode == "signal":
        stop.set()
    else:
        task.cancel()
    with pytest.raises(OperationCanceled if cancel_mode == "signal" else asyncio.CancelledError):
        await asyncio.wait_for(task, 2)
    assert len(calls) == 2 and not active
    assert (await db.fetch_one("SELECT result_json FROM web_research_attempts WHERE call_id = 'cancel-batch'"))["result_json"] is None
    with pytest.raises(WebResearchError, match="中断"):
        await service.read_pages("web-run", "cancel-batch", pages(rows))
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_cancelled_partial_batch_preserves_completed_page_cache(setup):
    db, service = setup
    rows = await sources(service, 3)
    stop = asyncio.Event()
    calls = []
    async def read(url):
        calls.append(url)
        if url.endswith("/0"):
            return WebPage(url, "已读取的正文" * 1_000)
        await asyncio.Event().wait()
    service.reader.read = read
    task = asyncio.create_task(service.read_pages("web-run", "partial-cancel", pages(rows), stop))
    async def wait_for_cache():
        while not (await db.fetch_one("SELECT page_text FROM web_research_sources WHERE id = ?", [rows[0]["id"]]))["page_text"]:
            await asyncio.sleep(0)
    await asyncio.wait_for(wait_for_cache(), 2)
    stop.set()
    with pytest.raises(OperationCanceled):
        await asyncio.wait_for(task, 2)
    count = len(calls)
    replay = await service.read_pages("web-run", "reuse-cache", pages(rows[:1]))
    assert replay["results"][0]["success"] and len(calls) == count


@pytest.mark.asyncio
async def test_batch_reader_keeps_redirect_ssrf_guard_per_page(setup, monkeypatch):
    from infrastructure.web_research import PublicPageReader
    _, service = setup
    rows = await sources(service, 1)
    calls = install_http(monkeypatch, [{"status": 302, "headers": {"Location": "http://127.0.0.1:18321/settings"}}])
    service.reader = PublicPageReader()
    result = await service.read_pages("web-run", "ssrf", pages(rows))
    assert result["results"][0]["code"] == "web_url_blocked"
    assert calls == [rows[0]["url"]]


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [1, 5])
async def test_real_tool_events_reach_frontend_timeline_via_persisted_sse_wire(setup, count):
    from agents.shared.web_research_tools import with_web_research_tools
    from application.agent_tool_presentation import enforce_agent_tool_catalog_presentation
    from application.composition_factory import create_agent_composition
    from application.sse_mapping import canonical_output_to_sse_chunk
    from purra.contracts import ExecutionState, ToolBatchRequest, ToolCall
    from purra.operations import AgentOperationController
    from purra.tools import InMemoryToolCatalog
    from purra.tools.executor import CoreToolExecutor

    db, service = setup
    async def search(*args):
        return [SearchHit(f"来源{i + 1}", f"https://example.com/{i}", "摘要", "fixture") for i in range(count)], []
    service.search_provider.search = search
    composition = create_agent_composition(db)
    catalog = enforce_agent_tool_catalog_presentation("batch-web", with_web_research_tools(InMemoryToolCatalog(()), service))
    executor = CoreToolExecutor(catalog, operation_controller=AgentOperationController(composition.output_processor))
    state = ExecutionState(run_id="web-run")
    class Sink:
        async def emit(self, event):
            pass
    async def execute(name, call_id, args):
        result = await executor.execute_batch(ToolBatchRequest(
            run_id="web-run", invocation_id="batch-wire", state=state,
            allowed_tool_names=frozenset({"searchWeb", "readWebPages"}),
            calls=(ToolCall(id=call_id, name=name, arguments_json=json.dumps(args)),),
        ), Sink())
        return json.loads(result.results[0].content)
    try:
        search_result = await execute("searchWeb", "find", {"query": "资料", "maxResults": count})
        args = {"pages": [{"sourceId": r["sourceId"]} for r in search_result["results"]]}
        first = await execute("readWebPages", "read", args)
        await execute("readWebPages", "continue", {"pages": [{"sourceId": r["sourceId"], "offset": r["nextOffset"]} for r in first["results"]]})
        events = await composition.output_repository.list_events("web-run", after_sequence=0)
        wire = [canonical_output_to_sse_chunk(event) for event in events]
        names = "、".join(f"来源{i + 1}" for i in range(count))
        expected = ["搜索公开网页“资料”", *[
            f"{action} 1 个网页（{names}）" if count == 1 else f"{action} {names} 等 {count} 个网页"
            for action in ("读取", "续读")
        ]]
        script = """
import fs from 'node:fs';
import assert from 'node:assert/strict';
const { initialCanonicalOutputState, reduceCanonicalOutput } = await import(process.argv[1]);
const { buildAssistantTimeline } = await import(process.argv[2]);
const { wire, expected } = JSON.parse(fs.readFileSync(0, 'utf8'));
let state = initialCanonicalOutputState();
let started = 0;
const labels = () => buildAssistantTimeline({ role: 'assistant', content: '', canonicalOutput: state }, { messageIndex: 0 }).filter(p => p.type === 'operation').map(p => p.label);
for (const event of wire) {
  state = reduceCanonicalOutput(state, event);
  if (event.kind === 'operation.started') assert.equal(labels().at(-1), expected[started++]);
}
assert.deepEqual(labels(), expected);
for (const event of wire) state = reduceCanonicalOutput(state, event);
assert.deepEqual(labels(), expected);
"""
        root = Path(__file__).resolve().parents[2]
        completed = subprocess.run([
            "node", "--experimental-strip-types", "--input-type=module", "-e", script,
            (root / "src/agent-runtime/canonicalOutput.ts").as_uri(),
            (root / "src/components/AgentConversation/AssistantOutput/timeline.ts").as_uri(),
        ], input=json.dumps({"wire": wire, "expected": expected}, ensure_ascii=False), text=True, capture_output=True, timeout=15)
        assert completed.returncode == 0, completed.stderr
        assert service.reader.calls == count
    finally:
        await composition.shutdown()
