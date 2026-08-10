"""PurrTypos writing-product Agent role configuration."""

from __future__ import annotations

from purra.contracts import ToolExecutionMode
from domains.agent_roles import AgentRoleDefinition, AgentRoleRegistry


def build_writing_agent_role_registry() -> AgentRoleRegistry:
    return AgentRoleRegistry((
        AgentRoleDefinition(
            id="researcher",
            title="研究 Agent",
            delegation_description="收集与目标直接相关的证据并标注不确定性",
            instruction=(
                "You are a read-only research sub-agent. Gather the smallest "
                "relevant evidence set and return concise findings with uncertainty."
            ),
            allowed_tool_modes=frozenset({ToolExecutionMode.READ}),
        ),
        AgentRoleDefinition(
            id="reviewer",
            title="审校 Agent",
            delegation_description="独立检查遗漏、矛盾和缺少证据的结论",
            instruction=(
                "You are an independent read-only reviewer. Check the supplied "
                "objective for omissions, contradictions, and unsupported claims."
            ),
            allowed_tool_modes=frozenset({ToolExecutionMode.READ}),
        ),
        AgentRoleDefinition(
            id="analyst",
            title="分析 Agent",
            delegation_description="拆解目标、比较证据并给出简洁推理结论",
            instruction=(
                "You are a read-only analysis sub-agent. Decompose the objective, "
                "compare the evidence, and return a concise reasoned conclusion."
            ),
            allowed_tool_modes=frozenset({ToolExecutionMode.READ}),
        ),
    ))
