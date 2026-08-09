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
                "scene range assigned by the host workflow. Treat accepted scene "
                "contracts, declared dependency outputs, and boundary context "
                "as authoritative. The host has already assembled the exact "
                "draft context; do not request or invent additional scope. "
                "Never silently rewrite sibling ranges or "
                "invent a different execution plan. Return the required "
                "structured batch response exactly."
            ),
            # Role definitions keep a non-empty capability contract, while
            # durable child runs explicitly disable the tool catalog. This
            # lets the shared registry validate the role without reopening a
            # planning/tool loop inside the host-orchestrated unit.
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
            delegation_description=(
                "全局检查已生成场景的跨集连续性，只返回紧凑问题清单"
            ),
            instruction=(
                "You are a screenplay continuity Reviewer sub-agent. Review "
                "only the exact scene range assigned by the host workflow and the "
                "Writer outputs supplied by declared dependencies. The host "
                "has already assembled the authoritative range. Identify "
                "timeline, character-state, prop, setup/payoff, formatting, "
                "and boundary-continuity conflicts while preserving the "
                "accepted scene contracts. Return only the required compact "
                "review report. Do not reproduce or rewrite screenplay prose; "
                "never change the plan or touch scenes outside the assigned range."
            ),
            allowed_tool_modes=frozenset({ToolExecutionMode.READ}),
        ),
        AgentRoleDefinition(
            id="screenplay_rewriter",
            title="剧本修订 Agent",
            delegation_description=(
                "仅根据已验证审阅问题修订被点名的场景，不重写无关正文"
            ),
            instruction=(
                "You are a screenplay Rewriter sub-agent. Rewrite only the "
                "single host-bound scene and only when the supplied review "
                "issues require a change. Preserve its accepted scene contract, "
                "resolve every supplied instruction, and return the required "
                "structured scene response exactly. Never broaden the scope, "
                "re-plan the workflow, or rewrite adjacent scenes."
            ),
            allowed_tool_modes=frozenset({ToolExecutionMode.READ}),
        ),
    ))
