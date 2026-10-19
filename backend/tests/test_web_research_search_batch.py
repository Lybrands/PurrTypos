"""Batch searches spend per-query budgets and return a bounded candidate pool."""
from __future__ import annotations

import asyncio
import json

import pytest

from application.web_research import WebResearchService
from domains.web_research import SearchHit, WebResearchError, source_key
from purra.cancellation import OperationCanceled
from tests.test_web_research import setup
from tests.test_web_research_batch import sources, pages


def queries(*items, **options):
    return {"queries": [{"query": query, "language": language} for query, language in items], **options}


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [1, 3])
@pytest.mark.parametrize("maximum", [None, 20])
async def test_queries_run_with_two_workers_and_interleave_bounded_candidates(setup, count, maximum):
    _, service = setup
    started, release = asyncio.Event(), asyncio.Event()
    calls, active, peak = [], 0, 0

    async def search(query, language, max_results):
        nonlocal active, peak
        calls.append((query, language, max_results))
        active += 1
        peak = max(peak, active)
        if active == min(count, 2):
            started.set()
        try:
            await release.wait()
            return [SearchHit(f"{query}-{i}", f"https://example.com/{query}/{i}", "摘要" * 400, "fixture") for i in range(max_results)], []
        finally:
            active -= 1

    service.search_provider.search = search
    options = {} if maximum is None else {"maxResults": maximum}
    args = queries(*[(f"q{i}", "en") for i in range(count)], **options)
    task = asyncio.create_task(service.search("web-run", "bounded", args))
    try:
        await asyncio.wait_for(started.wait(), 2)
        assert len(calls) == min(count, 2)
    finally:
        release.set()
    result = await asyncio.wait_for(task, 3)
    cap = maximum or 10
    assert peak == min(count, 2) and active == 0
    assert len(calls) == count and all(row[2] == cap for row in calls)
    assert [row["query"] for row in result["queries"]] == [f"q{i}" for i in range(count)]
    assert len(result["results"]) == cap
    assert [row["title"] for row in result["results"][:count]] == [f"q{i}-0" for i in range(count)]
    assert all(len(row["excerpt"]) == 300 for row in result["results"])
    assert result["untrusted"]
    assert result["queryBudget"] == {"limit": 6, "used": count, "remaining": 6 - count}
    assert result["readBudget"] == {"limit": 8, "used": 0, "remaining": 8}


@pytest.mark.asyncio
@pytest.mark.parametrize("args", [
    {}, {"query": "retired"}, {"queries": []}, {"queries": None},
    {"queries": [{"query": "a"}] * 4}, {"queries": [None]}, {"queries": [{}]},
    {"queries": [{"query": " "}]}, {"queries": [{"query": "x" * 301}]},
    {"queries": [{"query": "a", "language": "fr"}]},
    {"queries": [{"query": "a", "url": "https://example.com/"}]},
    {"queries": [{"query": "a"}], "maxResults": 21},
    {"queries": [{"query": "a"}], "maxResults": 0},
    {"queries": [{"query": "a"}], "maxResults": True},
    {"queries": [{"query": "a"}], "researchMode": "unlimited"},
    {"queries": [{"query": "a"}], "query": "retired"},
])
async def test_invalid_search_input_spends_no_budget(setup, args):
    db, service = setup
    with pytest.raises(WebResearchError) as error:
        await service.search("web-run", "invalid", args)
    assert error.value.code == "tool_input_invalid"
    assert service.search_provider.calls == 0
    assert await db.fetch_all("SELECT * FROM web_research_attempts") == []
    assert await db.fetch_all("SELECT * FROM web_research_run_limits") == []


@pytest.mark.asyncio
async def test_duplicate_queries_and_tracking_urls_are_deduplicated_without_hiding_shortfall(setup):
    _, service = setup
    calls = []

    async def search(query, language, cap):
        calls.append((query, language))
        return [
            SearchHit("source", f"https://EXAMPLE.com:443/a?id=1&utm_source={language}#anchor", "摘要", "fixture"),
            SearchHit("variant", "https://example.com/a?fbclid=tracking&id=1", "摘要", "fixture"),
            SearchHit("other", f"https://example.com/{language}", "摘要", "fixture"),
        ], []

    service.search_provider.search = search
    result = await service.search("web-run", "duplicates", queries(("same  words", "zh"), ("same\nwords", "zh"), ("same words", "en")))
    assert calls == [("same words", "zh"), ("same words", "en")]
    assert len(result["queries"]) == 3
    assert result["queries"][0] == result["queries"][1]
    assert len(result["results"]) == 3
    assert result["results"][0]["url"] == "https://EXAMPLE.com:443/a?id=1&utm_source=zh#anchor"
    assert result["warnings"] and result["queryBudget"]["used"] == 2
    assert source_key("https://example.com/a?id=1") != source_key("https://example.com/a?id=2")


@pytest.mark.asyncio
async def test_partial_failure_is_sanitized_and_failed_query_still_counts(setup):
    _, service = setup

    async def search(query, language, cap):
        if query == "bad":
            raise RuntimeError("private diagnostic secret")
        return [SearchHit("good", "https://example.com/a", "摘要", "fixture")], []

    service.search_provider.search = search
    result = await service.search("web-run", "partial", queries(("bad", "zh"), ("good", "en")))
    assert result["success"] and [row["success"] for row in result["queries"]] == [False, True]
    assert len(result["results"]) == 1 and result["queryBudget"]["used"] == 2
    assert "private diagnostic secret" not in json.dumps(result)
    assert result == await service.search("web-run", "partial", queries(("bad", "zh"), ("good", "en")))


@pytest.mark.asyncio
async def test_six_actual_queries_cannot_be_bypassed_by_concurrent_batches_or_restart(setup):
    db, service = setup
    other = WebResearchService(db, search=service.search_provider)
    args = queries(("one", "zh"), ("two", "en"), ("three", "zh"))
    results = await asyncio.gather(*[(service if i % 2 else other).search("web-run", f"batch-{i}", args) for i in range(3)])
    assert sum(row["success"] for result in results for row in result["queries"]) == 6
    assert sum(row.get("code") == "web_budget_exhausted" for result in results for row in result["queries"]) == 3
    assert service.search_provider.calls == 6
    replay = await other.search("web-run", "batch-0", args)
    assert replay == results[0] and service.search_provider.calls == 6
    assert (await other.repository.budgets("web-run"))["queryBudget"]["remaining"] == 0


@pytest.mark.asyncio
async def test_replay_checks_order_and_mode_without_network_and_disable_revokes_even_replay(setup):
    db, service = setup
    args = queries(("one", "zh"), ("two", "en"), researchMode="extended")
    first = await service.search("web-run", "replay", args)
    other = WebResearchService(db, search=service.search_provider)
    assert await other.search("web-run", "replay", args) == first
    assert service.search_provider.calls == 2
    for changed in ({**args, "queries": list(reversed(args["queries"]))}, {**args, "researchMode": "standard"}, {**args, "maxResults": 20}):
        with pytest.raises(WebResearchError, match="不一致"):
            await other.search("web-run", "replay", changed)
    await db.execute("UPDATE settings SET value='false' WHERE key='web_search_enabled'")
    with pytest.raises(WebResearchError, match="关闭"):
        await other.search("web-run", "replay", args)


@pytest.mark.asyncio
async def test_extended_read_limit_is_durable_monotonic_and_hard_capped_with_cached_reads(setup):
    db, service = setup
    rows = await sources(service, 14)
    await service.read_pages("web-run", "first-five", pages(rows[:5]))
    await service.read_pages("web-run", "next-three", pages(rows[5:8]))
    blocked = await service.read_pages("web-run", "blocked", pages(rows[8:9]))
    assert blocked["results"][0]["code"] == "web_budget_exhausted"
    extended = await service.search("web-run", "upgrade", queries(("independent evidence", "en"), researchMode="extended"))
    assert extended["readBudget"] == {"limit": 12, "used": 8, "remaining": 4}
    other = WebResearchService(db, search=service.search_provider, reader=service.reader)
    await other.search("web-run", "standard-after-upgrade", queries(("followup", "zh")))
    last = await other.read_pages("web-run", "last-five", pages(rows[8:13]))
    assert sum(row["success"] for row in last["results"]) == 4
    assert last["readBudget"] == {"limit": 12, "used": 12, "remaining": 0}
    assert service.reader.calls == 12
    cached = await other.read_pages("web-run", "cached", {"pages": [{"sourceId": rows[0]["id"], "offset": 1600}]})
    assert cached["results"][0]["success"] and service.reader.calls == 12
    await db.execute("DELETE FROM ai_agent_runs WHERE id='web-run'")
    assert await db.fetch_all("SELECT * FROM web_research_run_limits") == []


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_mode", ["signal", "task"])
async def test_search_cancel_drains_active_workers_and_does_not_start_third_or_blindly_replay(setup, cancel_mode):
    db, service = setup
    started, stop = asyncio.Event(), asyncio.Event()
    calls, active = [], set()

    async def search(query, *_args):
        calls.append(query)
        active.add(query)
        if len(active) == 2:
            started.set()
        try:
            await asyncio.Event().wait()
        finally:
            active.remove(query)

    service.search_provider.search = search
    args = queries(("one", "zh"), ("two", "en"), ("three", "zh"))
    task = asyncio.create_task(service.search("web-run", "cancel", args, stop))
    await asyncio.wait_for(started.wait(), 2)
    if cancel_mode == "signal":
        stop.set()
    else:
        task.cancel()
    with pytest.raises(OperationCanceled if cancel_mode == "signal" else asyncio.CancelledError):
        await asyncio.wait_for(task, 2)
    assert len(calls) == 2 and not active
    assert all(row["result_json"] is None for row in await db.fetch_all("SELECT * FROM web_research_attempts"))
    assert (await service.repository.budgets("web-run"))["queryBudget"]["used"] == 2
    with pytest.raises(WebResearchError, match="中断"):
        await service.search("web-run", "cancel", args)
    assert len(calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("args", [
    {"query": "retired"}, {"queries": []}, {"queries": [{"query": "a"}] * 4},
    {"queries": [{"query": "a"}], "maxResults": 21},
    {"queries": [{"query": "a"}], "researchMode": "unlimited"},
])
async def test_gateway_rejects_old_schema_and_oversize_search_before_service(setup, args):
    from agents.shared.web_research_tools import with_web_research_tools
    from purra.contracts import ExecutionState, ToolBatchRequest, ToolCall, ToolBatchOutcome
    from purra.tools import InMemoryToolCatalog
    from purra.tools.executor import CoreToolExecutor
    db, service = setup

    class Sink:
        async def emit(self, event):
            pass

    result = await CoreToolExecutor(with_web_research_tools(InMemoryToolCatalog(()), service)).execute_batch(ToolBatchRequest(
        run_id="web-run", state=ExecutionState(run_id="web-run"), allowed_tool_names=frozenset({"searchWeb"}),
        calls=(ToolCall(id="invalid", name="searchWeb", arguments_json=json.dumps(args)),),
    ), Sink())
    assert result.outcome is ToolBatchOutcome.FAILED and result.results[0].error
    assert service.search_provider.calls == 0
    assert await db.fetch_all("SELECT * FROM web_research_attempts") == []


@pytest.mark.asyncio
async def test_schema_upgrade_preserves_receipts_and_replaces_run_cleanup_trigger(setup):
    from database.web_research_schema import init_web_research_schema
    db, service = setup
    first = await service.search("web-run", "upgrade-schema", queries(("evidence", "en"), researchMode="extended"))
    # Simulate an installed database with the old two-table cleanup trigger.
    await db.execute("DROP TRIGGER web_research_delete_run")
    await db.execute("CREATE TRIGGER web_research_delete_run AFTER DELETE ON ai_agent_runs BEGIN DELETE FROM web_research_sources WHERE run_id=OLD.id; DELETE FROM web_research_attempts WHERE run_id=OLD.id; END")
    await init_web_research_schema(db)
    await init_web_research_schema(db)
    other = WebResearchService(db, search=service.search_provider)
    assert await other.search("web-run", "upgrade-schema", queries(("evidence", "en"), researchMode="extended")) == first
    assert await other.repository.read_limit("web-run") == 12
    assert service.search_provider.calls == 1
    await db.execute("DELETE FROM ai_agent_runs WHERE id='web-run'")
    for table in ("web_research_sources", "web_research_attempts", "web_research_run_limits"):
        assert await db.fetch_all(f"SELECT * FROM {table}") == []
