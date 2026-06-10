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
    "createCharacter",
    "updateCharacter",
    "editStoryBackground",
}

# 写意图判定的代价是不对称的：
# - 误保留写工具（false positive）只是工具在场，常驻 prompt 仍约束"未经确认不写入"；
# - 误移除写工具（false negative）会让用户明确要求写入时模型无工具可用，体验直接破。
# 因此宁可放宽：命中任何"改/写/存"类动词即保留写工具。
WRITE_INTENT_RE = re.compile(
    r"(写入|保存|落稿|应用|覆盖|写进|存进|存到|改成|改为|改掉|改一下|修改|更新|重写|"
    r"删掉|删除|加上|加进|新增|添加|记下|记录|沉淀|动笔|落笔|写到)"
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
        "若用户**明确要求**一次性全文、跳过协商、直接成稿，则服从其指令，仍可分段排版以便阅读。\n"
        "当用户在对话中明确表示确认（如同意当前提案、要求继续/推进）时，直接推进下一小节，"
        "不要反复征求同意；注意区分肯定与否定（「不同意」「先别写」是否定，应回到协商）。\n\n"
        "输出结构建议（Markdown 小标题即可）：\n"
        "- **协商问题**（需要用户拍板或补充的信息）\n"
        "- **当前提案**（结构、情节走向或本段草案，保持克制篇幅）\n"
        "- **下一步选择**（A/B 或「确认后写下一小节」等）\n\n"
        f"{challenge_line}\n"
        "可调用工具读取本书设定、大纲与章节；**未经用户明确「写入/保存/应用到章节」等意图时，"
        "不要调用会改写书稿或记忆库的工具**（系统可能已从工具列表中移除这些项）。"
    )


def build_collab_turn_appendix(messages: list[dict[str, Any]]) -> str:
    """轮次级协作指引。

    只保留按对话深度的区分（首轮先对齐目标）；「是否确认/是否要求一次成稿」
    这类意图判断交给模型自己结合常驻系统约束完成 —— 旧版用关键词正则猜意图，
    「不同意」会命中「同意」分支、「不可以」会命中「可以」，比模型判断得更差。
    """
    users = [m for m in (messages or []) if m and m.get("role") == "user"]
    if len(users) <= 1:
        return (
            "【本轮协作指引】对话尚浅：本轮以澄清目标、读者预期、体裁与节奏为主，"
            "给出**结构或提纲级**提案即可，避免默认输出大段正文；"
            "若用户首条消息已明确要求直接成稿，则服从其指令。"
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
