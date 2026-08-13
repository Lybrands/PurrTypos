from __future__ import annotations

import asyncio

import pytest

from application.agent_run_service import AgentRunService
from application.request_mapping import (
    to_writing_agent_request,
    writing_run_options,
)
from infrastructure.models.provider_capabilities import ProviderCapabilityCache
from schemas.ai import ChatStreamRequest


class _Stream:
    def __init__(self) -> None:
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration

    async def aclose(self) -> None:
        self.closed = True


class _Handle:
    run_id = "run-bound-before-bind-error"

    def __init__(self, stream: _Stream) -> None:
        self._stream = stream
        self.finished = asyncio.Event()

    def subscribe(self, *, after_sequence: int):
        assert after_sequence == 0
        return self._stream

    async def wait(self):
        await self.finished.wait()


class _Core:
    def __init__(self, stream: _Stream) -> None:
        self._stream = stream
        self.handle = _Handle(stream)
        self.closed = False

    async def submit(self, _request, *, options):
        del options
        return self.handle

    async def close(self) -> None:
        self.closed = True


class _Composition:
    def __init__(self) -> None:
        self.provider_capabilities = ProviderCapabilityCache()
        self.stream = _Stream()
        self.core = _Core(self.stream)
        self.released = []
        self.background = []

    def create_response_judge_policies(self, _request):
        return ()

    def agent_role_registry_for_request(self, _request):
        return None

    def bind_run_profile(self, _request, options):
        return options

    def create_core_for_request(self, _request, _api_key, **_kwargs):
        return self.core

    def release_core(self, core) -> None:
        self.released.append(core)

    def track_background_run(self, task) -> None:
        self.background.append(task)


class _FailingBinding:
    async def validate(self) -> None:
        return None

    async def before_submit(self) -> None:
        return None

    async def on_run_started(self, _run_id: str) -> None:
        raise RuntimeError("receipt bind failed after submit")

    async def on_run_finished(self, _result) -> None:
        return None


class _CancelBeforeSubmit(_FailingBinding):
    async def before_submit(self) -> None:
        raise RuntimeError("request canceled before submit")


class _SubmitFailsCore(_Core):
    async def submit(self, _request, *, options):
        del options
        raise RuntimeError("submit lost after supervisor spawn")


@pytest.mark.asyncio
async def test_bind_failure_after_submit_closes_stream_and_releases_core():
    body = ChatStreamRequest(
        messages=[{"role": "user", "content": "hello"}],
        apiKey="key",
        apiProvider="openai",
        options={
            "model": "deepseek-v4-flash",
            "model_profile": "deepseek:deepseek-v4-flash",
        },
        chatAgentMode="agent",
    )
    provider_options = {
        "model": "deepseek-v4-flash",
        "baseURL": "https://example.test/v1",
        "max_tokens": 2_048,
    }
    request = to_writing_agent_request(body, provider_options)
    options = writing_run_options(request, provider_options)
    composition = _Composition()
    service = AgentRunService(composition)  # type: ignore[arg-type]

    updates = service.run(
        body=body,
        api_key="key",
        provider_options=provider_options,
        signal=asyncio.Event(),
        mapped_request=request,
        base_options=options,
        run_binding_lifecycle=_FailingBinding(),  # type: ignore[arg-type]
    )
    with pytest.raises(RuntimeError, match="receipt bind failed"):
        await anext(updates)

    assert composition.stream.closed is True
    assert composition.released == []
    assert len(composition.background) == 1
    composition.core.handle.finished.set()
    await composition.background[0]
    assert composition.released == [composition.core]


@pytest.mark.asyncio
async def test_pre_submit_failure_releases_core_without_starting_a_run():
    body = ChatStreamRequest(
        messages=[{"role": "user", "content": "hello"}],
        apiKey="key",
        apiProvider="openai",
        options={
            "model": "deepseek-v4-flash",
            "model_profile": "deepseek:deepseek-v4-flash",
        },
        chatAgentMode="agent",
    )
    provider_options = {
        "model": "deepseek-v4-flash",
        "baseURL": "https://example.test/v1",
        "max_tokens": 2_048,
    }
    request = to_writing_agent_request(body, provider_options)
    composition = _Composition()
    updates = AgentRunService(composition).run(  # type: ignore[arg-type]
        body=body,
        api_key="key",
        provider_options=provider_options,
        signal=asyncio.Event(),
        mapped_request=request,
        base_options=writing_run_options(request, provider_options),
        run_binding_lifecycle=_CancelBeforeSubmit(),  # type: ignore[arg-type]
    )

    with pytest.raises(RuntimeError, match="canceled before submit"):
        await anext(updates)

    assert composition.released == [composition.core]
    assert composition.background == []


@pytest.mark.asyncio
async def test_submit_failure_closes_core_before_releasing_ownership():
    body = ChatStreamRequest(
        messages=[{"role": "user", "content": "hello"}],
        apiKey="key",
        apiProvider="openai",
        options={
            "model": "deepseek-v4-flash",
            "model_profile": "deepseek:deepseek-v4-flash",
        },
        chatAgentMode="agent",
    )
    provider_options = {
        "model": "deepseek-v4-flash",
        "baseURL": "https://example.test/v1",
        "max_tokens": 2_048,
    }
    request = to_writing_agent_request(body, provider_options)
    composition = _Composition()
    composition.core = _SubmitFailsCore(composition.stream)
    updates = AgentRunService(composition).run(  # type: ignore[arg-type]
        body=body,
        api_key="key",
        provider_options=provider_options,
        signal=asyncio.Event(),
        mapped_request=request,
        base_options=writing_run_options(request, provider_options),
        run_binding_lifecycle=_FailingBinding(),  # type: ignore[arg-type]
    )

    with pytest.raises(RuntimeError, match="supervisor spawn"):
        await anext(updates)

    assert composition.core.closed is True
    assert composition.released == [composition.core]


@pytest.mark.asyncio
async def test_shutdown_cancel_keeps_detached_core_owned_for_close():
    composition = _Composition()
    task = asyncio.create_task(composition.core.handle.wait())
    composition.background.append(task)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # Canceling an ownership waiter must not masquerade as Run settlement.
    assert composition.released == []
    await composition.core.close()
    assert composition.core.closed is True
