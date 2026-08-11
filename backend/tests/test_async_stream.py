from __future__ import annotations

import asyncio
from time import perf_counter

import pytest

from purra import stream_ownership as async_stream


@pytest.mark.asyncio
async def test_terminal_item_does_not_wait_forever_for_provider_close(
    monkeypatch: pytest.MonkeyPatch,
):
    close_started = asyncio.Event()
    close_canceled = asyncio.Event()

    class _BlockingRawStream:
        def __init__(self):
            self.close_calls = 0

        async def close(self):
            self.close_calls += 1
            close_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                close_canceled.set()
                raise

    async def _items():
        yield "terminal"

    monkeypatch.setattr(async_stream, "_CLOSE_TIMEOUT_SECONDS", 0.01)
    raw_stream = _BlockingRawStream()
    stream = async_stream.OwnedAsyncIterator(
        _items(),
        raw_stream,
        terminal_predicate=lambda item: item == "terminal",
    )

    started = perf_counter()
    item = await asyncio.wait_for(anext(stream), timeout=0.25)
    elapsed = perf_counter() - started
    await asyncio.wait_for(close_canceled.wait(), timeout=0.25)

    assert item == "terminal"
    assert close_started.is_set()
    assert elapsed < 0.25
    assert raw_stream.close_calls == 1


@pytest.mark.asyncio
async def test_canceled_close_owner_waits_for_bounded_shared_cleanup(
    monkeypatch: pytest.MonkeyPatch,
):
    transform_close_started = asyncio.Event()
    transform_close_canceled = asyncio.Event()

    class _BlockingTransform:
        def __init__(self):
            self.close_calls = 0

        def __aiter__(self):
            return self

        async def __anext__(self):
            raise AssertionError("close must not read the transform")

        async def aclose(self):
            self.close_calls += 1
            transform_close_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                transform_close_canceled.set()
                raise

    class _RawStream:
        def __init__(self):
            self.close_calls = 0

        async def close(self):
            self.close_calls += 1

    monkeypatch.setattr(async_stream, "_CLOSE_TIMEOUT_SECONDS", 0.01)
    transform = _BlockingTransform()
    raw_stream = _RawStream()
    stream = async_stream.OwnedAsyncIterator(transform, raw_stream)

    owner = asyncio.create_task(stream.aclose())
    await asyncio.wait_for(transform_close_started.wait(), timeout=0.25)
    owner.cancel()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(owner, timeout=0.25)
    await asyncio.wait_for(transform_close_canceled.wait(), timeout=0.25)

    assert transform.close_calls == 1
    assert raw_stream.close_calls == 1

    await asyncio.wait_for(stream.aclose(), timeout=0.25)
    assert transform.close_calls == 1
    assert raw_stream.close_calls == 1
