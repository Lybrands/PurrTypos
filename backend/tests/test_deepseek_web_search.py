from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from application.web_research import WebResearchService
from domains.web_research import API_KEY_SETTING, PROVIDER_SETTING, WebResearchError
from infrastructure.deepseek_web_search import DeepSeekWebSearch, parse_search_response
from infrastructure.web_research import FreeWebSearch
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


async def configure(db, provider="deepseek", key=SECRET):
    for name, value in ((PROVIDER_SETTING,provider),(API_KEY_SETTING,key)):
        await db.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",[name,value])


@pytest.mark.asyncio
@pytest.mark.parametrize("provider,key,expected,reason", [
    (None,None,"free","missing_api_key"),("deepseek","","free","missing_api_key"),
    ("deepseek",SECRET,"deepseek",None),("free",SECRET,"free",None),
])
async def test_routing_uses_saved_choice_and_key_with_per_batch_snapshot(setup, monkeypatch, provider, key, expected, reason):
    db, _ = setup
    if provider is not None:
        await configure(db,provider,key)
    requests, free_queries = [], []
    def handler(request):
        requests.append(request)
        return httpx.Response(200,json={"results":[result()]})
    mock_http(monkeypatch,handler)
    async def free_search(self,*args):
        from domains.web_research import SearchHit
        free_queries.append(args)
        # A mid-batch configuration change must not route the other query differently.
        await configure(db,"deepseek",SECRET)
        return [SearchHit("免费来源","https://example.com/free","摘要","bing_rss")],[]
    monkeypatch.setattr(FreeWebSearch,"search",free_search)
    service = WebResearchService(db)
    args={"queries":[{"query":"资料"},{"query":"sources lang:en","language":"en"}]}
    found=await service.search("web-run","routed",args)
    assert found["success"] and found["searchProvider"]==expected and found["fallbackReason"]==reason
    assert len(requests)==(2 if expected=="deepseek" else 0)
    assert len(free_queries)==(2 if expected=="free" else 0)
    assert found["queryBudget"]["used"]==2
    await configure(db,"free","")
    assert await WebResearchService(db).search("web-run","routed",args)==found
    assert SECRET not in json.dumps(await db.fetch_all("SELECT * FROM web_research_attempts"))
    assert SECRET not in repr(await service.repository.search_configuration())
    if expected=="deepseek":
        stored=(await db.fetch_all("SELECT * FROM web_research_sources"))[0]
        assert stored["published_at"]==found["results"][0]["publishedAt"]
        assert stored["authority"]==4 and found["results"][0]["rerankScore"]==2.7


@pytest.mark.asyncio
async def test_api_auth_failure_is_not_silently_replaced_by_free_search_and_counts_attempt(setup,monkeypatch):
    db,_=setup
    await configure(db)
    mock_http(monkeypatch,lambda request:httpx.Response(401,json={"error":{"message":SECRET}}))
    async def forbidden(*args):
        raise AssertionError("only absent credentials enable automatic fallback")
    monkeypatch.setattr(FreeWebSearch,"search",forbidden)
    found=await WebResearchService(db).search("web-run","bad-key",{"queries":[{"query":"query"}]})
    assert not found["success"] and found["code"]=="web_search_auth_failed"
    assert found["queryBudget"]["used"]==1 and SECRET not in json.dumps(found)


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
    found=await WebResearchService(db).search('web-run','overlong',{'queries':[{'query':'x'*129}]})
    assert not found['success'] and found['code']=='tool_input_invalid'
    assert found['queryBudget']=={'limit':6,'used':0,'remaining':6}
    assert await db.fetch_all("SELECT * FROM web_research_attempts WHERE external_request=1")==[]
