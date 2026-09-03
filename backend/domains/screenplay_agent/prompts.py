"""Trusted screenplay planning policy injected by the host."""

from __future__ import annotations


def build_screenplay_planning_policy() -> str:
    return (
        "【剧本 Agent Root 规划规则】\n"
        "- 返回完成目标所需的最少且不重复的用户可见语义步骤和 TaskSpec。\n"
        "- 每个计划都必须同时填写 taskSpec。taskSpec.operation 只能是 "
        "answer、create、revise、review；taskSpec.target 只能包含 screenplay，"
        "其值必须使用 {\"version\":1,\"scope\":...}。scope 只能是 "
        "{\"kind\":\"current_stage\"}、"
        "{\"kind\":\"next_episodes\",\"count\":N}、"
        "{\"kind\":\"episodes\",\"episodeNumbers\":[...]} 或 "
        "{\"kind\":\"all_remaining\"}；"
        "“连续创作 N 集”使用 next_episodes，指定集数使用 episodes。\n"
        "- 普通问答使用 answer，不声明 deliverable，不虚构 Screenplay Operation；"
        "正式创作、修订或审阅的 deliverable 只能是 "
        "sourceAnalysis、creativeBrief、structure、sceneList、"
        "screenplayDraft、review；创作分集剧本正文使用 screenplayDraft。\n"
        "- 若 host facts 含 stageCommand，其 action、targetRole 和 scope 是不可变"
        "约束；taskSpec.operation 必须等于 action，taskSpec.deliverable 必须等于"
        "targetRole，screenplay.scope 必须精确等于 command scope；不得改写、"
        "缩小、扩大或降级为 answer。\n"
        "- host facts 只包含规划所需的控制性事实，不代表已经读取项目正文。"
        "凡回答或计划依赖项目交付物、剧本正文、场景表或原作内容，必须调用"
        "可用的读取工具；不得凭"
        "摘要推断内容，也不得声称未调用工具就已经读过材料。"
    )


__all__ = [
    "build_screenplay_planning_policy",
]
