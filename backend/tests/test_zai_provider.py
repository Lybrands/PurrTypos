from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

import pytest

from schemas.ai import GenerateTitleRequest, ListModelsRequest


class _Dumpable:
    def __init__(self, value):
        self._value = value

    def model_dump(self):
        return self._value


class _SyncStream:
    def __init__(self, chunks):
        self._chunks = iter(chunks)
        self.close_calls = 0

    def __iter__(self):
        return self

    def __next__(self):
        return next(self._chunks)

    def close(self):
        self.close_calls += 1


class _FakeClient:
    def __init__(self, response):
        self.response = response
        self.create_calls: list[dict] = []
        self.close_calls = 0
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self._create),
        )
        self.models = SimpleNamespace(list=self._list_models)

    def _create(self, **kwargs):
        self.create_calls.append(dict(kwargs))
        return self.response

    def _list_models(self):
        return self.response

    def close(self):
        self.close_calls += 1


def test_zai_transport_does_not_preempt_source_analysis_activity_window(
    monkeypatch: pytest.MonkeyPatch,
):
    from infrastructure.models import zai_chat

    captured = {}

    class CapturingClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setitem(
        sys.modules,
        "zai",
        SimpleNamespace(ZhipuAiClient=CapturingClient),
    )
    zai_chat._create_client("secret", "https://open.bigmodel.cn/api/paas/v4/")

    timeout = captured["timeout"]
    assert captured["max_retries"] == 0
    assert timeout.connect == 15.0
    assert timeout.read == 125.0
    assert timeout.write == 30.0
    assert timeout.pool == 30.0


@pytest.mark.asyncio
async def test_zai_non_stream_uses_sdk_native_parameters_and_normalizes_response(
    monkeypatch: pytest.MonkeyPatch,
):
    from infrastructure.models import zai_chat

    client = _FakeClient(_Dumpable({
        "model": "glm-5.3-flash",
        "choices": [{
            "message": {
                "role": "assistant",
                "content": "完成",
                "reasoning_content": "先分析",
                "tool_calls": [{
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "read", "arguments": "{}"},
                }],
            },
        }],
        "usage": {
            "prompt_tokens": 12,
            "completion_tokens": 7,
            "total_tokens": 19,
        },
    }))
    monkeypatch.setattr(zai_chat, "_create_client", lambda *_args: client)

    result = await zai_chat.chat_no_stream(
        "secret",
        [{"role": "user", "content": "继续"}],
        {
            "model": "glm-5.3-flash",
            "model_profile": "zai:glm-5.3-flash", "profile_binding": "compatible",
            "baseURL": "https://open.bigmodel.cn/api/paas/v4/",
            "thinking": {"type": "enabled"},
            "temperature": 1.0,
            "max_tokens": 4096,
            "response_format": {"type": "json_object"},
            "tools": [{"type": "function", "function": {"name": "read"}}],
            "tool_choice": "required",
        },
    )

    assert client.create_calls == [{
        "model": "glm-5.3-flash",
        "messages": [{"role": "user", "content": "继续"}],
        "stream": False,
        "thinking": {"type": "enabled"},
        "temperature": 1.0,
        "max_tokens": 4096,
        "response_format": {"type": "json_object"},
        "tools": [{"type": "function", "function": {"name": "read"}}],
        "tool_choice": "required",
    }]
    assert result == {
        "applied_generation_limit": 4096,
        "message": {
            "role": "assistant",
            "content": "完成",
            "reasoning_content": "先分析",
            "tool_calls": [{
                "id": "call-1",
                "type": "function",
                "function": {"name": "read", "arguments": "{}"},
            }],
        },
        "model": "glm-5.3-flash",
        "finish_reason": None,
        "usage": {
            "prompt_tokens": 12,
            "completion_tokens": 7,
            "total_tokens": 19,
        },
    }
    assert client.close_calls == 1


@pytest.mark.asyncio
async def test_zai_stream_bridges_sync_chunks_and_closes_resources(
    monkeypatch: pytest.MonkeyPatch,
):
    from infrastructure.models import zai_chat

    raw_stream = _SyncStream([
        _Dumpable({
            "model": "glm-5.3-flash",
            "choices": [{
                "delta": {
                    "reasoning_content": "分析",
                    "content": "答",
                    "tool_calls": [{
                        "index": 0,
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "read", "arguments": "{"},
                    }],
                },
                "finish_reason": None,
            }],
        }),
        _Dumpable({
            "model": "glm-5.3-flash",
            "choices": [{
                "delta": {
                    "tool_calls": [{
                        "index": 0,
                        "function": {"arguments": "}"},
                    }],
                },
                "finish_reason": "tool_calls",
            }],
            "usage": {
                "prompt_tokens": 5,
                "completion_tokens": 3,
                "total_tokens": 8,
            },
        }),
    ])
    client = _FakeClient(raw_stream)
    monkeypatch.setattr(zai_chat, "_create_client", lambda *_args: client)

    result = await zai_chat.chat_stream(
        "secret",
        [{"role": "user", "content": "读取"}],
        {
            "model": "glm-5.3-flash",
            "model_profile": "zai:glm-5.3-flash", "profile_binding": "compatible",
            "baseURL": "https://open.bigmodel.cn/api/paas/v4/",
            "thinking": {"type": "enabled"},
        },
    )
    chunks = [chunk async for chunk in result["stream"]]

    assert client.create_calls == [{
        "model": "glm-5.3-flash",
        "messages": [{"role": "user", "content": "读取"}],
        "stream": True,
        "thinking": {"type": "enabled"},
    }]
    assert chunks[0]["choices"][0]["delta"]["reasoning_content"] == "分析"
    assert chunks[0]["choices"][0]["delta"]["tool_calls"][0]["function"] == {
        "name": "read",
        "arguments": "{",
    }
    assert chunks[1]["choices"][0]["finish_reason"] == "tool_calls"
    assert chunks[1]["usage"]["total_tokens"] == 8
    assert raw_stream.close_calls == 1
    assert client.close_calls == 1


@pytest.mark.asyncio
async def test_zai_stream_honors_pre_start_cancellation_and_closes_resources(
    monkeypatch: pytest.MonkeyPatch,
):
    from infrastructure.models import zai_chat

    raw_stream = _SyncStream([_Dumpable({"choices": []})])
    client = _FakeClient(raw_stream)
    monkeypatch.setattr(zai_chat, "_create_client", lambda *_args: client)
    signal = asyncio.Event()
    signal.set()

    from purra.cancellation import OperationCanceled
    with pytest.raises(OperationCanceled):
        await zai_chat.chat_stream("secret", [{"role": "user", "content": "取消"}], {
            "model": "glm-5.3-flash", "model_profile": "zai:glm-5.3-flash",
            "baseURL": "https://open.bigmodel.cn/api/paas/v4/",
        }, signal)
    assert not client.create_calls
    assert raw_stream.close_calls == 0
    assert client.close_calls == 1


@pytest.mark.asyncio
async def test_zai_title_and_model_listing_use_the_sdk(monkeypatch: pytest.MonkeyPatch):
    from infrastructure.models import zai_chat

    title_client = _FakeClient(_Dumpable({
        "model": "glm-5.3-flash",
        "choices": [{"message": {"role": "assistant", "content": "「春日写作」"}, "finish_reason": "stop"}],
    }))
    monkeypatch.setattr(zai_chat, "_create_client", lambda *_args: title_client)
    from application.session_title_service import generate_session_title
    title = await generate_session_title(
        api_key="secret", provider="zai", prompt="写一段春天的故事",
        options={
            "model": "glm-5.3-flash",
            "model_profile": "zai:glm-5.3-flash", "profile_binding": "compatible",
            "baseURL": "https://open.bigmodel.cn/api/paas/v4/",
        },
    )
    assert title == "春日写作"
    assert title_client.create_calls[0]["thinking"] == {"type": "enabled"}
    assert title_client.close_calls == 1

    models_client = _FakeClient(None)
    monkeypatch.setattr(zai_chat, "_create_client", lambda *_args: models_client)
    assert await zai_chat.list_models(
        "secret",
        "https://open.bigmodel.cn/api/paas/v4/",
    ) == ["glm-5.3-flash"]
    assert models_client.close_calls == 1


@pytest.mark.asyncio
async def test_provider_router_selects_zai_adapter(monkeypatch: pytest.MonkeyPatch):
    from infrastructure.models import provider_router, zai_chat

    async def fake_stream(*_args):
        return {"stream": "zai-stream", "model": "glm-5.3-flash"}

    async def fake_complete(*_args):
        return {"message": {"content": "zai"}, "model": "glm-5.3-flash"}

    monkeypatch.setattr(zai_chat, "chat_stream", fake_stream)
    monkeypatch.setattr(zai_chat, "chat_no_stream", fake_complete)

    assert (await provider_router.create_chat_stream(
        "secret", [], {"model": "glm-5.3-flash"}, "zai",
    ))["stream"] == "zai-stream"
    assert (await provider_router.create_chat_no_stream(
        "secret", [], {"model": "glm-5.3-flash"}, "zai",
    ))["message"]["content"] == "zai"


@pytest.mark.asyncio
async def test_ai_routes_select_zai_for_models_and_titles(monkeypatch: pytest.MonkeyPatch):
    from infrastructure.models import zai_chat
    from routers.ai import generate_title, list_models

    async def fake_models(api_key, base_url):
        assert api_key == "secret"
        assert base_url == "https://open.bigmodel.cn/api/paas/v4"
        return ["glm-5.3-flash"]

    async def fake_title(*, api_key, prompt, options, provider, db):
        assert api_key == "secret"
        assert prompt == "春天"
        assert options["model"] == "glm-5.3-flash"
        return "春日"

    monkeypatch.setattr(zai_chat, "list_models", fake_models)
    monkeypatch.setattr("application.session_title_service.generate_session_title", fake_title)
    monkeypatch.setattr("routers.ai.get_db", lambda: None)

    models_response = await list_models(ListModelsRequest(
        apiKey="secret",
        baseURL="https://open.bigmodel.cn/api/paas/v4/",
        apiProvider="zai",
    ))
    assert models_response == {"success": True, "data": ["glm-5.3-flash"]}

    title_response = await generate_title(GenerateTitleRequest(
        apiKey="secret",
        baseURL="https://open.bigmodel.cn/api/paas/v4/",
        apiProvider="zai",
        model="glm-5.3-flash",
        prompt="春天",
    ))
    assert title_response == {"success": True, "data": "春日"}


@pytest.mark.parametrize('effort', ['low', 'high', 'max'])
@pytest.mark.parametrize('stream', [False, True])
def test_zai_preserves_reasoning_effort_at_sdk_boundary(effort, stream):
    from infrastructure.models.zai_chat import _build_chat_params
    from infrastructure.models.profiles.glm5_3_flash import GLM5_3_FLASH_PROFILE
    params = _build_chat_params([{'role': 'user', 'content': 'test'}], {
        'model': 'glm-5.3-flash', 'thinking': {'type': 'enabled'},
        'reasoning_effort': effort, 'max_tokens': 10000,
    }, GLM5_3_FLASH_PROFILE, stream=stream)
    assert params['reasoning_effort'] == effort
    assert params['max_tokens'] == 10000
    assert params['thinking'] == {'type': 'enabled'}
