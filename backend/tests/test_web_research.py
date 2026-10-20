from __future__ import annotations

import asyncio
import json
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
