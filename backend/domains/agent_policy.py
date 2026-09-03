"""Product-neutral model behavior shared by every hosted Agent."""

from __future__ import annotations

def build_agent_public_progress_policy() -> str:
    return (
        "【公开工作进展规则】\n"
        "- 每次准备调用工具推进任务时，先输出一条简短的当前步骤标题，"
        "概括此刻正在推进的用户目标；这是面向用户的状态标题，"
        "不是工具日志或思考过程。\n"
        "- 只描述可公开的工作方向，不得包含 Run、Task、Turn、Operation、Artifact、"
        "Revision、项目或场景等内部 ID，不得包含工具协议名、参数、JSON、文件路径、"
        "链接、错误堆栈、私有 reasoning、chain-of-thought 或隐藏推理。\n"
        "- 使用一行、一个短语，避免 Markdown、编号、句末标点和技术术语；"
        "若无法安全概括，不要输出进度文字，直接调用工具。\n"
        "- 纯模型阶段的公开进展由运行环境的独立通道承载；不要为了模拟阶段而改写"
        "普通回答正文，也不要把隐藏推理、预计步骤或尚未发生的结果描述成进展。"
    )


def build_agent_final_response_policy() -> str:
    return (
        "【最终答复规则】\n"
        "- 面向用户的 Root 任务成功完成时，必须给出最终答复，直接回应用户原始目标；"
        "复杂任务应清楚归纳已经完成的结果、覆盖范围以及仍存在的限制或待确认事项。\n"
        "- 只能把已经执行并得到验证的结果写成完成；计划意图、准备动作、进行中步骤和"
        "失败结果不得改写成已经完成。\n"
        "- 不得暴露内部 ID、工具协议、计划 JSON、私有状态或隐藏推理；正文和候选产物"
        "继续遵守产品既有交付边界，不为总结而复制整份产物。\n"
        "- 不规定固定句数、标题或模板；答复结构和篇幅应由用户目标与真实结果决定。"
    )


__all__ = [
    "build_agent_final_response_policy",
    "build_agent_public_progress_policy",
]
