from __future__ import annotations

import asyncio
import json
import socket
import threading
from dataclasses import replace
from types import SimpleNamespace

import pytest
import pytest_asyncio

from agents.shared.web_research_tools import with_web_research_tools
from application.agent_tool_presentation import validate_agent_tool_catalog_presentation
from application.web_research import WEB_RESEARCH_POLICY, WebResearchService
from database.connection import DatabaseConnection
from domains.web_research import SearchHit, WebPage, WebResearchError, public_url
from infrastructure.persistence.run_store import create_run
from infrastructure.web_research import FreeWebSearch, PublicPageReader, PublicResolver, _bounded_sync, parse_search_feed, coverage_warnings
from purra.cancellation import OperationCanceled
from purra.contracts import AgentMessage, AgentRunRequest, DomainContext, ExecutionState, ModelRequest, RunBinding, ToolCall
from purra.tools import InMemoryToolCatalog


class Search:
    calls = 0

    async def search(self, query, language, max_results):
        self.calls += 1
        return [SearchHit("公开资料", "https://example.com/article", "检索摘要，不是正文", "bing")], []


class Reader:
    calls = 0

    async def read(self, url):
        self.calls += 1
        return WebPage("https://example.com/final", "公开正文内容。" * 1_000)


@pytest_asyncio.fixture
async def setup(tmp_path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    await db.execute("INSERT INTO settings (key, value) VALUES ('web_search_enabled', 'true')")
    await create_run(db, run_id="web-run", session_id=None, prompt="公开资料查询", mode="agent", binding=RunBinding(
        namespace="test.web", aggregate_id="book-1", command_id="web-1", attributes={
            "domainNamespace": "purrtypos.writing", "webResearch": {"version": 1, "enabled": True},
        },
    ))
    service = WebResearchService(db, search=Search(), reader=Reader())
    try:
        yield db, service
    finally:
        await db.close()


def request(**kwargs):
    return AgentRunRequest(
        messages=(AgentMessage(role="user", content="查公开资料"),),
        model=ModelRequest(provider="fixture", model="model"),
        domain_context=DomainContext(namespace=kwargs.pop("namespace", "purrtypos.writing")),
        tools_enabled=kwargs.pop("tools_enabled", True), **kwargs,
    )


@pytest.mark.asyncio
async def test_explicit_grant_and_tool_catalog_are_persistable(setup):
    db, service = setup
    prepared = await service.prepare_request(request())
    assert prepared.metadata["webResearch"] == {"version": 1, "enabled": True}
    assert any(m.content == WEB_RESEARCH_POLICY for m in prepared.messages)
    catalog = with_web_research_tools(InMemoryToolCatalog(()), service)
    validate_agent_tool_catalog_presentation("web-test", catalog)
    assert set(catalog.enabled_names(prepared)) == {"searchWeb", "readWebPages"}
    assert "readWebPage" not in {item.schema.name for item in catalog.registrations()}
    assert catalog.enabled_names(request()) == frozenset()
    result = await catalog.get("searchWeb").call_handler(
        ExecutionState(run_id="web-run"), {"queries": [{"query": "气象"}]}, ToolCall(id="call-1", name="searchWeb", arguments_json='{"queries":[{"query":"气象"}]}'),
    )
    assert json.loads(result.content)["results"][0]["sourceId"]
    assert result.error_code is None


@pytest.mark.asyncio
async def test_registered_web_tool_executes_through_purra_gateway(setup):
    from purra.contracts import ToolBatchRequest, ToolBatchOutcome
    from purra.testing import assert_tool_execution_gateway_conforms
    from purra.tools.executor import CoreToolExecutor
    _, service = setup
    prepared = await service.prepare_request(request())
    catalog = with_web_research_tools(InMemoryToolCatalog(()), service)
    result, _ = await assert_tool_execution_gateway_conforms(
        gateway=CoreToolExecutor(catalog),
        request=ToolBatchRequest(
            run_id="web-run", invocation_id="web-invocation",
            calls=(ToolCall(id="native-call", name="searchWeb", arguments_json='{"queries":[{"query":"公开资料"}]}'),),
            allowed_tool_names=frozenset(catalog.enabled_names(prepared)),
            state=ExecutionState(run_id="web-run"),
        ),
    )
    assert result.outcome is ToolBatchOutcome.COMPLETED
    assert json.loads(result.results[0].content)["success"] is True
    assert service.search_provider.calls == 1


@pytest.mark.asyncio
async def test_web_operation_details_survive_checkpoint_roundtrip_without_page_body(setup):
    from application.agent_tool_presentation import enforce_agent_tool_catalog_presentation
    from purra.contracts import ToolBatchRequest, ToolBatchOutcome
    from purra.json_values import freeze_json_mapping, thaw_json_mapping
    from purra.operations import AgentOperationController
    from purra.tools.executor import CoreToolExecutor

    class Output:
        def __init__(self):
            self.events = []

        async def accept_operation_event(self, event):
            self.events.append(event)
            return event

    class Sink:
        async def emit(self, event):
            pass

    _, service = setup
    catalog = enforce_agent_tool_catalog_presentation(
        "web-test", with_web_research_tools(InMemoryToolCatalog(()), service),
    )
    output = Output()
    executor = CoreToolExecutor(catalog, operation_controller=AgentOperationController(output))

    async def execute(state, name, call_id, arguments):
        result = await executor.execute_batch(ToolBatchRequest(
            run_id="web-run", invocation_id="display-test", state=state,
            calls=(ToolCall(id=call_id, name=name, arguments_json=json.dumps(arguments)),),
            allowed_tool_names=frozenset({"searchWeb", "readWebPages"}),
        ), Sink())
        assert result.outcome is ToolBatchOutcome.COMPLETED
        return json.loads(result.results[0].content)

    state = ExecutionState(run_id="web-run")
    search = await execute(state, "searchWeb", "display-search", {"queries": [{"query": "杭州\n 今日天气"}], "maxResults": 1})
    assert output.events[0].display["labelParams"]["toolArguments"] == {"queries": [{"query": "杭州 今日天气", "language": "zh"}], "researchMode": "standard"}
    # The same thawed state shape used when restoring a persisted checkpoint.
    restored = ExecutionState(run_id="web-run", domain=thaw_json_mapping(freeze_json_mapping(state.domain)))
    source_id = search["results"][0]["sourceId"]
    batch = await execute(restored, "readWebPages", "display-read", {"pages": [{"sourceId": source_id}]})
    page = batch["results"][0]
    assert output.events[2].display["labelParams"]["toolArguments"] == {
        "pages": [{"title": "公开资料", "host": "example.com", "continuation": False}],
    }
    await execute(restored, "readWebPages", "display-next", {"pages": [{"sourceId": source_id, "offset": page["nextOffset"]}]})
    assert output.events[4].display["labelParams"]["toolArguments"]["pages"][0]["continuation"] is True
    assert "公开正文内容" not in json.dumps(restored.domain, ensure_ascii=False)
    assert "检索摘要" not in json.dumps(restored.domain, ensure_ascii=False)
    assert source_id not in json.dumps(thaw_json_mapping(output.events[2].display))
    assert service.reader.calls == 1


@pytest.mark.asyncio
async def test_web_display_projection_bounds_untrusted_text_and_hides_unknown_source_ids(setup):
    from purra.json_values import freeze_json_mapping
    _, service = setup
    catalog = with_web_research_tools(InMemoryToolCatalog(()), service)
    state = ExecutionState(run_id="web-run")
    params = catalog.get("searchWeb").operation_display_params(
        state, freeze_json_mapping({"queries": [{"query": "x" * 300, "language": "en"}], "maxResults": 5}), None,
    )
    assert len(params["toolArguments"]["queries"][0]["query"]) == 120
    assert set(params["toolArguments"]) == {"queries", "researchMode"}
    params = catalog.get("readWebPages").operation_display_params(
        state, freeze_json_mapping({"pages": [{"sourceId": "unknown-opaque-id"}]}), None,
    )
    assert params["toolArguments"] == {"pages": [{"title": "", "host": "", "continuation": False}]}


@pytest.mark.asyncio
@pytest.mark.parametrize("values", [
    {"tools_enabled": False}, {"namespace": "purrtypos.novel_analysis"},
    {"metadata": {"parentRunId": "root"}}, {"metadata": {"responseAudience": "internal"}},
    {"namespace": "purrtypos.screenplay", "mode": "screenplay_generate"},
])
async def test_internal_analysis_and_toolless_requests_never_gain_web_access(setup, values):
    _, service = setup
    prepared = await service.prepare_request(request(**values))
    assert prepared.metadata.get("webResearch") is None
    assert not with_web_research_tools(InMemoryToolCatalog(()), service).enabled_names(prepared)


@pytest.mark.asyncio
async def test_resume_does_not_retrofit_access_and_current_disable_revokes_calls(setup):
    db, service = setup
    await create_run(db, run_id="old-run", session_id=None, prompt="old", mode="agent")
    assert (await service.prepare_request(request(), run_id="old-run")).metadata.get("webResearch") is None
    resumed = await service.prepare_request(request(), run_id="web-run")
    assert resumed.metadata["webResearch"]["enabled"]
    await db.execute("UPDATE settings SET value = 'false' WHERE key = 'web_search_enabled'")
    with pytest.raises(WebResearchError, match="关闭"):
        await service.search("web-run", "disabled", {"queries": [{"query": "公开资料"}]})
    assert service.search_provider.calls == 0


@pytest.mark.asyncio
async def test_search_replay_has_stable_sources_and_no_second_request(setup):
    _, service = setup
    first = await service.search("web-run", "lookup", {"queries": [{"query": "气象", "language": "zh"}]})
    again = await service.search("web-run", "lookup", {"queries": [{"query": "气象", "language": "zh"}]})
    assert first == again
    assert first["results"][0]["evidenceKind"] == "search_excerpt"
    assert first["results"][0]["publishedAt"] is None
    assert service.search_provider.calls == 1
    with pytest.raises(WebResearchError, match="不一致"):
        await service.search("web-run", "lookup", {"queries": [{"query": "改掉参数"}]})


@pytest.mark.asyncio
async def test_page_pagination_is_local_stable_and_scoped(setup):
    db, service = setup
    search = await service.search("web-run", "s1", {"queries": [{"query": "气象"}]})
    source_id = search["results"][0]["sourceId"]
    batch = await service.read_pages("web-run", "r1", {"pages": [{"sourceId": source_id}]})
    first = batch["results"][0]
    assert first["url"] == "https://example.com/final"
    assert first["evidenceKind"] == "page_extract"
    assert len(first["content"]) == 4_000
    assert first["nextOffset"] == 4_000
    assert batch == await service.read_pages("web-run", "r1", {"pages": [{"sourceId": source_id}]})
    second = (await service.read_pages("web-run", "r2", {"pages": [{"sourceId": source_id, "offset": 4_000}]}))["results"][0]
    assert first["contentRevisionId"] == second["contentRevisionId"]
    assert service.reader.calls == 1
    count = await db.fetch_one("SELECT COUNT(*) AS n FROM web_research_attempts WHERE kind = 'read' AND external_request = 1")
    assert count["n"] == 1
    with pytest.raises(WebResearchError, match="不一致"):
        await service.read_pages("web-run", "s1", {"pages": [{"sourceId": source_id}]})
    with pytest.raises(WebResearchError):
        await service.read_pages("other-run", "r3", {"pages": [{"sourceId": source_id}]})
    await db.execute("DELETE FROM ai_agent_runs WHERE id = 'web-run'")
    assert await db.fetch_all("SELECT * FROM web_research_sources") == []
    assert await db.fetch_all("SELECT * FROM web_research_attempts") == []
    assert await db.fetch_all("SELECT * FROM web_research_run_limits") == []


@pytest.mark.asyncio
async def test_atomic_budget_survives_new_service_instances(setup):
    db, service = setup
    other = WebResearchService(db, search=Search(), reader=Reader())
    results = await asyncio.gather(*[
        (service if i % 2 else other).search("web-run", f"parallel-{i}", {"queries": [{"query": "公开资料"}]}) for i in range(10)
    ], return_exceptions=True)
    assert sum(isinstance(result, dict) and result["success"] for result in results) == 6
    assert sum(isinstance(result, dict) and result.get("code") == "web_budget_exhausted" for result in results) == 4


@pytest.mark.asyncio
async def test_failed_search_is_an_observation_and_retains_budget(setup):
    db, service = setup
    async def failing(*args):
        raise WebResearchError("web_search_unavailable", "受限流影响")
    service.search_provider.search = failing
    payload = await service.search("web-run", "fail", {"queries": [{"query": "公开资料"}]})
    assert payload["success"] is False and payload["code"] == "web_search_unavailable"
    assert payload["results"] == []
    assert payload == await service.search("web-run", "fail", {"queries": [{"query": "公开资料"}]})
    assert (await db.fetch_one("SELECT COUNT(*) AS n FROM web_research_attempts WHERE kind = 'search' AND external_request = 1"))["n"] == 1


@pytest.mark.asyncio
async def test_cancel_does_not_record_a_fake_result_or_blindly_resend(setup):
    db, service = setup
    started, stop = asyncio.Event(), asyncio.Event()
    async def waiting(*args):
        started.set()
        await asyncio.Event().wait()
    service.search_provider.search = waiting
    task = asyncio.create_task(service.search("web-run", "cancel", {"queries": [{"query": "公开资料"}]}, stop))
    await started.wait()
    stop.set()
    with pytest.raises(OperationCanceled):
        await task
    assert (await db.fetch_one("SELECT result_json FROM web_research_attempts"))["result_json"] is None
    with pytest.raises(WebResearchError, match="中断"):
        await service.search("web-run", "cancel", {"queries": [{"query": "公开资料"}]})


@pytest.mark.parametrize("url", [
    "http://localhost/", "http://127.0.0.1/", "http://[::1]/", "http://[::ffff:127.0.0.1]/",
    "http://169.254.169.254/", "http://10.0.0.1/", "http://192.168.1.1/", "http://[fc00::1]/",
    "http://2130706433/", "http://0177.0.0.1/", "http://0x7f.0.0.1/", "file:///etc/passwd",
    "javascript:alert(1)", "https://user:password@example.com/", "https://example.com:18321/",
    "http://machine.local/", "http://localhost./", "https://example.com/\nprivate",
])
def test_public_url_blocks_internal_addresses_and_unsafe_schemes(url):
    with pytest.raises(WebResearchError):
        public_url(url)


@pytest.mark.asyncio
async def test_dns_answers_are_pinned_and_mixed_private_answers_rejected(monkeypatch):
    loop = asyncio.get_running_loop()
    async def public(*args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
    monkeypatch.setattr(loop, "getaddrinfo", public)
    rows = await PublicResolver().resolve("public.example", 443)
    assert rows[0]["host"] == "93.184.216.34"
    assert rows[0]["flags"] == socket.AI_NUMERICHOST
    async def mixed(*args, **kwargs):
        return [*(await public()), (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]
    monkeypatch.setattr(loop, "getaddrinfo", mixed)
    with pytest.raises(WebResearchError):
        await PublicResolver().resolve("public.example", 443)


def test_ddgs_uses_explicit_free_backends_and_validates_results(monkeypatch):
    import ddgs
    calls = []
    class DDGS:
        def __init__(self, **kwargs):
            assert kwargs == {"timeout": 6}
        def text(self, query, **kwargs):
            calls.append(kwargs)
            if kwargs["backend"] == "duckduckgo":
                raise RuntimeError("provider failed")
            return [
                {"href": "http://127.0.0.1/", "title": "blocked"},
                {"href": "https://example.com/a", "body": "extracted search excerpt"},
                {"href": "https://example.com/a", "title": "duplicate"},
            ]
    monkeypatch.setattr(ddgs, "DDGS", DDGS)
    hits, warnings = FreeWebSearch._search("历史", "zh", 5)
    assert [item["backend"] for item in calls] == ["duckduckgo", "brave"]
    assert calls[0]["region"] == "cn-zh"
    assert len(hits) == 1 and hits[0].backend == "brave" and warnings


def test_rss_parser_keeps_source_evidence_and_filters_unsafe_links():
    body = b'''<?xml version="1.0"?><rss version="2.0"><channel><title>Search</title>
    <item><title>unsafe</title><link>http://127.0.0.1/</link></item>
    <item><title>Official &amp; useful</title><link>https://example.com/a</link><description>Public evidence</description></item>
    <item><title>Duplicate</title><link>https://example.com/a</link></item>
    </channel></rss>'''
    hits = parse_search_feed(body, 5)
    assert len(hits) == 1 and hits[0].backend == "bing_rss"
    assert hits[0].title == "Official & useful"
    assert hits[0].excerpt == "Public evidence"
    with pytest.raises(WebResearchError):
        parse_search_feed(b"<html><body>captcha</body></html>", 5)


def test_partial_keyword_coverage_is_not_presented_as_confirmed_evidence():
    hit = SearchHit("故宫博物院", "https://example.com/", "故宫的参观信息", "bing_rss")
    assert coverage_warnings("故宫博物院 榫卯 家具", [hit])
    specific = replace(hit, title="故宫博物院的家具榫卯工艺")
    assert coverage_warnings("故宫博物院 榫卯 家具", [specific]) == []


@pytest.mark.asyncio
async def test_search_prefers_rss_and_only_falls_back_after_failure(monkeypatch):
    provider = FreeWebSearch()
    hit = SearchHit("Source", "https://example.com/a", "evidence", "bing_rss")
    async def rss(*args): return [hit]
    def fallback(*args): raise AssertionError("RSS worked; do not send query elsewhere")
    monkeypatch.setattr(provider, "_rss", rss)
    monkeypatch.setattr(provider, "_search", fallback)
    assert await provider.search("public", "en", 3) == ([hit], [])
    async def failed(*args): raise WebResearchError("web_search_unavailable", "unavailable")
    monkeypatch.setattr(provider, "_rss", failed)
    monkeypatch.setattr(provider, "_search", lambda *args: ([hit], []))
    assert (await provider.search("public", "en", 3))[1]


def test_disabled_ddgs_backends_cannot_trigger_auto_selection(monkeypatch):
    import ddgs
    import ddgs.engines
    class Unexpected:
        def __init__(self, *args, **kwargs): raise AssertionError("disabled engine must not run")
    monkeypatch.setattr(ddgs, "DDGS", Unexpected)
    monkeypatch.setitem(ddgs.engines.ENGINES, "text", {})
    with pytest.raises(WebResearchError):
        FreeWebSearch._search("public", "en", 5)


def test_canceled_native_lookup_does_not_start_next_backend(monkeypatch):
    import ddgs
    stopped = threading.Event()
    calls = []
    class DDGS:
        def __init__(self, **kwargs): pass
        def text(self, query, **kwargs):
            calls.append(kwargs["backend"])
            stopped.set()
            raise RuntimeError("first request ended after cancellation")
    monkeypatch.setattr(ddgs, "DDGS", DDGS)
    with pytest.raises(WebResearchError) as error:
        FreeWebSearch._search("public", "en", 5, stopped)
    assert error.value.code == "web_search_canceled"
    assert calls == ["duckduckgo"]


@pytest.mark.asyncio
async def test_cancelled_native_work_retains_its_capacity_until_finished():
    gate = threading.Event()
    started = threading.Event()
    def blocking():
        started.set()
        gate.wait(3)
        return "finished"
    first = asyncio.create_task(_bounded_sync(blocking))
    second = asyncio.create_task(_bounded_sync(blocking))
    try:
        while not started.is_set():
            await asyncio.sleep(0.001)
        await asyncio.sleep(0)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        with pytest.raises(WebResearchError, match="繁忙"):
            await _bounded_sync(lambda: "must not run")
    finally:
        gate.set()
        await second
        await asyncio.sleep(0)


def install_http(monkeypatch, responses):
    import aiohttp
    calls = []
    class Response:
        def __init__(self, status=200, content_type="text/html", body=b"", headers=None):
            self.status, self.content_type, self.charset = status, content_type, "utf-8"
            self.headers = headers or {}
            self.content = self
            self.body = body
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def iter_chunked(self, size):
            yield self.body
    class Session:
        def __init__(self, **kwargs):
            assert kwargs["trust_env"] is False
            self.connector = kwargs["connector"]
        async def __aenter__(self): return self
        async def __aexit__(self, *args): await self.connector.close()
        def get(self, url, **kwargs):
            assert kwargs["allow_redirects"] is False
            calls.append(url)
            return Response(**responses[len(calls) - 1])
    monkeypatch.setattr(aiohttp, "ClientSession", Session)
    return calls


@pytest.mark.asyncio
async def test_reader_extracts_article_with_real_trafilatura(monkeypatch):
    text = "这是一段有关历史生活和传统工艺的公开研究材料，包含具体步骤、时间和资料背景。" * 20
    html = ("<html><head><title>公开研究</title></head><body><nav>广告导航</nav><article><h1>公开研究</h1>" + f"<p>{text}</p>" * 3 + "</article></body></html>").encode()
    install_http(monkeypatch, [{"body": html}])
    result = await PublicPageReader().read("https://example.com/article")
    assert "历史生活" in result.text and "广告导航" not in result.text


@pytest.mark.asyncio
async def test_redirect_to_localhost_is_blocked_before_second_connection(monkeypatch):
    calls = install_http(monkeypatch, [{"status": 302, "headers": {"Location": "http://127.0.0.1:18321/settings"}}])
    with pytest.raises(WebResearchError):
        await PublicPageReader().read("https://example.com/article")
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("response,code", [
    ({"status": 403}, "web_page_unavailable"),
    ({"content_type": "application/pdf"}, "web_content_unsupported"),
    ({"body": b"a" * 2_000_001}, "web_page_too_large"),
    ({"body": b"<html><body>Login required</body></html>"}, "web_extraction_failed"),
])
async def test_reader_reports_unreadable_and_oversize_pages(monkeypatch, response, code):
    install_http(monkeypatch, [response])
    with pytest.raises(WebResearchError) as error:
        await PublicPageReader().read("https://example.com/article")
    assert error.value.code == code
