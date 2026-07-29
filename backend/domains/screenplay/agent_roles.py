"""Initial read-only roles for screenplay runs."""

from __future__ import annotations

from agent_core.contracts import ToolExecutionMode
from domains.agent_roles import AgentRoleDefinition, AgentRoleRegistry


def build_screenplay_agent_role_registry() -> AgentRoleRegistry:
    return AgentRoleRegistry((
        AgentRoleDefinition(
            id="dramaturg",
            title="剧作顾问 Agent",
            delegation_description="检查戏剧目标、冲突、结构与创作取舍",
            instruction=(
                "You are a read-only dramaturg. Evaluate dramatic goals, "
                "conflict, structure, and unresolved creative choices."
            ),
            allowed_tool_modes=frozenset({ToolExecutionMode.READ}),
        ),
        AgentRoleDefinition(
            id="screenplay_reviewer",
            title="剧本审阅 Agent",
            delegation_description="独立检查剧本方案中的遗漏、矛盾与不可执行表述",
            instruction=(
                "You are a read-only screenplay reviewer. Identify omissions, "
                "contradictions, and proposals that are not yet actionable."
            ),
            allowed_tool_modes=frozenset({ToolExecutionMode.READ}),
        ),
    ))
