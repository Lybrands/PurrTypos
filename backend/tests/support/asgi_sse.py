"""Small queue-backed ASGI harness for live SSE integration tests.

``httpx.ASGITransport`` buffers an ASGI response before returning it.  That is
fine for finite responses, but it deadlocks a Human-in-the-Loop test: the SSE
request cannot finish until a second HTTP request resolves the approval, while
the test cannot observe the approval id until the first request returns.

This helper drives the ASGI callable directly and exposes response body chunks
as they are sent.  A second request can therefore be issued against the same
application while the first SSE response is still open.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping


ASGIApp = Callable[
    [
        dict[str, Any],
        Callable[[], Awaitable[dict[str, Any]]],
        Callable[[dict[str, Any]], Awaitable[None]],
    ],
    Awaitable[None],
]


@dataclass(frozen=True, slots=True)
class ASGIResponse:
    status_code: int
    headers: Mapping[str, str]
    content: bytes

    def json(self) -> Any:
        return json.loads(self.content)


class LiveASGIResponse:
    """One running ASGI HTTP exchange with incrementally readable body bytes."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        method: str,
        path: str,
        body: bytes,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        request_path, separator, query = path.partition("?")
        request_headers = {
            "host": "testserver",
            "content-length": str(len(body)),
            **dict(headers or {}),
        }
        self._scope: dict[str, Any] = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": method.upper(),
            "scheme": "http",
            "path": request_path,
            "raw_path": request_path.encode("ascii"),
            "query_string": query.encode("ascii") if separator else b"",
            "root_path": "",
            "headers": [
                (name.lower().encode("latin-1"), value.encode("latin-1"))
                for name, value in request_headers.items()
            ],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
        }
        self._request_messages: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._request_messages.put_nowait({
            "type": "http.request",
            "body": body,
            "more_body": False,
        })
        self._started = asyncio.Event()
        self._finished = asyncio.Event()
        self._body_chunks: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._raw_parts: list[bytes] = []
        self._status_code: int | None = None
        self._headers: dict[str, str] = {}
        self._sse_buffer = b""
        self._fail_next_body_send = False
        self._fail_next_response_start = False
        self._task = asyncio.create_task(
            app(self._scope, self._receive, self._send)
        )

    async def _receive(self) -> dict[str, Any]:
        return await self._request_messages.get()

    async def _send(self, message: dict[str, Any]) -> None:
        message_type = message["type"]
        if message_type == "http.response.start":
            if self._fail_next_response_start:
                self._fail_next_response_start = False
                raise OSError("simulated disconnect before response start")
            self._status_code = int(message["status"])
            self._headers = {
                name.decode("latin-1").lower(): value.decode("latin-1")
                for name, value in message.get("headers", [])
            }
            self._started.set()
            return
        if message_type != "http.response.body":
            return
        if self._fail_next_body_send:
            self._fail_next_body_send = False
            raise OSError("simulated ASGI 2.4 client disconnect")
        chunk = bytes(message.get("body") or b"")
        if chunk:
            self._raw_parts.append(chunk)
            await self._body_chunks.put(chunk)
        if not message.get("more_body", False):
            self._finished.set()
            await self._body_chunks.put(None)

    @property
    def status_code(self) -> int:
        if self._status_code is None:
            raise RuntimeError("ASGI response has not started")
        return self._status_code

    @property
    def headers(self) -> Mapping[str, str]:
        return dict(self._headers)

    async def wait_started(self, *, timeout: float = 2.0) -> None:
        await asyncio.wait_for(self._started.wait(), timeout=timeout)
        if self._task.done():
            self._task.result()

    async def next_sse_json(self, *, timeout: float = 2.0) -> dict[str, Any]:
        """Return the next JSON ``data:`` frame, skipping SSE comments."""

        while True:
            frame = _pop_sse_frame(self)
            if frame is not None:
                data_lines = [
                    line[5:].lstrip(b" ")
                    for line in frame.splitlines()
                    if line.startswith(b"data:")
                ]
                if not data_lines:
                    continue
                payload = json.loads(b"\n".join(data_lines))
                if not isinstance(payload, dict):
                    raise AssertionError(f"SSE payload is not an object: {payload!r}")
                return payload

            chunk = await asyncio.wait_for(
                self._body_chunks.get(), timeout=timeout
            )
            if chunk is None:
                self._task.result()
                raise EOFError("ASGI SSE response ended before another data frame")
            self._sse_buffer += chunk

    async def disconnect(self) -> None:
        await self._request_messages.put({"type": "http.disconnect"})

    def fail_next_body_send(self) -> None:
        """Make the next response-body send report an ASGI 2.4 disconnect."""

        self._fail_next_body_send = True

    def fail_next_response_start(self) -> None:
        """Make response.start be the first observation of a dead peer."""

        self._fail_next_response_start = True

    async def finish(self, *, timeout: float = 5.0) -> ASGIResponse:
        response = await self.wait_closed(timeout=timeout)
        if not self._finished.is_set():
            raise AssertionError("ASGI application returned without closing its body")
        return response

    async def wait_closed(self, *, timeout: float = 5.0) -> ASGIResponse:
        """Wait for the ASGI callable, allowing a client-disconnected body."""

        await asyncio.wait_for(asyncio.shield(self._task), timeout=timeout)
        return ASGIResponse(
            status_code=self.status_code,
            headers=self.headers,
            content=b"".join(self._raw_parts),
        )

    async def aclose(self) -> None:
        if self._task.done():
            self._task.result()
            return
        await self.disconnect()
        try:
            await asyncio.wait_for(asyncio.shield(self._task), timeout=1.0)
        except TimeoutError:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)


def start_asgi_request(
    app: ASGIApp,
    *,
    method: str,
    path: str,
    json_body: Any | None = None,
    headers: Mapping[str, str] | None = None,
) -> LiveASGIResponse:
    body = (
        json.dumps(json_body, ensure_ascii=False).encode("utf-8")
        if json_body is not None
        else b""
    )
    request_headers = {
        **({"content-type": "application/json"} if json_body is not None else {}),
        **dict(headers or {}),
    }
    return LiveASGIResponse(
        app,
        method=method,
        path=path,
        body=body,
        headers=request_headers,
    )


async def request_json(
    app: ASGIApp,
    *,
    method: str,
    path: str,
    json_body: Any,
    timeout: float = 2.0,
) -> ASGIResponse:
    response = start_asgi_request(
        app,
        method=method,
        path=path,
        json_body=json_body,
    )
    await response.wait_started(timeout=timeout)
    return await response.finish(timeout=timeout)


def decode_sse_json(content: bytes) -> list[dict[str, Any]]:
    """Decode a completed SSE body and assert that no partial frame remains."""

    holder = type("_Buffer", (), {"_sse_buffer": bytes(content)})()
    payloads: list[dict[str, Any]] = []
    while True:
        frame = _pop_sse_frame(holder)
        if frame is None:
            break
        data_lines = [
            line[5:].lstrip(b" ")
            for line in frame.splitlines()
            if line.startswith(b"data:")
        ]
        if not data_lines:
            continue
        payload = json.loads(b"\n".join(data_lines))
        if not isinstance(payload, dict):
            raise AssertionError(f"SSE payload is not an object: {payload!r}")
        payloads.append(payload)
    if holder._sse_buffer:
        raise AssertionError(f"incomplete SSE frame: {holder._sse_buffer!r}")
    return payloads


def _pop_sse_frame(holder: Any) -> bytes | None:
    buffer = holder._sse_buffer
    separators = [
        (index, separator)
        for separator in (b"\r\n\r\n", b"\n\n")
        if (index := buffer.find(separator)) >= 0
    ]
    if not separators:
        return None
    index, separator = min(separators, key=lambda item: item[0])
    frame = buffer[:index]
    holder._sse_buffer = buffer[index + len(separator):]
    return frame
