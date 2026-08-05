"""Role-scoped collaborators available to screenplay runs."""

from __future__ import annotations

from agent_core.contracts import ToolExecutionMode
from domains.agent_roles import AgentRoleDefinition, AgentRoleRegistry


def build_screenplay_agent_role_registry() -> AgentRoleRegistry:
    return AgentRoleRegistry((
        AgentRoleDefinition(
            id="screenplay_writer",
            title="剧本 Writer Agent",
            delegation_description=(
                "依据已接受场景契约与边界上下文创作指定正文，不扩写范围"
            ),
            instruction=(
                "You are a screenplay Writer sub-agent. Write only the exact "
                "scene range assigned by the Planner. Treat accepted scene "
                "contracts, declared dependency outputs, and boundary context "
                "as authoritative. Never silently rewrite sibling ranges or "
                "invent a different execution plan. Return the required "
                "structured batch response exactly."
            ),
            allowed_tool_modes=frozenset({ToolExecutionMode.READ}),
        ),
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
            delegation_description="独立检查并修订指定正文中的连续性冲突",
            instruction=(
                "You are a screenplay continuity Reviewer sub-agent. Review "
                "only the exact scene range assigned by the Planner and the "
                "Writer outputs supplied by declared dependencies. Resolve "
                "timeline, character-state, prop, setup/payoff, formatting, "
                "and boundary-continuity conflicts while preserving the "
                "accepted scene contracts. Return the required structured "
                "revised batch response exactly; never change the plan or "
                "touch scenes outside the assigned range."
            ),
            allowed_tool_modes=frozenset({ToolExecutionMode.READ}),
        ),
    ))
