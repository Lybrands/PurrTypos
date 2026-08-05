from __future__ import annotations

import asyncio
import json

import pytest

from agent_core.cancellation import OperationCanceled, await_with_cancellation
from agent_core.contracts import ToolCall, ToolExecutionLimits
from agent_core.tools.security import (
    ParsedToolCall,
    normalize_tool_arguments_to_schema,
    parse_tool_arguments,
    preflight_tool_calls,
    sanitize_error_message,
    sanitize_tool_result,
    summarize_tool_arguments,
    validate_tool_arguments_schema,
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
    assert oversized.diagnostics == {
        "stage": "transport_preflight",
        "actualChars": 7,
        "maxChars": 4,
        "measurement": "raw_json_transport_chars",
    }


def test_invalid_json_reports_position_without_echoing_payload():
    _, failure = parse_tool_arguments(
        '{"sceneText":"没有闭合}',
        tool_name="proposeSceneDraft",
    )

    assert failure is not None
    assert failure.code == "invalid_tool_arguments_json"
    assert failure.diagnostics["stage"] == "json_decode"
    assert failure.diagnostics["toolName"] == "proposeSceneDraft"
    assert failure.diagnostics["line"] == 1
    assert failure.diagnostics["column"] > 1
    assert "没有闭合" not in failure.message


def test_default_transport_envelope_does_not_reintroduce_the_legacy_32k_limit():
    raw = json.dumps({"sceneText": "雾" * 6_000}, ensure_ascii=True)
    assert len(raw) > 32_000

    value, failure = parse_tool_arguments(raw)

    assert failure is None
    assert value == {"sceneText": "雾" * 6_000}


def test_discriminated_any_of_reports_the_selected_variant_requirement():
    parameters = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "anyOf": [{
                        "type": "object",
                        "properties": {
                            "itemType": {
                                "type": "string",
                                "enum": ["text_item"],
                            },
                            "text": {"type": "string"},
                        },
                        "required": ["itemType", "text"],
                        "additionalProperties": False,
                    }, {
                        "type": "object",
                        "properties": {
                            "itemType": {
                                "type": "string",
                                "enum": ["evidence"],
                            },
                            "sourceIds": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                        "required": ["itemType", "sourceIds"],
                        "additionalProperties": False,
                    }],
                },
            },
        },
        "required": ["items"],
        "additionalProperties": False,
    }
    parsed = ParsedToolCall(
        call=ToolCall(
            id="call-any-of",
            name="appendItems",
            arguments_json='{"items":[{"itemType":"text_item"}]}',
        ),
        arguments={"items": [{"itemType": "text_item"}]},
    )

    failure = validate_tool_arguments_schema(parsed, parameters)

    assert failure is not None
    assert failure.code == "invalid_tool_arguments_schema"
    assert failure.diagnostics["path"] == "$.items[0].text"
    assert failure.diagnostics["keyword"] == "required"


def test_discriminated_any_of_normalizes_nested_structures():
    parameters = {
        "type": "object",
        "properties": {
            "item": {
                "anyOf": [{
                    "type": "object",
                    "properties": {
                        "itemType": {
                            "type": "string",
                            "enum": ["evidence"],
                        },
                        "sourceIds": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": ["itemType", "sourceIds"],
                    "additionalProperties": False,
                }],
            },
        },
        "required": ["item"],
        "additionalProperties": False,
    }
    parsed = ParsedToolCall(
        call=ToolCall(
            id="call-normalize-any-of",
            name="appendItems",
            arguments_json="{}",
        ),
        arguments={
            "item": {
                "itemType": "evidence",
                "sourceIds": '["chapter-1"]',
            },
        },
    )

    normalized, failure = normalize_tool_arguments_to_schema(
        parsed,
        parameters,
    )

    assert failure is None
    assert normalized is not None
    assert normalized.arguments["item"]["sourceIds"] == ("chapter-1",)
    assert normalized.normalized_argument_paths == ("$.item.sourceIds",)


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


@pytest.mark.asyncio
async def test_await_with_cancellation_returns_a_suppressed_commit_receipt():
    signal = asyncio.Event()
    started = asyncio.Event()

    async def _commit_like_operation():
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            # A cancellation-linearizable adapter suppresses cancellation once
            # COMMIT has begun and returns the authoritative durable receipt.
            return "durable-receipt"

    task = asyncio.create_task(
        await_with_cancellation(
            _commit_like_operation(),
            signal,
            completion_wins_after_cancel=True,
        )
    )
    await started.wait()
    signal.set()

    assert await asyncio.wait_for(task, timeout=1) == "durable-receipt"


@pytest.mark.asyncio
async def test_swallowed_cancellation_is_not_a_receipt_without_opt_in():
    signal = asyncio.Event()
    started = asyncio.Event()

    async def _badly_behaved_operation():
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            return "not-a-receipt"

    task = asyncio.create_task(
        await_with_cancellation(_badly_behaved_operation(), signal)
    )
    await started.wait()
    signal.set()

    with pytest.raises(OperationCanceled):
        await asyncio.wait_for(task, timeout=1)


@pytest.mark.asyncio
async def test_external_task_cancellation_honors_opted_in_durable_receipt():
    started = asyncio.Event()
    release = asyncio.Event()

    async def _commit_like_operation():
        started.set()
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                continue
        return "durable-receipt"

    task = asyncio.create_task(await_with_cancellation(
        _commit_like_operation(),
        None,
        completion_wins_after_cancel=True,
    ))
    await started.wait()
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()

    release.set()
    assert await asyncio.wait_for(task, timeout=1) == "durable-receipt"
