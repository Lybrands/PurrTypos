"""Product-neutral model behavior shared by every hosted Agent."""

from __future__ import annotations

from constants import AGENT_PUBLIC_PROGRESS_PREFIX

def build_agent_public_progress_policy() -> str:
    return (
        "【进展标题】\n"
        f"调用工具前，先输出以{AGENT_PUBLIC_PROGRESS_PREFIX}开头的一行简短标题，"
        "说明当前正在推进的用户目标，然后立即调用工具；"
        "直接回答而不调用工具时不得使用此前缀。"
        "只写用户能理解的工作内容，不包含技术细节或未经验证的结果；"
        "无法恰当概括时直接执行。"
    )


def build_agent_final_response_policy() -> str:
    return (
        "【最终答复】\n"
        "任务完成后，直接回应用户目标，说明已验证的结果、覆盖范围及仍需确认的事项。"
        "只陈述实际完成的内容，使用适合当前任务的自然表达；"
        "不复述整份交付物或执行细节。"
    )


__all__ = [
    "build_agent_final_response_policy",
    "build_agent_public_progress_policy",
]
