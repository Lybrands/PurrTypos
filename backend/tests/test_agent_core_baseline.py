"""Executable pre-extraction Agent behavior baseline.

The fixture deliberately uses the legacy planner/stream helpers.  A future
``agent_core.runtime`` replay test must consume the same fixture before the
legacy path can be removed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from routers.ai import _classify_tool_round_messages
from services.task_planner import normalize_model_task_plan
from utils.chat_stream import StreamAccumulator, build_tool_round_messages


FIXTURE_PATH = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "agent_core"
    / "runtime_replay_v1.json"
)


def _fixture() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_component_fixture_inventory_cannot_silently_shrink():
    fixture = _fixture()

    assert fixture["schemaVersion"] == 1
    assert [case["id"] for case in fixture["plannerCases"]] == ["read-then-review"]
    assert [case["id"] for case in fixture["modelCases"]] == [
        "direct-answer",
        "fragmented-tool-call",
    ]
    assert [case["id"] for case in fixture["toolOutcomeCases"]] == [
        "approval-rejected",
        "approval-timeout",
        "request-canceled",
        "handler-failed",
        "tool-completed",
    ]


def test_recorded_planner_outputs_keep_their_normalized_contract():
    fixture = _fixture()
    assert fixture["schemaVersion"] == 1

    for case in fixture["plannerCases"]:
        plan = normalize_model_task_plan(
            case["decision"],
            available_tool_names=set(case["availableTools"]),
        )
        expected = case["expected"]

        assert plan is not None, case["id"]
        assert plan["title"] == expected["title"], case["id"]
        assert plan["goal"] == case["decision"]["goal"], case["id"]
        assert [step["id"] for step in plan["steps"]] == expected["stepIds"], case["id"]
        assert [step["executor"] for step in plan["steps"]] == expected["executors"], case["id"]
        assert [step["type"] for step in plan["steps"]] == ["read", "review"], case["id"]
        assert [step["status"] for step in plan["steps"]] == ["pending", "pending"], case["id"]
        assert sorted({
            tool
            for step in plan["steps"]
            for tool in step.get("suggestedTools", [])
        }) == expected["allowedTools"], case["id"]


@pytest.mark.parametrize("case", _fixture()["modelCases"], ids=lambda case: case["id"])
def test_recorded_model_streams_keep_their_accumulation_contract(case: dict[str, Any]):
    accumulator = StreamAccumulator()
    emitted: list[dict[str, Any]] = []
    finish_reason = None

    for chunk in case["chunks"]:
        outcome = accumulator.process_chunk(chunk)
        emitted.extend(outcome.events)
        if outcome.finish_reason is not None:
            finish_reason = outcome.finish_reason

    expected = case["expected"]
    assert accumulator.content == expected["content"]
    assert accumulator.thinking == expected["thinking"]
    assert accumulator.tool_calls == expected["toolCalls"]
    assert finish_reason == expected["finishReason"]
    assert emitted == expected["events"]

    if case.get("toolResults") is not None:
        continuation = build_tool_round_messages(
            accumulator.tool_calls,
            accumulator.content,
            accumulator.thinking,
            case["toolResults"],
        )
        assert [message["role"] for message in continuation] == expected["continuationRoles"]
        assert continuation[0]["tool_calls"] == expected["toolCalls"]
        assert continuation[0]["reasoning_content"] == expected["thinking"]
        assert continuation[1]["tool_call_id"] == case["toolResults"][0]["tool_call_id"]
        assert continuation[1]["content"] == case["toolResults"][0]["content"]


@pytest.mark.parametrize(
    "case",
    _fixture()["toolOutcomeCases"],
    ids=lambda case: case["id"],
)
def test_recorded_tool_outcomes_keep_terminal_classification(case: dict[str, Any]):
    messages = [
        {
            "role": "tool",
            "tool_call_id": f"call-{index}",
            "content": json.dumps(payload, ensure_ascii=False),
        }
        for index, payload in enumerate(case["toolPayloads"], start=1)
    ]

    outcome, error = _classify_tool_round_messages(messages)

    assert outcome == case["expectedOutcome"]
    assert bool(error) is case["expectsError"]
    if case.get("expectedErrorContains"):
        assert case["expectedErrorContains"] in str(error)
