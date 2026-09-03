from __future__ import annotations

from purra.api import PlanningMode

from application.request_mapping import to_writing_agent_request
from application.screenplay_agent_service import _root_request
from schemas.ai import ChatStreamRequest
from schemas.screenplay_agent import ScreenplayAgentRuntimeRequest


def _writing_request(**overrides):
    return ChatStreamRequest(
        messages=[{"role": "user", "content": "处理当前书籍"}],
        apiKey="test-key",
        options={
            "model": "test-model",
            "model_profile": "deepseek:deepseek-v4-flash",
        },
        bookId="book-1",
        chatAgentMode="agent",
        enableAgentTools=True,
        **overrides,
    )


def test_writing_agent_mode_and_tools_keep_core_auto_default():
    request = to_writing_agent_request(
        _writing_request(),
        {"model": "test-model"},
    )

    assert request.planning_mode is PlanningMode.AUTO


def test_writing_request_can_explicitly_select_reactive_execution():
    request = to_writing_agent_request(
        _writing_request(planningMode="reactive"),
        {"model": "test-model"},
    )

    assert request.planning_mode is PlanningMode.REACTIVE


def test_writing_request_can_explicitly_select_planned_execution():
    request = to_writing_agent_request(
        _writing_request(planningMode="planned"),
        {"model": "test-model"},
    )

    assert request.planning_mode is PlanningMode.PLANNED


def test_screenplay_root_uses_auto_planning():
    runtime = ScreenplayAgentRuntimeRequest(
        apiKey="test-key",
        options={
            "model": "test-model",
            "model_profile": "deepseek:deepseek-v4-flash",
        },
        contextWindow="128k",
    )

    request = _root_request(
        {
            "id": "turn-1",
            "projectId": "project-1",
            "sessionId": 1,
            "userContent": "创建剧本阶段产物",
        },
        runtime,
    )

    assert request.planning_mode is PlanningMode.AUTO
