from __future__ import annotations

from dataclasses import replace

import pytest

from application.shared_agent_context import (
    AGENT_FINAL_RESPONSE_CONTEXT,
    AGENT_PUBLIC_PROGRESS_CONTEXT,
    SharedAgentContextProvider,
)
from domains.agent_policy import (
    build_agent_final_response_policy,
    build_agent_public_progress_policy,
)
from domains.novel_analysis_prompts import build_novel_analysis_method_guidance
from domains.screenplay_agent.prompts import build_screenplay_planning_policy
from domains.writing.prompts import build_writing_planning_policy
from purra.contracts import (
    AgentMessage,
    AgentRunRequest,
    ContextBlock,
    ContextBudget,
    ContextBundle,
    DomainContext,
    ModelRequest,
    TaskContextRequest,
    TaskSpec,
)
from purra.errors import ContractViolationError
from purra.api import PlanningMode


class _DomainContextProvider:
    async def build_context(self, request, budget, signal=None):
        del request, budget, signal
        return _domain_bundle("execution")

    async def build_planning_context(self, request, budget, signal=None):
        del request, budget, signal
        return _domain_bundle("planning")

    async def build_task_context(self, request, budget, task, signal=None):
        del request, budget, task, signal
        return _domain_bundle("task")


def _domain_bundle(phase: str) -> ContextBundle:
    return ContextBundle(
        blocks=(ContextBlock(
            name="domain_policy",
            content=f"domain:{phase}",
            token_count=1,
            untrusted=False,
        ),),
        diagnostics={"phase": phase},
    )


def _request() -> AgentRunRequest:
    return AgentRunRequest(
        messages=(AgentMessage(role="user", content="test"),),
        model=ModelRequest(provider="fixture", model="model"),
        domain_context=DomainContext(namespace="test.domain"),
        tools_enabled=True,
    )


def _budget() -> ContextBudget:
    return ContextBudget(
        window_tokens=32_768,
        output_reserve_tokens=4_096,
        safety_reserve_tokens=1_024,
        runtime_reserve_tokens=1_024,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", list(PlanningMode))
async def test_shared_policy_is_phase_aware_and_domain_independent(mode):
    provider = SharedAgentContextProvider(_DomainContextProvider())
    request = replace(_request(), planning_mode=mode)

    planning = await provider.build_planning_context(
        request, _budget()
    )
    execution = await provider.build_context(request, _budget())
    task = await provider.build_task_context(
        request,
        _budget(),
        TaskContextRequest(task_spec=TaskSpec(goal="complete test")),
    )

    assert [block.name for block in planning.blocks] == [
        "domain_policy",
    ]
    assert [block.name for block in execution.blocks] == [
        AGENT_PUBLIC_PROGRESS_CONTEXT,
        AGENT_FINAL_RESPONSE_CONTEXT,
        "domain_policy",
    ]
    assert [block.name for block in task.blocks] == [
        AGENT_PUBLIC_PROGRESS_CONTEXT,
        AGENT_FINAL_RESPONSE_CONTEXT,
        "domain_policy",
    ]
    assert execution.blocks[1].content == build_agent_final_response_policy()
    assert AGENT_FINAL_RESPONSE_CONTEXT not in {
        block.name for block in planning.blocks
    }
    assert planning.diagnostics == {"phase": "planning"}
    assert execution.diagnostics == {"phase": "execution"}
    assert task.diagnostics == {"phase": "task"}


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["execution", "task"])
async def test_internal_reactive_units_do_not_receive_public_or_planner_policy(phase):
    provider = SharedAgentContextProvider(_DomainContextProvider())
    request = replace(
        _request(), planning_mode=PlanningMode.REACTIVE,
        metadata={"responseAudience": "internal"},
    )
    bundle = (
        await provider.build_context(request, _budget()) if phase == "execution"
        else await provider.build_task_context(
            request, _budget(), TaskContextRequest(task_spec=TaskSpec(goal="part"))
        )
    )
    assert [block.name for block in bundle.blocks] == ["domain_policy"]


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["execution", "task"])
async def test_internal_artifact_can_expose_progress_without_a_public_final_response(phase):
    provider = SharedAgentContextProvider(_DomainContextProvider())
    request = replace(
        _request(), planning_mode=PlanningMode.REACTIVE,
        tools_enabled=True,
        metadata={"responseAudience": "internal", "progressAudience": "public"},
    )
    bundle = (
        await provider.build_context(request, _budget()) if phase == "execution"
        else await provider.build_task_context(
            request, _budget(), TaskContextRequest(task_spec=TaskSpec(goal="part"))
        )
    )
    assert [block.name for block in bundle.blocks] == [
        AGENT_PUBLIC_PROGRESS_CONTEXT, "domain_policy",
    ]
    assert bundle.blocks[0].content == build_agent_public_progress_policy()


@pytest.mark.asyncio
async def test_public_reactive_response_keeps_only_applicable_shared_policy():
    provider = SharedAgentContextProvider(_DomainContextProvider())
    for tools_enabled in (False, True):
        bundle = await provider.build_context(replace(
            _request(), planning_mode=PlanningMode.REACTIVE, tools_enabled=tools_enabled,
        ), _budget())
        assert [block.name for block in bundle.blocks] == [
            *([AGENT_PUBLIC_PROGRESS_CONTEXT] if tools_enabled else []),
            AGENT_FINAL_RESPONSE_CONTEXT, "domain_policy",
        ]


@pytest.mark.asyncio
async def test_domain_cannot_replace_a_shared_policy_block():
    class _CollisionProvider:
        async def build_context(self, request, budget, signal=None):
            del request, budget, signal
            return ContextBundle(blocks=(ContextBlock(
                name=AGENT_FINAL_RESPONSE_CONTEXT,
                content="domain override",
                token_count=1,
            ),))

    provider = SharedAgentContextProvider(_CollisionProvider())

    with pytest.raises(
        ContractViolationError,
        match="domain context cannot replace shared Agent policy",
    ):
        await provider.build_context(_request(), _budget())


@pytest.mark.parametrize(
    "domain_policy",
    (
        build_writing_planning_policy(),
        build_novel_analysis_method_guidance(),
        build_screenplay_planning_policy(),
    ),
)
def test_domain_policy_does_not_restate_shared_agent_behavior(domain_policy):
    for shared_marker in (
        "【公开工作进展规则】",
        "【最终答复规则】",
        "Root 任务成功完成时",
    ):
        assert shared_marker not in domain_policy
