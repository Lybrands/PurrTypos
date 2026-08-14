from __future__ import annotations

import asyncio

import pytest

from application.agent_run_service import (
    AgentRunService,
    _HostChildCancellationSignal,
)
from purra.api import AgentCoreRunOptions
from purra.contracts import AgentRunResult, RunLineage, RunStatus
from purra.output import (
    PublicPresentationMode,
    ResponseTransactionMode,
    ResponseTransactionPolicy,
)
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

    async def cancel(self, _reason: str) -> None:
        self.finished.set()


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
        self.output_repository = _OutputRepository()

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
    def __init__(self) -> None:
        self.start_failure_codes: list[str] = []

    async def validate(self) -> None:
        return None

    async def before_submit(self) -> None:
        return None

    async def on_run_started(self, _run_id: str) -> None:
        raise RuntimeError("receipt bind failed after submit")

    async def on_run_finished(self, _result) -> None:
        return None

    async def on_start_failed(self, code: str) -> None:
        self.start_failure_codes.append(code)


class _CancelBeforeSubmit(_FailingBinding):
    async def before_submit(self) -> None:
        raise RuntimeError("request canceled before submit")


class _SubmitFailsCore(_Core):
    async def submit(self, _request, *, options):
        del options
        raise RuntimeError("submit lost after supervisor spawn")


class _CancelableHandle(_Handle):
    run_id = "child-run"

    def __init__(self, stream: _Stream) -> None:
        super().__init__(stream)
        self.cancel_reasons: list[str] = []

    async def cancel(self, reason: str) -> None:
        self.cancel_reasons.append(reason)
        self.finished.set()

    async def wait(self):
        await self.finished.wait()
        return AgentRunResult(
            run_id=self.run_id,
            status=RunStatus.CANCELED,
            error="host_child_canceled",
        )


class _CancelableCore(_Core):
    def __init__(self, stream: _Stream) -> None:
        super().__init__(stream)
        self.handle = _CancelableHandle(stream)


class _OutputRepository:
    def __init__(self, value: str = '{"answer":"persisted"}') -> None:
        self.value = value
        self.reads: list[str] = []

    async def load_validated_result(self, run_id: str) -> str:
        self.reads.append(run_id)
        return self.value


class _DoneHandle(_Handle):
    run_id = "child-run-done"

    async def wait(self):
        return AgentRunResult(
            run_id=self.run_id,
            status=RunStatus.DONE,
        )


class _DoneCore(_Core):
    def __init__(self, stream: _Stream) -> None:
        super().__init__(stream)
        self.handle = _DoneHandle(stream)


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

    binding = _FailingBinding()
    updates = service.run(
        body=body,
        api_key="key",
        provider_options=provider_options,
        signal=asyncio.Event(),
        mapped_request=request,
        base_options=options,
        run_binding_lifecycle=binding,
    )
    with pytest.raises(RuntimeError, match="receipt bind failed"):
        await anext(updates)

    assert composition.stream.closed is True
    assert binding.start_failure_codes == ["RuntimeError"]
    assert composition.released == [composition.core]
    assert composition.background == []


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
    binding = _CancelBeforeSubmit()
    updates = AgentRunService(composition).run(  # type: ignore[arg-type]
        body=body,
        api_key="key",
        provider_options=provider_options,
        signal=asyncio.Event(),
        mapped_request=request,
        base_options=writing_run_options(request, provider_options),
        run_binding_lifecycle=binding,
    )

    with pytest.raises(RuntimeError, match="canceled before submit"):
        await anext(updates)

    assert composition.released == [composition.core]
    assert composition.background == []
    assert binding.start_failure_codes == ["RuntimeError"]


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
    binding = _FailingBinding()
    updates = AgentRunService(composition).run(  # type: ignore[arg-type]
        body=body,
        api_key="key",
        provider_options=provider_options,
        signal=asyncio.Event(),
        mapped_request=request,
        base_options=writing_run_options(request, provider_options),
        run_binding_lifecycle=binding,
    )

    with pytest.raises(RuntimeError, match="supervisor spawn"):
        await anext(updates)

    assert composition.core.closed is True
    assert composition.released == [composition.core]
    assert binding.start_failure_codes == ["RuntimeError"]


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


@pytest.mark.asyncio
async def test_host_child_signal_cancels_the_same_handle_and_awaits_terminal():
    body = ChatStreamRequest(
        messages=[{"role": "user", "content": "child part"}],
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
    composition.core = _CancelableCore(composition.stream)
    signal = asyncio.Event()
    task = asyncio.create_task(AgentRunService(
        composition,  # type: ignore[arg-type]
    ).run_host_child(
        body=body,
        api_key="key",
        provider_options=provider_options,
        signal=signal,
        lineage=RunLineage(
            parent_run_id="root-run",
            root_run_id="root-run",
            delegation_id=None,
            agent_role="screenplay-part",
            depth=1,
        ),
        mapped_request=request,
        base_options=AgentCoreRunOptions(),
    ))

    await asyncio.sleep(0)
    signal.set()
    result = await task

    assert result.status is RunStatus.CANCELED
    assert composition.core.handle.cancel_reasons == ["host_child_canceled"]
    assert composition.released == [composition.core]


@pytest.mark.asyncio
async def test_canceling_host_child_caller_does_not_leave_the_run_alive():
    body = ChatStreamRequest(
        messages=[{"role": "user", "content": "child part"}],
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
    composition.core = _CancelableCore(composition.stream)
    task = asyncio.create_task(AgentRunService(
        composition,  # type: ignore[arg-type]
    ).run_host_child(
        body=body,
        api_key="key",
        provider_options=provider_options,
        signal=asyncio.Event(),
        lineage=RunLineage(
            parent_run_id="root-run",
            root_run_id="root-run",
            delegation_id=None,
            agent_role="screenplay-part",
            depth=1,
        ),
        mapped_request=request,
        base_options=AgentCoreRunOptions(),
    ))

    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert composition.core.handle.cancel_reasons == ["host_child_canceled"]
    assert composition.released == [composition.core]


@pytest.mark.asyncio
async def test_host_child_entry_rejects_model_delegation_lineage():
    composition = _Composition()
    body = ChatStreamRequest(
        messages=[{"role": "user", "content": "child part"}],
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

    with pytest.raises(ValueError, match="cannot claim a delegation"):
        await AgentRunService(composition).run_host_child(  # type: ignore[arg-type]
            body=body,
            api_key="key",
            provider_options=provider_options,
            signal=asyncio.Event(),
            lineage=RunLineage(
                parent_run_id="root-run",
                root_run_id="root-run",
                delegation_id="delegation-1",
                agent_role="researcher",
                depth=1,
            ),
            mapped_request=to_writing_agent_request(body, provider_options),
            base_options=AgentCoreRunOptions(),
        )

    assert composition.released == []


@pytest.mark.asyncio
async def test_validated_host_child_reads_the_authoritative_persisted_result():
    body = ChatStreamRequest(
        messages=[{"role": "user", "content": "child part"}],
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
    composition.core = _DoneCore(composition.stream)
    options = AgentCoreRunOptions(
        response_transaction_policy=ResponseTransactionPolicy(
            mode=ResponseTransactionMode.VALIDATED_RESULT,
            public_presentation=PublicPresentationMode.NONE,
        )
    )

    result = await AgentRunService(composition).run_host_child(  # type: ignore[arg-type]
        body=body,
        api_key="key",
        provider_options=provider_options,
        signal=None,
        lineage=RunLineage(
            parent_run_id="root-run",
            root_run_id="root-run",
            delegation_id=None,
            agent_role="screenplay-part",
            depth=1,
        ),
        mapped_request=request,
        base_options=options,
    )

    assert result.validated_result == '{"answer":"persisted"}'
    assert composition.output_repository.reads == [result.run_id]


@pytest.mark.asyncio
async def test_direct_host_child_never_reads_or_exposes_a_validated_result():
    body = ChatStreamRequest(
        messages=[{"role": "user", "content": "child part"}],
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
    composition.core = _DoneCore(composition.stream)

    result = await AgentRunService(composition).run_host_child(  # type: ignore[arg-type]
        body=body,
        api_key="key",
        provider_options=provider_options,
        signal=None,
        lineage=RunLineage(
            parent_run_id="root-run",
            root_run_id="root-run",
            delegation_id=None,
            agent_role="screenplay-part",
            depth=1,
        ),
        mapped_request=request,
        base_options=AgentCoreRunOptions(),
    )

    assert result.validated_result is None
    assert composition.output_repository.reads == []


@pytest.mark.asyncio
async def test_validated_result_can_be_read_after_service_recreation():
    composition = _Composition()
    first = AgentRunService(composition)  # type: ignore[arg-type]
    second = AgentRunService(composition)  # type: ignore[arg-type]

    assert await first.read_validated_result("child-run") == '{"answer":"persisted"}'
    assert await second.read_validated_result("child-run") == '{"answer":"persisted"}'
    assert composition.output_repository.reads == ["child-run", "child-run"]


def _pending_event_waits_since(before: set[asyncio.Task]) -> list[asyncio.Task]:
    return [
        task
        for task in asyncio.all_tasks() - before
        if not task.done()
        and getattr(task.get_coro(), "__qualname__", "") == "Event.wait"
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("settle", ("normal", "signal", "caller", "race"))
async def test_host_child_combined_signal_never_leaks_event_waiters(settle):
    upstream = asyncio.Event()
    combined = _HostChildCancellationSignal(upstream)
    before = set(asyncio.all_tasks())
    waiter = asyncio.create_task(combined.wait())
    await asyncio.sleep(0)

    if settle == "normal":
        waiter.cancel()
    elif settle == "signal":
        upstream.set()
    elif settle == "caller":
        combined.cancel()
    else:
        upstream.set()
        combined.cancel()

    await asyncio.gather(waiter, return_exceptions=True)
    await asyncio.sleep(0)

    leaked = _pending_event_waits_since(before)
    for task in leaked:
        task.cancel()
    await asyncio.gather(*leaked, return_exceptions=True)
    assert leaked == []
