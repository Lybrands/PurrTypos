from __future__ import annotations

import json

import pytest

from application.agent_composition import get_agent_composition
from application.model_runtime import model_request_from_runtime
from infrastructure.persistence.run_store import create_run
from purra.api import AgentPlanner
from purra.contracts import (
    AgentMessage,
    AgentRunRequest,
    DomainContext,
    PlanningCapabilities,
    PlanningKind,
)
from schemas.screenplay_agent import ScreenplayAgentRuntimeRequest
from tests.test_agent_run_queries import temp_db


def _request() -> AgentRunRequest:
    return AgentRunRequest(
        messages=(AgentMessage(role="user", content="test"),),
        model=model_request_from_runtime(ScreenplayAgentRuntimeRequest(
            apiKey="test-key",
            options={
                "model": "test-model",
                "model_profile": "deepseek:deepseek-v4-flash",
                "max_tokens": 2048,
            },
            contextWindow="128k",
        )),
        domain_context=DomainContext(namespace="test"),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", ["writing", "novel_analysis", "screenplay"])
async def test_every_product_uses_the_core_planner_without_a_host_wrapper(
    temp_db,
    profile,
):
    composition = get_agent_composition()
    core = composition.create_core("test-key", agent_profile=profile)
    try:
        assert isinstance(core._planner, AgentPlanner)
        assert not hasattr(core._planner, "planner")
    finally:
        composition.release_core(core)
        await core.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", ["writing", "novel_analysis", "screenplay"])
async def test_every_product_planner_uses_the_versioned_provider_stream(
    temp_db,
    monkeypatch,
    profile,
):
    composition = get_agent_composition()
    calls = []

    async def stream(_key, messages, options, _provider, signal=None):
        calls.append((messages, options, signal))
        wire = json.dumps({
            "v": 1,
            "type": "plan",
            "plan": {
                "needsTodos": False,
                "reason": "direct response",
            },
        }, separators=(",", ":")) + "\n"

        async def chunks():
            yield {
                "choices": [{
                    "delta": {"content": wire},
                    "finish_reason": "stop",
                }],
            }

        return {
            "applied_output_limit": options.get("max_tokens"),
            "stream": chunks(),
            "model": "test-model",
        }

    monkeypatch.setattr(
        "infrastructure.models.provider_router.create_chat_stream",
        stream,
    )
    core = composition.create_core("test-key", agent_profile=profile)
    try:
        core._planner._result_validator = None
        run_id = await create_run(
            temp_db,
            session_id=None,
            prompt="planner stream",
            mode="agent",
        )
        result = await core._planner.create_plan(
            _request(),
            PlanningCapabilities(
                available_tool_names=(),
                model_supports_tools=True,
            ),
            run_id=run_id,
        )
        assert result.kind is PlanningKind.DIRECT_RESPONSE
        assert [step.id for step in result.work_plan.steps] == ["respond"]
        assert len(calls) == 1
        assert any(
            "purra.planning-stream/v1" in str(message.get("content") or "")
            for message in calls[0][0]
        )
        assert calls[0][1].get("tools") is None
    finally:
        composition.release_core(core)
        await core.close()
