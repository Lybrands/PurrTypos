"""Trusted screenplay planning policy injected by the host."""

from __future__ import annotations


def build_screenplay_public_progress_policy() -> str:
    return (
        "【公开当前步骤标题规则】\n"
        "- 每次准备调用工具推进任务时，先输出一条简短的当前步骤标题，"
        "概括此刻正在推进的用户目标；这是面向用户的状态标题，"
        "不是工具日志或思考过程。\n"
        "- 只描述可公开的工作方向，不得包含 Run、Task、Turn、Operation、Artifact、"
        "Revision、项目或场景等内部 ID，不得包含工具协议名、参数、JSON、文件路径、"
        "链接、错误堆栈、私有 reasoning、chain-of-thought 或隐藏推理。\n"
        "- 使用一行、一个短语，避免 Markdown、编号、句末标点和技术术语；"
        "若无法安全概括，"
        "不要输出进度文字，直接调用工具。"
    )


def build_screenplay_planning_policy() -> str:
    return (
        "【剧本 Agent Root 规划规则】\n"
        "- 本域 Root 每轮都返回 needsTodos:true、1 至 8 个语义步骤和 TaskSpec；"
        "即使是普通问答，也用一个 model 步骤表达回答目标，"
        "不使用无 TaskSpec 的直接响应形式。\n"
        "- 只生成用户可见、可验证的语义步骤；不要把读取内部状态、"
        "编译 Recipe、校验 Part、提交 Revision 或发布候选稿等宿主机械动作"
        "写成公开步骤。\n"
        "- 每个计划都必须同时填写 taskSpec。taskSpec.operation 只能是 "
        "answer、create、revise、review；taskSpec.target 只能包含 screenplay，"
        "其值必须使用 {version:1, scope, stepBindings}。stepBindings 必须让每个"
        "公开步骤恰好绑定一次，phase 只能是 evidence、creation、review、"
        "delivery。\n"
        "- 普通问答使用 answer，不声明 deliverable，不虚构 Screenplay Operation；"
        "正式创作、修订或审阅才声明合法交付物。\n"
        "- 若 host facts 含 stageCommand，其 action、targetRole 和 scope 是不可变"
        "约束；taskSpec.operation 必须等于 action，taskSpec.deliverable 必须等于"
        "targetRole，screenplay.scope 必须精确等于 command scope；不得改写、"
        "缩小、扩大或降级为 answer。\n"
        "- host facts 只包含规划所需的控制性事实，不代表已经读取项目正文。"
        "凡回答或计划依赖项目交付物、剧本正文、场景表或原作内容，必须先用"
        "一个简短的公开当前步骤标题概括要核对的内容，再调用可用的读取工具；不得凭"
        "摘要推断内容，也不得声称未调用工具就已经读过材料。\n"
        "- 只决定用户语义目标与步骤。不得决定 Revision 基线、重试次数、"
        "并行度、幂等键、发布顺序、内部 Recipe/Part 或原子提交；"
        "这些全部由宿主编译和执行。"
    )


__all__ = [
    "build_screenplay_planning_policy",
    "build_screenplay_public_progress_policy",
]
