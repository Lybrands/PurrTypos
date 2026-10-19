from __future__ import annotations

import json

import httpx
import pytest

from fastapi import FastAPI
from routers import settings
from domains.web_research import API_KEY_SETTING, KEY_CONFIGURED_SETTING, PROVIDER_SETTING
from tests.test_web_research import setup

app = FastAPI()
app.include_router(settings.router, prefix="/api")


@pytest.mark.asyncio
async def test_settings_http_masks_key_keeps_it_on_other_changes_and_explicitly_deletes(setup, monkeypatch):
    db, _ = setup
    monkeypatch.setattr(settings, "get_db", lambda: db)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
        async def put(data):
            response = await client.put('/api/settings', json={"data": data})
            assert response.status_code == 200, response.text
        initial = (await client.get('/api/settings')).json()["data"]
        assert initial[PROVIDER_SETTING] == 'deepseek' and initial[KEY_CONFIGURED_SETTING] is False
        await put({PROVIDER_SETTING:'deepseek',API_KEY_SETTING:'  sk-fixture-private  '})
        loaded = (await client.get('/api/settings')).json()["data"]
        assert loaded[KEY_CONFIGURED_SETTING] is True and API_KEY_SETTING not in loaded
        assert 'sk-fixture-private' not in json.dumps(loaded)
        await put({'web_search_enabled':False,PROVIDER_SETTING:'free',KEY_CONFIGURED_SETTING:False})
        assert (await db.fetch_one('SELECT value FROM settings WHERE key=?',[API_KEY_SETTING]))['value']=='sk-fixture-private'
        assert (await client.get('/api/settings')).json()["data"][KEY_CONFIGURED_SETTING] is True
        await put({API_KEY_SETTING:''})
        assert (await client.get('/api/settings')).json()["data"][KEY_CONFIGURED_SETTING] is False
        # Keys are opaque strings even if their content resembles JSON.
        await put({API_KEY_SETTING:'123'})
        assert (await client.get('/api/settings')).json()["data"][KEY_CONFIGURED_SETTING] is True


@pytest.mark.asyncio
@pytest.mark.parametrize('patch',[
    {PROVIDER_SETTING:'unknown'},{API_KEY_SETTING:None},{API_KEY_SETTING:123},
    {API_KEY_SETTING:'x'*513},{API_KEY_SETTING:'sk-bad\nheader'},{API_KEY_SETTING:'sk bad'},
    {API_KEY_SETTING:'密钥'}, {'web_search_enabled':'true'},
])
async def test_invalid_cascaded_update_is_atomic_and_does_not_leak_secret(setup,monkeypatch,patch):
    db,_=setup
    monkeypatch.setattr(settings,'get_db',lambda:db)
    before=await db.fetch_all('SELECT key,value FROM settings ORDER BY key')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver') as client:
        response=await client.put('/api/settings',json={'data':{'web_search_enabled':False,**patch}})
    assert response.status_code==422
    assert await db.fetch_all('SELECT key,value FROM settings ORDER BY key')==before
    if API_KEY_SETTING in patch and isinstance(patch[API_KEY_SETTING],str) and len(patch[API_KEY_SETTING])>10:
        assert patch[API_KEY_SETTING] not in response.text


@pytest.mark.asyncio
async def test_additive_source_metadata_migration_keeps_existing_cached_page(setup):
    from database.web_research_schema import init_web_research_schema
    from domains.web_research import SearchHit,WebPage
    db,service=setup
    row=await service.repository.add_source('web-run',SearchHit('原来源','https://example.com/a','摘要','bing_rss'))
    await service.repository.save_page('web-run',row['id'],WebPage(row['url'],'历史正文'))
    for name in ('published_at','relevance_score','authority'):
        await db.execute(f'ALTER TABLE web_research_sources DROP COLUMN {name}')
    await init_web_research_schema(db)
    await init_web_research_schema(db)
    migrated=await service.repository.source('web-run',row['id'])
    assert migrated['page_text']=='历史正文' and migrated['published_at'] is None
    updated=await service.repository.add_source('web-run',SearchHit('新来源',row['url'],'摘要','deepseek','2026-10-18T00:00:00+00:00',2.5,3))
    assert updated['id']==row['id'] and updated['page_text']=='历史正文'
    assert updated['authority']==3 and updated['published_at']=='2026-10-18T00:00:00+00:00'


@pytest.mark.asyncio
async def test_saved_settings_drive_real_tool_gateway_metadata_local_batch_reads_and_replay(setup,monkeypatch):
    from agents.shared.web_research_tools import with_web_research_tools
    from application.web_research import WebResearchService
    from purra.contracts import ExecutionState,ToolBatchRequest,ToolCall,ToolBatchOutcome
    from purra.tools import InMemoryToolCatalog
    from purra.tools.executor import CoreToolExecutor
    from tests.test_deepseek_web_search import mock_http,result,SECRET
    db,fixture=setup
    monkeypatch.setattr(settings,'get_db',lambda:db)
    # Save through the actual HTTP settings router before installing the remote HTTP mock.
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver') as client:
        saved=await client.put('/api/settings',json={'data':{PROVIDER_SETTING:'deepseek',API_KEY_SETTING:SECRET}})
        assert saved.status_code==200
    requests=[]
    def handler(request):
        requests.append(request)
        return httpx.Response(200,json={'results':[result(url=f'https://example.com/{i}',title=f'来源{i}') for i in range(5)]})
    mock_http(monkeypatch,handler)
    service=WebResearchService(db,reader=fixture.reader)
    catalog=with_web_research_tools(InMemoryToolCatalog(()),service)
    state=ExecutionState(run_id='web-run')
    class Sink:
        async def emit(self,event):
            pass
    async def execute(name,call_id,args):
        outcome=await CoreToolExecutor(catalog).execute_batch(ToolBatchRequest(
            run_id='web-run',state=state,allowed_tool_names=frozenset({'searchWeb','readWebPages'}),
            calls=(ToolCall(id=call_id,name=name,arguments_json=json.dumps(args)),),
        ),Sink())
        assert outcome.outcome is ToolBatchOutcome.COMPLETED
        return json.loads(outcome.results[0].content)
    search_args={'queries':[{'query':'资料 lang:zh'},{'query':'sources lang:en','language':'en'}],'maxResults':5}
    found=await execute('searchWeb','ds-tools',search_args)
    assert found['searchProvider']=='deepseek' and len(requests)==2
    assert all(row['backend']=='deepseek' and row['authority']==4 for row in found['results'])
    read_args={'pages':[{'sourceId':row['sourceId']} for row in found['results']]}
    batch=await execute('readWebPages','ds-pages',read_args)
    assert len(batch['results'])==5 and all(row['success'] for row in batch['results'])
    assert batch['bodyBudget']['maxCharactersPerPage']==1600 and batch['readBudget']['used']==5
    assert all(row['publishedAt']=='2026-10-18T02:00:00+00:00' for row in batch['results'])
    assert fixture.reader.calls==5 and len(requests)==2
    assert found==await execute('searchWeb','ds-tools',search_args)
    assert batch==await execute('readWebPages','ds-pages',read_args)
    assert fixture.reader.calls==5 and len(requests)==2
    assert SECRET not in json.dumps(state.domain)
    assert SECRET not in json.dumps(await db.fetch_all('SELECT * FROM web_research_attempts'))
