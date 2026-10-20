from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from application.web_research import WebResearchService
from domains.web_research import API_KEY_SETTING, SearchHit, WebResearchError, WebPage
from infrastructure.deepseek_web_search import DeepSeekWebSearch, DeepSeekPageReader, parse_search_response
from purra.cancellation import OperationCanceled
from tests.test_web_research import setup

SECRET = "sk-fixture-not-a-real-key"


def result(**changes):
    return {"title": "官方资料", "url": "https://example.com/article", "summary": "可供核对的摘要", "snippet": "短摘要",
            "date": "2026-10-18T10:00:00+08:00", "rerank_score": 2.7, "authority": 4, **changes}


def mock_http(monkeypatch, handler):
    original = httpx.AsyncClient
    def client(**options):
        assert options["trust_env"] is False and options["follow_redirects"] is False
        assert options["timeout"].connect == 5
        return original(transport=httpx.MockTransport(handler), **options)
    monkeypatch.setattr("infrastructure.deepseek_web_search.httpx.AsyncClient", client)


@pytest.mark.asyncio
async def test_official_search_contract_auth_count_metadata_and_no_full_body(monkeypatch):
    calls = []
    def handler(request):
        calls.append(request)
        assert str(request.url) == "https://api.deepseek.com/v1/tools/search"
        assert request.method == "POST" and request.headers["authorization"] == f"Bearer {SECRET}"
        assert json.loads(request.content) == {"query": "古籍修复 lang:zh", "count": 20, "need_content": False}
        return httpx.Response(200, json={"results": [result(content="PRIVATE LARGE BODY")]})
    mock_http(monkeypatch, handler)
    hits, warnings = await DeepSeekWebSearch(SECRET).search("古籍修复 lang:zh", "zh", 20)
    assert len(calls) == 1 and not warnings
    assert hits[0].backend == "deepseek" and hits[0].published_at == "2026-10-18T02:00:00+00:00"
    assert hits[0].relevance_score == 2.7 and hits[0].authority == 4
    assert "PRIVATE LARGE BODY" not in repr(hits) and SECRET not in repr(hits)


@pytest.mark.parametrize("field,value,expected", [
    ("date", None, None), ("date", "yesterday", None), ("date", "2026-10-18", None),
    ("rerank_score", True, None), ("rerank_score", 10**1000, None), ("rerank_score", -1, None),
    ("rerank_score", 3, 3.0), ("authority", True, None), ("authority", 5, None), ("authority", -1, -1),
])
def test_provider_metadata_is_optional_and_validated(field, value, expected):
    hits, _ = parse_search_response(json.dumps({"results": [result(**{field: value})]}), 10)
    attr = {"date": "published_at", "rerank_score": "relevance_score", "authority": "authority"}[field]
    assert getattr(hits[0], attr) == expected


def test_parser_preserves_order_filters_ssrf_deduplicates_and_distinguishes_no_match():
    hits, warnings = parse_search_response(json.dumps({"results": [
        result(url="http://127.0.0.1/"), None, result(url="https://example.com/a?utm_source=x"),
        result(url="https://example.com/a?utm_source=y"), result(url="https://example.com/b"),
    ]}), 1)
    assert len(hits) == 1 and hits[0].url.endswith("utm_source=x") and warnings
    assert parse_search_response('{"results":[]}', 10) == ([], [])
    for body in ('{"results":{}}', '{"error":"PRIVATE"}', 'not json', '{"results":[{"url":"http://localhost/"}]}'):
        with pytest.raises(WebResearchError):
            parse_search_response(body, 10)


@pytest.mark.asyncio
@pytest.mark.parametrize("status,code", [(401,"web_search_auth_failed"),(402,"web_search_insufficient_balance"),(429,"web_search_rate_limited"),(500,"web_search_unavailable"),(302,"web_search_unavailable")])
async def test_http_errors_are_sanitized_and_never_follow_redirects(monkeypatch, status, code):
    mock_http(monkeypatch, lambda request: httpx.Response(status, headers={"Location":"https://evil.example/"}, json={"error":{"message":SECRET}}))
    with pytest.raises(WebResearchError) as error:
        await DeepSeekWebSearch(SECRET).search("query", "en", 10)
    assert error.value.code == code and SECRET not in str(error.value)


@pytest.mark.asyncio
async def test_timeout_and_response_size_are_bounded(monkeypatch):
    def timeout(request):
        raise httpx.ReadTimeout(SECRET, request=request)
    mock_http(monkeypatch, timeout)
    with pytest.raises(WebResearchError) as error:
        await DeepSeekWebSearch(SECRET).search("query", "en", 10)
    assert error.value.code == "web_search_timeout" and SECRET not in str(error.value)
    # Restore the class so the second mock does not recursively wrap the first.
    monkeypatch.undo()
    mock_http(monkeypatch, lambda request: httpx.Response(200, content=b" "*2_000_001))
    with pytest.raises(WebResearchError, match="大小上限"):
        await DeepSeekWebSearch(SECRET).search("query", "en", 10)


@pytest.mark.asyncio
async def test_query_limit_does_not_silently_truncate_or_call_http(monkeypatch):
    def forbidden(_request):
        raise AssertionError("overlong queries must not reach the API")
    mock_http(monkeypatch, forbidden)
    with pytest.raises(WebResearchError, match="128"):
        await DeepSeekWebSearch(SECRET).search("x"*129,"en",10)


async def configure(db, key=SECRET):
    await db.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", [API_KEY_SETTING, key])


@pytest.mark.asyncio
async def test_only_deepseek_routes_with_per_batch_key_snapshot_and_replay(setup, monkeypatch):
    db, _ = setup
    await configure(db)
    # A legacy free preference must never select the deleted implementation.
    await db.execute("INSERT INTO settings(key,value) VALUES('web_search_provider','free')")
    requests = []
    async def handler(request):
        requests.append(request)
        await configure(db, "sk-next-batch")
        return httpx.Response(200, json={"results": [result()]})
    mock_http(monkeypatch, handler)
    service = WebResearchService(db)
    args = {"queries": [{"query": "资料"}, {"query": "sources lang:en", "language": "en"}]}
    found = await service.search("web-run", "routed", args)
    assert found["success"] and found["searchProvider"] == "deepseek"
    assert "fallbackReason" not in found
    assert len(requests) == 2 and all(r.headers["authorization"] == f"Bearer {SECRET}" for r in requests)
    assert found["queryBudget"]["used"] == 2
    await configure(db, "")
    assert await WebResearchService(db).search("web-run", "routed", args) == found
    assert SECRET not in json.dumps(await db.fetch_all("SELECT * FROM web_research_attempts"))
    assert SECRET not in repr(await service.repository.search_configuration())
    stored = (await db.fetch_all("SELECT * FROM web_research_sources"))[0]
    assert stored["published_at"] == found["results"][0]["publishedAt"]
    assert stored["authority"] == 4 and found["results"][0]["rerankScore"] == 2.7


@pytest.mark.asyncio
@pytest.mark.parametrize("key,status,code,used", [
    ("", None, "web_search_key_missing", 0),
    (SECRET, 401, "web_search_auth_failed", 1),
    (SECRET, 402, "web_search_insufficient_balance", 1),
    (SECRET, 429, "web_search_rate_limited", 1),
])
async def test_configuration_and_api_failures_never_fallback_or_retry(setup, monkeypatch, key, status, code, used):
    db, _ = setup
    await configure(db, key)
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, json={"error": {"message": SECRET}})
    mock_http(monkeypatch, handler)
    service = WebResearchService(db)
    args = {"queries": [{"query": "query"}]}
    found = await service.search("web-run", "bad-key", args)
    assert not found["success"] and found["code"] == code
    assert found["queryBudget"]["used"] == used and len(calls) == used
    assert SECRET not in json.dumps(found)
    assert await service.search("web-run", "bad-key", args) == found
    assert len(calls) == used


@pytest.mark.asyncio
async def test_deepseek_cancel_propagates_and_replay_cannot_charge_again(setup,monkeypatch):
    db,_=setup
    await configure(db)
    started,stop=asyncio.Event(),asyncio.Event()
    calls=[]
    async def handler(request):
        calls.append(request)
        started.set()
        await asyncio.Event().wait()
    mock_http(monkeypatch,handler)
    service=WebResearchService(db)
    args={"queries":[{"query":"query"}]}
    task=asyncio.create_task(service.search("web-run","cancel-ds",args,stop))
    await asyncio.wait_for(started.wait(),2)
    stop.set()
    with pytest.raises(OperationCanceled):
        await asyncio.wait_for(task,2)
    with pytest.raises(WebResearchError,match="中断"):
        await service.search("web-run","cancel-ds",args)
    assert len(calls)==1


@pytest.mark.asyncio
async def test_overlong_deepseek_query_is_rejected_before_spending_external_budget(setup,monkeypatch):
    db,_=setup
    await configure(db)
    def forbidden(request):
        raise AssertionError('invalid queries must not reach the provider')
    mock_http(monkeypatch,forbidden)
    with pytest.raises(WebResearchError) as error:
        await WebResearchService(db).search('web-run','overlong',{'queries':[{'query':'x'*129}]})
    assert error.value.code == 'tool_input_invalid'
    assert (await WebResearchService(db).repository.budgets('web-run'))['queryBudget']=={'limit':6,'used':0,'remaining':6}
    assert await db.fetch_all("SELECT * FROM web_research_attempts WHERE external_request=1")==[]


@pytest.mark.asyncio
@pytest.mark.parametrize("query,language,expected", [
    ("公开资料", "zh", "公开资料 lang:zh"),
    ("documentation", "en", "documentation lang:en"),
    ("资料 lang:zh,en", "en", "资料 lang:zh,en"),
])
async def test_language_is_forwarded_as_official_search_syntax(monkeypatch, query, language, expected):
    def handler(request):
        assert json.loads(request.content)["query"] == expected
        return httpx.Response(200, json={"results": []})
    mock_http(monkeypatch, handler)
    await DeepSeekWebSearch(SECRET).search(query, language, 10)


@pytest.mark.asyncio
async def test_language_suffix_limit_is_checked_before_external_budget(setup, monkeypatch):
    db, _ = setup
    await configure(db)
    mock_http(monkeypatch, lambda request: pytest.fail("oversize effective query must not call HTTP"))
    result = await WebResearchService(db).search("web-run", "suffix", {"queries": [{"query": "x" * 125}]})
    assert result["code"] == "tool_input_invalid" and result["queryBudget"]["used"] == 0


@pytest.mark.asyncio
async def test_fetch_contract_bounded_markdown_title_and_no_target_http(monkeypatch):
    calls = []
    def handler(request):
        calls.append(request)
        assert str(request.url) == "https://api.deepseek.com/v1/tools/fetch"
        assert request.method == "POST" and request.headers["authorization"] == f"Bearer {SECRET}"
        assert json.loads(request.content) == {"url": "https://example.com/article"}
        return httpx.Response(200, json={"title": "正文标题", "content": "# Markdown正文\n" + "字" * 25_000})
    mock_http(monkeypatch, handler)
    page = await DeepSeekPageReader(SECRET).read("https://example.com/article")
    assert len(calls) == 1 and page.title == "正文标题"
    assert len(page.text) == 24_000 and page.truncated and page.text.startswith("# Markdown正文")


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["http://127.0.0.1/", "http://localhost/", "file:///etc/passwd", "https://user:pass@example.com/"])
async def test_fetch_rejects_private_urls_before_any_http(monkeypatch, url):
    mock_http(monkeypatch, lambda request: pytest.fail("unsafe URL must not be submitted"))
    with pytest.raises(WebResearchError) as error:
        await DeepSeekPageReader(SECRET).read(url)
    assert error.value.code == "web_url_blocked"


@pytest.mark.asyncio
@pytest.mark.parametrize("status,body,code", [
    (401, {}, "web_search_auth_failed"), (402, {}, "web_search_insufficient_balance"),
    (403, {}, "web_url_blocked"), (404, {}, "web_page_unavailable"),
    (429, {}, "web_search_rate_limited"), (302, {}, "web_page_unavailable"),
    (200, {"content": " "}, "web_extraction_failed"),
    (200, {"content": 7}, "web_page_unavailable"),
    (200, {"content": "body", "title": []}, "web_page_unavailable"),
])
async def test_fetch_errors_are_sanitized_and_not_retried(monkeypatch, status, body, code):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, json={**body, "error": SECRET}, headers={"Location": "http://127.0.0.1/"})
    mock_http(monkeypatch, handler)
    with pytest.raises(WebResearchError) as error:
        await DeepSeekPageReader(SECRET).read("https://example.com/article")
    assert error.value.code == code and SECRET not in str(error.value) and len(calls) == 1


@pytest.mark.asyncio
async def test_missing_key_blocks_new_fetch_but_keeps_cached_pagination(setup, monkeypatch):
    db, fixture = setup
    source = await fixture.repository.add_source("web-run", result_hit())
    mock_http(monkeypatch, lambda request: pytest.fail("missing credentials must not call HTTP"))
    service = WebResearchService(db)
    args = {"pages": [{"sourceId": source["id"]}]}
    blocked = await service.read_pages("web-run", "missing", args)
    assert blocked["results"][0]["code"] == "web_search_key_missing"
    assert blocked["readBudget"]["used"] == 0
    await fixture.repository.save_page("web-run", source["id"], WebPage(source["url"], "历史正文" * 2000))
    cached = await service.read_pages("web-run", "cached", {"pages": [{"sourceId": source["id"], "offset": 4000}]})
    assert cached["results"][0]["success"] and cached["readBudget"]["used"] == 0
    assert await service.read_pages("web-run", "missing", args) == blocked


def result_hit():
    return SearchHit("官方资料", "https://example.com/article", "摘要", "deepseek")


@pytest.mark.asyncio
async def test_fetch_cancel_drains_two_workers_and_replay_never_charges_again(setup, monkeypatch):
    db, fixture = setup
    await configure(db)
    sources = [await fixture.repository.add_source("web-run", SearchHit(f"来源{i}", f"https://example.com/{i}", "摘要", "deepseek")) for i in range(3)]
    started, stop = asyncio.Event(), asyncio.Event()
    calls, active = [], set()
    async def handler(request):
        calls.append(request)
        active.add(id(request))
        if len(active) == 2:
            started.set()
        try:
            await asyncio.Event().wait()
        finally:
            active.remove(id(request))
    mock_http(monkeypatch, handler)
    service = WebResearchService(db)
    args = {"pages": [{"sourceId": row["id"]} for row in sources]}
    task = asyncio.create_task(service.read_pages("web-run", "cancel-fetch", args, stop))
    await asyncio.wait_for(started.wait(), 2)
    stop.set()
    with pytest.raises(OperationCanceled):
        await asyncio.wait_for(task, 2)
    assert len(calls) == 2 and not active
    assert (await service.repository.budgets("web-run"))["readBudget"]["used"] == 2
    with pytest.raises(WebResearchError, match="中断"):
        await service.read_pages("web-run", "cancel-fetch", args)
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_fetch_batch_uses_one_key_snapshot_partial_failure_duplicate_cache_and_quota(setup, monkeypatch):
    db, fixture = setup
    await configure(db)
    rows = [await fixture.repository.add_source("web-run", SearchHit(str(i), f"https://example.com/{i}", "摘要", "deepseek")) for i in range(5)]
    calls, active, peak = [], 0, 0
    async def handler(request):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        calls.append(request)
        try:
            await asyncio.sleep(0.01)
            await configure(db, "sk-changed-mid-batch")
            index = int(json.loads(request.content)["url"].rsplit("/", 1)[-1])
            return httpx.Response(404 if index == 1 else 200, json={"title": f"正文{index}", "content": "字" * 9000})
        finally:
            active -= 1
    mock_http(monkeypatch, handler)
    service = WebResearchService(db)
    args = {"pages": [{"sourceId": row["id"]} for row in rows]}
    first = await service.read_pages("web-run", "five-fetch", args)
    assert [row["success"] for row in first["results"]] == [True, False, True, True, True]
    assert len(calls) == 5 and peak == 2 and active == 0
    assert all(request.headers["authorization"] == f"Bearer {SECRET}" for request in calls)
    assert first["readBudget"]["used"] == 5
    assert all(len(row["content"]) == 1600 for row in first["results"] if row["success"])
    assert await WebResearchService(db).read_pages("web-run", "five-fetch", args) == first
    duplicate = await service.read_pages("web-run", "duplicate", {"pages": [{"sourceId": rows[0]["id"], "offset": 1600}] * 5})
    assert all(row["success"] for row in duplicate["results"]) and len(calls) == 5
    assert sum(len(row["content"]) for row in duplicate["results"]) == 8000
    assert duplicate["readBudget"]["used"] == 5
    more = [await fixture.repository.add_source("web-run", SearchHit(str(i), f"https://example.com/{i}", "摘要", "deepseek")) for i in range(5, 10)]
    capped = await service.read_pages("web-run", "cap", {"pages": [{"sourceId": row["id"]} for row in more]})
    assert capped["readBudget"] == {"limit": 8, "used": 8, "remaining": 0}
    assert len(calls) == 8 and sum(row.get("code") == "web_budget_exhausted" for row in capped["results"]) == 2
