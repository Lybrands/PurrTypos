import asyncio
import importlib.util
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import httpx
from openai import AsyncOpenAI

from application.model_runtime import model_request_from_runtime, runtime_from_settings
from application.model_preferences import upgrade_model_config
from application.model_request_service import ModelRequestService, BackgroundModelContext
from infrastructure.models.provider_model_gateway import ProviderModelGateway, _provider_options
from infrastructure.models.profiles.descriptors import profile_for_options, DESCRIPTOR_KEY, model_descriptors
from infrastructure.models.request_boundary import SendContext, prepare_request, CheckedClient
from purra.contracts import ModelInvocation, AgentMessage
from purra.model_protocol import resolve_invocation_output_budget
from purra.errors import ContractViolationError


BASE = {"name": "glm-5.3-flash", "presetId": "zai:glm-5.3-flash", "apiProvider": "zai",
        "baseUrl": "https://open.bigmodel.cn/api/paas/v4", "contextWindow": "128k", "maxGenerationTokens": 4096,
        "thinkingEnabled": True}


def request(config=None, **kwargs):
    return model_request_from_runtime(runtime_from_settings({**BASE, **(config or {})}), **kwargs)


def invoke(req):
    from infrastructure.models.capabilities import reasoning_mode_from_options
    return ModelInvocation(request=req, reasoning_mode=reasoning_mode_from_options(req.options),
                           output_budget=resolve_invocation_output_budget(req.capability_snapshot, max_generation_tokens=req.max_generation_tokens))


@pytest.mark.parametrize('state,effort', [('inherit', 'low'), ('provider_default', None), ('explicit', 'high')])
def test_task_default_never_overrides_provider_default_or_explicit(state, effort):
    choice = {'state': state, **({'value': effort} if state == 'explicit' else {})}
    req = request({'modelPreferences': {'reasoning_effort': choice}}, task_reasoning_preference='economical')
    assert req.options.get('reasoning_effort') == effort


def test_legacy_missing_preference_is_not_reinterpreted_as_task_default():
    req = request(task_reasoning_preference='economical')
    assert 'reasoning_effort' not in req.options
    original = {**BASE, 'apiKey': 'private-key'}
    upgraded = upgrade_model_config(original)
    assert upgraded['modelPreferences']['reasoning_effort'] == {'state': 'provider_default'}
    assert upgraded['apiKey'] == original['apiKey']
    assert upgrade_model_config(upgraded) == upgraded
    assert 'modelPreferences' not in original


def test_generic_frozen_capability_reaches_adapter_without_builtin_lookup():
    cfg = {'name': 'custom', 'baseUrl': 'https://proxy.invalid/v1', 'apiProvider': 'openai',
           'contextWindow': '128k', 'supportsThinking': True, 'thinkingOnly': False,
           'profileMaxGenerationTokens': 8192, 'thinkingEnabled': True}
    req = model_request_from_runtime(runtime_from_settings(cfg))
    profile = profile_for_options(_provider_options(invoke(req)))
    assert profile.profile_id == 'generic'
    assert profile.build_openai_extra_body(True) == {'thinking': {'type': 'enabled'}}


def test_frozen_descriptor_survives_catalog_change(monkeypatch):
    from infrastructure.models.profiles.glm5_3_flash import GLM5_3_FLASH_PROFILE
    req = request()
    monkeypatch.setattr(GLM5_3_FLASH_PROFILE, 'build_openai_extra_body', lambda _: {'broken': True})
    assert profile_for_options(_provider_options(invoke(req))).build_openai_extra_body(True) == {'thinking': {'type': 'enabled'}}


@pytest.mark.parametrize('patch,match', [
    ({'reasoningEffort': 'turbo'}, 'reasoning_effort'),
    ({'apiProvider': 'typo'}, 'provider'),
    ({'baseUrl': 'https://unknown.invalid'}, 'binding'),
    ({'descriptorDigest': 'old-version'}, 'descriptor changed'),
])
def test_invalid_configuration_fails_before_sdk(patch, match):
    with pytest.raises(ValueError, match=match): request(patch)


def test_unknown_explicit_options_are_not_dropped():
    runtime = runtime_from_settings(BASE)
    runtime.options['invented_parameter'] = 'secret'
    with pytest.raises(ValueError, match='unsupported model request options'):
        model_request_from_runtime(runtime)


def test_sdk_omission_is_rejected_even_if_resolver_recorded_the_value():
    req = request({'reasoningEffort': 'low'})
    ctx = SendContext('attempt', req.options, 'zai', 4096)
    with pytest.raises(ContractViolationError, match='reasoning_effort'):
        prepare_request({'model': req.model, 'thinking': {'type': 'enabled'}, 'max_tokens': 4096}, ctx, 'zai')


@pytest.mark.asyncio
@pytest.mark.parametrize('stream', [False, True])
async def test_resolved_glm_request_reaches_sdk_and_records_exact_effort(monkeypatch, stream):
    from infrastructure.models import zai_chat
    from tests.test_zai_provider import _FakeClient, _Dumpable, _SyncStream
    payload = {'model': 'glm-5.3-flash', 'choices': [{'message': {'role': 'assistant', 'content': 'done'}, 'finish_reason': 'stop'}],
               'usage': {'prompt_tokens': 2, 'completion_tokens': 1, 'total_tokens': 3}}
    client = _FakeClient(_SyncStream([_Dumpable({'choices': [{'delta': {'content': 'done'}, 'finish_reason': 'stop'}]})]) if stream else _Dumpable(payload))
    monkeypatch.setattr(zai_chat, '_create_client', lambda *_: client)
    records = []
    async def record(value): records.append(value)
    gateway = ProviderModelGateway('private-key', request_observer=record)
    invocation = invoke(request({'reasoningEffort': 'low'}))
    described = gateway.describe_invocation([], invocation)
    if stream:
        result = await gateway.stream([AgentMessage(role='user', content='private-input')], invocation)
        assert len([chunk async for chunk in result.chunks]) == 1
    else:
        await gateway.complete([AgentMessage(role='user', content='private-input')], invocation)
    assert client.create_calls[0]['reasoning_effort'] == 'low'
    assert records[0]['attemptId'] == described['sdkAttemptId']
    assert records[0]['sent']['maxGenerationTokens'] == 4096
    assert 'private-key' not in str(records) and 'private-input' not in str(records)


@pytest.mark.asyncio
async def test_native_bridge_checks_sdk_and_actual_http_body():
    seen = []
    async def handler(req):
        import json
        seen.append(json.loads(req.content))
        return httpx.Response(200, json={'id': 'test', 'object': 'chat.completion', 'created': 1, 'model': 'test',
                                        'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': 'ok'}, 'finish_reason': 'stop'}]})
    client = AsyncOpenAI(api_key='private', http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    ctx = SendContext('attempt', {'reasoning_effort': 'low'}, 'openai', 1024)
    bridge = CheckedClient(client, protocol='openai', context=ctx).with_options(max_retries=0)
    try:
        await bridge.chat.completions.create(model='test', messages=[{'role': 'user', 'content': 'hello'}], reasoning_effort='low', max_completion_tokens=1024)
        assert seen[0]['reasoning_effort'] == 'low'
        assert seen[0]['max_completion_tokens'] == 1024
    finally:
        await client.close()


def test_generated_renderer_capabilities_match_backend():
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location('generate_descriptors', root / 'scripts/generate-model-descriptors.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert (root / 'src/models/descriptors.generated.ts').read_text() == module.generated_source()


def test_business_modules_cannot_bypass_request_boundary():
    import ast
    root = Path(__file__).resolve().parents[1]
    allowed = {'application/model_request_service.py', 'application/agent_composition.py'}
    violations = []
    for area in ('application', 'domains', 'routers', 'services'):
        for path in (root / area).rglob('*.py'):
            relative = path.relative_to(root).as_posix()
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    module = node.module or ''
                    names = {a.name for a in node.names}
                    if module == 'infrastructure.models.provider_router':
                        violations.append(relative)
                    if module == 'infrastructure.models.provider_model_gateway' and relative not in allowed:
                        violations.append(relative)
                    if module in {'infrastructure.models.openai_chat', 'infrastructure.models.anthropic_chat', 'infrastructure.models.zai_chat'} and names - {'list_models'}:
                        violations.append(relative)
    assert not violations, violations


@pytest.mark.asyncio
async def test_parallel_identical_invocations_have_distinct_sdk_attempts(monkeypatch):
    from infrastructure.models import zai_chat
    from tests.test_zai_provider import _FakeClient, _Dumpable
    payload = {'model': 'glm-5.3-flash', 'choices': [{'message': {'role': 'assistant', 'content': 'ok'}, 'finish_reason': 'stop'}]}
    monkeypatch.setattr(zai_chat, '_create_client', lambda *_: _FakeClient(_Dumpable(payload)))
    records = []
    async def record(value): records.append(value)
    gateway = ProviderModelGateway('key', request_observer=record)
    invocation = invoke(request())
    async def call():
        receipt = gateway.describe_invocation([], invocation)
        await gateway.complete([AgentMessage(role='user', content='same')], invocation)
        return receipt['sdkAttemptId']
    first, second = await asyncio.gather(call(), call())
    assert first != second
    assert {r['attemptId'] for r in records} == {first, second}
    assert len({r['requestDigest'] for r in records}) == 1


@pytest.mark.asyncio
async def test_zai_wait_cancellation_reaps_late_resource(monkeypatch):
    import threading
    from infrastructure.models import zai_chat
    from purra.cancellation import OperationCanceled
    started, release = threading.Event(), threading.Event()
    resource = SimpleNamespace(closed=False)
    resource.close = lambda: setattr(resource, 'closed', True)
    def blocking_call():
        started.set()
        release.wait(2)
        return resource
    signal = asyncio.Event()
    task = asyncio.create_task(zai_chat._thread_call(blocking_call, signal=signal))
    try:
        await asyncio.wait_for(asyncio.to_thread(started.wait, 2), 3)
        signal.set()
        with pytest.raises(OperationCanceled): await asyncio.wait_for(task, 1)
    finally:
        release.set()
        if zai_chat._REAPERS:
            await asyncio.wait_for(asyncio.gather(*tuple(zai_chat._REAPERS)), 3)
    assert resource.closed


def test_legacy_snapshot_uses_historical_codec_after_catalog_change(monkeypatch):
    from infrastructure.models.profiles.glm5_3_flash import GLM5_3_FLASH_PROFILE
    options = _provider_options(invoke(request()))
    options.pop(DESCRIPTOR_KEY)
    monkeypatch.setattr(GLM5_3_FLASH_PROFILE, 'build_openai_extra_body', lambda _: {'broken': True})
    assert profile_for_options(options).build_openai_extra_body(True) == {'thinking': {'type': 'enabled'}}


@pytest.mark.asyncio
async def test_sdk_diagnostics_attach_to_receipt_and_follow_run_deletion(tmp_path):
    import json
    from database.connection import DatabaseConnection
    from infrastructure.persistence.run_store import create_run
    from infrastructure.persistence.model_request_diagnostics import model_request_observer
    from infrastructure.persistence.model_input_diagnostics import read_model_input_diagnostics
    db = DatabaseConnection(tmp_path)
    await db.init()
    try:
        run_id = await create_run(db, session_id=1, prompt='smoke', mode='novel_analysis')
        await db.execute('INSERT INTO ai_agent_run_events(run_id, event_type, payload_json) VALUES (?, ?, ?)',
                         [run_id, 'stream.opened', json.dumps({'callParameters': [{'sdkAttemptId': 'sdk-test'}]})])
        observe = model_request_observer(db)
        value = {'attemptId': 'sdk-test', 'requestDigest': 'digest', 'stage': 'sdk_prepared', 'sent': {'reasoning_effort': 'low'}}
        await observe(value)
        await observe({**value, 'stage': 'provider_response_received'})
        rows = await read_model_input_diagnostics(db, run_id)
        assert rows[0]['sdkRequest']['stage'] == 'provider_response_received'
        assert (await db.fetch_one('SELECT run_id FROM ai_model_sdk_requests'))['run_id'] == run_id
        await db.execute('DELETE FROM ai_agent_runs WHERE id = ?', [run_id])
        assert await db.fetch_all('SELECT * FROM ai_model_sdk_requests') == []
    finally:
        await db.close()


def test_native_bridge_rejects_lost_disabled_reasoning_control():
    context = SendContext('attempt', {'thinking': {'type': 'disabled'}}, 'openai', 1024)
    with pytest.raises(ContractViolationError, match='native reasoning control'):
        prepare_request({'model': 'test', 'max_completion_tokens': 1024}, context, 'openai_native')
