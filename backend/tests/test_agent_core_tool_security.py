from __future__ import annotations

import asyncio
import json

import pytest

from agent_core.cancellation import OperationCanceled, await_with_cancellation
from agent_core.contracts import ToolCall, ToolExecutionLimits
from agent_core.tools.security import (
    parse_tool_arguments,
    preflight_tool_calls,
    sanitize_error_message,
    sanitize_tool_result,
    summarize_tool_arguments,
)


@pytest.mark.parametrize(
    "raw,code",
    [
        (None, "invalid_tool_arguments_type"),
        ("not-json", "invalid_tool_arguments_json"),
        ("[]", "invalid_tool_arguments_shape"),
        ("{}x", "invalid_tool_arguments_json"),
    ],
)
def test_strict_tool_arguments_require_a_json_object_string(raw, code):
    value, failure = parse_tool_arguments(raw)
    assert value is None
    assert failure is not None
    assert failure.code == code


def test_empty_arguments_are_compatible_with_empty_object_and_immutable():
    value, failure = parse_tool_arguments("")
    assert failure is None
    assert value == {}
    with pytest.raises(TypeError):
        value["new"] = True  # type: ignore[index]


def test_batch_preflight_rejects_duplicates_limits_and_oversized_arguments():
    limits = ToolExecutionLimits(max_calls_per_batch=1, max_argument_chars=4)
    calls = (
        ToolCall(id="same", name="alpha", arguments_json="{}"),
        ToolCall(id="same", name="alpha", arguments_json="{}"),
    )
    _, too_many = preflight_tool_calls(calls, limits)
    assert too_many is not None and too_many.code == "too_many_tool_calls"

    duplicate_limits = ToolExecutionLimits(max_calls_per_batch=2)
    _, duplicate = preflight_tool_calls(calls, duplicate_limits)
    assert duplicate is not None and duplicate.code == "duplicate_tool_call_id"

    _, oversized = parse_tool_arguments('{"x":1}', max_chars=4)
    assert oversized is not None and oversized.code == "tool_arguments_too_large"


def test_tool_result_and_error_sanitization_are_bounded_and_redacted():
    secret = "Bearer abc.def sk-abcdefgh C:\\private\\secret.txt"
    sanitized = sanitize_tool_result(secret)
    assert "abc.def" not in sanitized
    assert "sk-abcdefgh" not in sanitized
    assert "private" not in sanitized
    assert "[REDACTED" in sanitized

    oversized = json.loads(sanitize_tool_result("x" * 9, max_chars=8))
    assert oversized["errorCode"] == "tool_result_too_large"
    assert "x" * 9 not in json.dumps(oversized)

    error = sanitize_error_message(secret, max_chars=40)
    assert len(error) <= 40
    assert "abc.def" not in error

    posix = sanitize_tool_result("failed at /home/agent/private/data.db")
    assert "/home/agent" not in posix
    assert "[REDACTED_PATH]" in posix


def test_approval_summary_is_deterministic_redacted_and_strictly_bounded():
    arguments, failure = parse_tool_arguments(
        '{"z":"Bearer abcdefgh","a":"' + "x" * 100 + '"}'
    )
    assert failure is None and arguments is not None
    summary = summarize_tool_arguments(arguments, max_chars=32)

    assert len(summary) <= 32
    assert "abcdefgh" not in summary
    assert summary.endswith("…")


@pytest.mark.asyncio
async def test_await_with_cancellation_cancels_and_awaits_the_operation():
    signal = asyncio.Event()
    started = asyncio.Event()
    cleaned = asyncio.Event()

    async def _blocked():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    task = asyncio.create_task(await_with_cancellation(_blocked(), signal))
    await started.wait()
    signal.set()

    with pytest.raises(OperationCanceled):
        await asyncio.wait_for(task, timeout=1)
    assert cleaned.is_set()
