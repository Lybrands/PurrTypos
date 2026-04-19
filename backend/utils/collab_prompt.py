"""
Collaboration (co-creation) mode prompts and tool guard-rails —
port of electron/collabPrompt.js.
"""

from __future__ import annotations

import re
from typing import Any

COLLAB_WRITE_TOOL_NAMES: set[str] = {
    "createWritingChapter",
    "editChapterContent",
    "editGlobalOutline",
    "updateOutline",
    "addSparkIdea",
    "updateSparkIdea",
    "deleteSparkIdea",
    "addForeshadowing",
}

WRITE_INTENT_RE = re.compile(
    r"(写入|保存|落稿|写入章节|写入本书|应用到章节|帮我保存|确认写入|覆盖保存|写进书里|保存到章节|直接改正文|改掉这一章)"
)


def build_collab_system_prompt(
    challenge_level: str = "medium",
    generation_strategy: str = "outline_then_draft",
) -> str:
    if challenge_level == "strong":
        challenge_line = "可较高频地挑战设定、逻辑与叙事选择，并给出替代方案。"
    elif challenge_level == "soft":
        challenge_line = "仅在目标含糊、约束冲突或明显不可行时，再温和反问。"
    else:
        challenge_line = (
            "在目标模糊、约束互相冲突、人物动机或视角不自洽时，"
            "主动反问并给出 1～2 个可选走向；语气专业、尊重用户。"
        )

    return (
        "【协作共创模式 — 系统约束】\n"
        "你与用户**协商式**共同写作，偏一问一答；不要默认独自写完一整章或超长正文。\n"
        "默认策略：**先对齐目标与结构（大纲/段落要点），经用户确认后再分段生成正文**；"
        "每一段后简要说明走向，并用「下一步选择」请用户定夺。\n"
        "若用户**明确要求**一次性全文、跳过协商、直接成稿，则服从其指令，仍可分段排版以便阅读。\n\n"
        "输出结构建议（Markdown 小标题即可）：\n"
        "- **协商问题**（需要用户拍板或补充的信息）\n"
        "- **当前提案**（结构、情节走向或本段草案，保持克制篇幅）\n"
        "- **下一步选择**（A/B 或「确认后写下一小节」等）\n\n"
        f"{challenge_line}\n"
        "可调用工具读取本书设定、大纲与章节；**未经用户明确「写入/保存/应用到章节」等意图时，"
        "不要调用会改写书稿或记忆库的工具**（系统可能已从工具列表中移除这些项）。"
    )


def build_collab_turn_appendix(messages: list[dict[str, Any]]) -> str:
    users = [m for m in (messages or []) if m and m.get("role") == "user"]
    n = len(users)
    last_user = str(users[-1].get("content", "")) if users else ""

    if re.search(r"全文|一次性|直接生成|不用商量|跳过协商|直接写稿|一口气写完|别问了", last_user):
        return (
            "【本轮协作指引】用户倾向跳过协商或要求一次成稿："
            "按其要求输出，仍可在末尾给出简短「若需调整可说明」的收束。"
        )

    if n <= 1:
        return (
            "【本轮协作指引】对话尚浅：本轮以澄清目标、读者预期、体裁与节奏为主，"
            "给出**结构或提纲级**提案即可，避免默认输出大段正文。"
        )

    if re.search(r"确认|可以|同意|按这个|就这样|继续写|下一段|下一节|推进|先这样|OK|ok", last_user):
        return (
            "【本轮协作指引】用户已倾向确认：可推进**下一小节**正文或修订；"
            "本段结束后仍用「下一步选择」邀请用户定调。"
        )

    return "【本轮协作指引】延续协商与递进：可指出矛盾或风险；若需新信息，放在「协商问题」中逐条追问。"


def filter_collab_tools(tools: list[dict], user_text: str) -> list[dict]:
    if not tools:
        return tools
    if WRITE_INTENT_RE.search(user_text or ""):
        return tools
    return [
        t for t in tools
        if not (isinstance(t.get("function", {}).get("name"), str)
                and t["function"]["name"] in COLLAB_WRITE_TOOL_NAMES)
    ]
