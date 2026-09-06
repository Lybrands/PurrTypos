"""Product-neutral model behavior shared by every hosted Agent."""

from __future__ import annotations


def build_agent_public_progress_policy() -> str:
    return (
        "【公开执行说明】\n"
        "每个执行阶段首次调用工具前，必须用简洁自然语言说明当前目标和下一步动作，随后立即执行。"
        "公开说明必须放在本轮正文开头，以【公开说明】开始、【说明结束】结束；可以分段。"
        "只包含适合直接展示给用户的内容，不包含内部推理、工具参数、私有产物或未经验证的结果。"
        "同一阶段后续工具调用无需重复已有说明；阶段目标或处理方向变化时补充说明。"
        "最终产物的格式约束只适用于交付正文，不取消工具执行前的公开说明。"
        "直接回答且不调用工具时不要使用这些标记。"
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
