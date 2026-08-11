"""Paid release gate for the model-agnostic screenplay protocol.

These tests intentionally skip with a release-blocker message when credentials
are absent.  A Fake Gateway contract is useful, but never counts as this gate.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

import pytest
import pytest_asyncio

from database.connection import DatabaseConnection
from infrastructure.models.provider_model_gateway import ProviderModelGateway
from infrastructure.models.profiles.registry import resolve_model_profile
from purra.contracts import (
    AgentMessage,
    MessageRole,
    ModelFinishReason,
    ModelInvocation,
    ModelRequest,
    ReasoningMode,
)
from purra.model_protocol import resolve_invocation_output_limit
from purra.api import AgentModelTask, AgentModelTaskRunner
from purra.model_invocation import AgentModelInvocationManager, ModelInvocationContext
from tests.test_screenplay_agent_durable_service import _finalization_fixture


@dataclass(frozen=True)
class LiveCase:
    id: str
    key_env: str
    provider: str
    profile_id: str
    model: str
    base_url: str
    reasoning_mode: ReasoningMode
    thinking: str


LIVE_CASES = (
    LiveCase(
        id="deepseek-v4-flash-reasoning-on",
        key_env="DEEPSEEK_API_KEY",
        provider="openai",
        profile_id="deepseek:deepseek-v4-flash",
        model="deepseek-v4-flash",
        base_url="https://api.deepseek.com/v1",
        reasoning_mode=ReasoningMode.DEFAULT,
        thinking="enabled",
    ),
    LiveCase(
        id="deepseek-v4-flash-reasoning-off",
        key_env="DEEPSEEK_API_KEY",
        provider="openai",
        profile_id="deepseek:deepseek-v4-flash",
        model="deepseek-v4-flash",
        base_url="https://api.deepseek.com/v1",
        reasoning_mode=ReasoningMode.DISABLED,
        thinking="disabled",
    ),
    LiveCase(
        id="glm-5.2",
        key_env="ZAI_API_KEY",
        provider="zai",
        profile_id="zai:glm-5.2",
        model="glm-5.2",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        reasoning_mode=ReasoningMode.DEFAULT,
        thinking="enabled",
    ),
    LiveCase(
        id="mimo-v2.5-pro",
        key_env="MIMO_API_KEY",
        provider="openai",
        profile_id="mimo:mimo-v2.5-pro",
        model="mimo-v2.5-pro",
        base_url="https://api.xiaomimimo.com/v1",
        reasoning_mode=ReasoningMode.DEFAULT,
        thinking="enabled",
    ),
)


@pytest_asyncio.fixture
async def real_screenplay_db(tmp_path: Path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    try:
        yield db
    finally:
        await db.close()


@pytest.mark.real_provider
@pytest.mark.asyncio
@pytest.mark.parametrize("case", LIVE_CASES, ids=lambda case: case.id)
async def test_live_profile_completes_screenplay_candidate_revision_and_replay(
    case: LiveCase,
    real_screenplay_db,
):
    api_key = str(os.getenv(case.key_env) or "").strip()
    if not api_key:
        pytest.skip(
            f"RELEASE BLOCKER: {case.key_env} is missing; "
            f"{case.id} screenplay E2E was not executed"
        )
    profile = resolve_model_profile(case.profile_id, case.model, case.base_url)
    snapshot = profile.capability_snapshot(context_window_tokens=1_000_000)
    invocation = ModelInvocation(
        request=ModelRequest(
            provider=case.provider,
            model=case.model,
            capability_snapshot=snapshot,
            options={
                "baseURL": case.base_url,
                "model_profile": case.profile_id,
                "thinking": {"type": case.thinking},
            },
        ),
        output_limit=resolve_invocation_output_limit(
            snapshot,
            explicit_user_override=512,
        ),
        reasoning_mode=case.reasoning_mode,
    )
    completion = await ProviderModelGateway(api_key).complete(
        (AgentMessage(
            role=MessageRole.USER,
            content=(
                "用一句中文确认：已完成剧本候选稿。"
                "不要使用 Markdown，不要补充其他内容。"
            ),
        ),),
        invocation,
    )
    assert completion.finish_reason is ModelFinishReason.STOP
    assert str(completion.message.content or "").strip()
    assert completion.usage is not None
    assert completion.usage.input_tokens > 0
    assert completion.usage.output_tokens > 0

    if case.id == "deepseek-v4-flash-reasoning-on":
        structured_request = ModelRequest(
            provider=case.provider,
            model=case.model,
            capability_snapshot=snapshot,
            options={
                "baseURL": case.base_url,
                "model_profile": case.profile_id,
                "thinking": {"type": case.thinking},
                "response_format": {"type": "json_object"},
            },
        )
        structured = await AgentModelTaskRunner(
            AgentModelInvocationManager(ProviderModelGateway(api_key)),
            ModelInvocationContext(run_id=f"live-e2e-{case.id}"),
        ).stream_text(
            (AgentMessage(
                role=MessageRole.USER,
                content=(
                    '只输出一个 JSON 对象：{"status":"ok"}。'
                    "不要输出 Markdown 或额外文字。"
                ),
            ),),
            AgentModelTask(
                request=structured_request,
                output_limit=resolve_invocation_output_limit(
                    snapshot,
                    explicit_user_override=512,
                ),
                reasoning_mode=case.reasoning_mode,
            ),
        )
        parsed = json.loads(structured.content)
        assert isinstance(parsed, dict)
        assert 1 <= structured.attempts <= 3

    operation, turn_id, command, finalizer = await _finalization_fixture(
        real_screenplay_db
    )
    first = await finalizer.finalize(command)
    replay = await finalizer.finalize(command)
    assert replay == first
    assert first.operation_id == operation.id
    assert await real_screenplay_db.fetch_one(
        "SELECT status, result_revision_id, finalization_receipt_id "
        "FROM screenplay_agent_operations WHERE id = ?",
        [operation.id],
    ) == {
        "status": "succeeded",
        "result_revision_id": first.revision_id,
        "finalization_receipt_id": first.id,
    }
    assert await real_screenplay_db.fetch_one(
        "SELECT status, assistant_content FROM screenplay_agent_turns "
        "WHERE id = ?",
        [turn_id],
    ) == {
        "status": "completed",
        "assistant_content": "创作简报已经完成，可以在候选稿中查看。",
    }
