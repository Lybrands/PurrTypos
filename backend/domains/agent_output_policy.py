"""Product-neutral public progress rules shared by every hosted Agent."""

from __future__ import annotations


def build_agent_public_progress_policy() -> str:
    return (
        "【公开当前步骤标题规则】\n"
        "- 每次准备调用工具推进任务时，先输出一条简短的当前步骤标题，"
        "概括此刻正在推进的用户目标；这是面向用户的状态标题，"
        "不是工具日志或思考过程。\n"
        "- 只描述可公开的工作方向，不得包含 Run、Task、Turn、Operation、Artifact、"
        "Revision、项目或场景等内部 ID，不得包含工具协议名、参数、JSON、文件路径、"
        "链接、错误堆栈、私有 reasoning、chain-of-thought 或隐藏推理。\n"
        "- 使用一行、一个短语，避免 Markdown、编号、句末标点和技术术语；"
        "若无法安全概括，不要输出进度文字，直接调用工具。"
    )


__all__ = ["build_agent_public_progress_policy"]
