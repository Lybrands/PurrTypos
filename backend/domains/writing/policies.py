"""Host-enforced policies for every PurrTypos writing tool.

The model-facing ``SKILL.md`` files describe what a tool accepts.  This
module is the writing domain's authoritative decision about what the host may
execute directly, what only produces a reviewable proposal, and what requires
an explicit one-shot approval.
"""

from __future__ import annotations

from collections.abc import Iterable

from purra.contracts import (
    ToolExecutionMode,
    ToolPolicy,
    ToolRiskLevel,
)


WRITING_TOOL_POLICIES: dict[str, ToolPolicy] = {
    # Read-only tools.
    "searchNovelKnowledge": ToolPolicy(ToolExecutionMode.READ, "检索创作资料"),
    "readNovelKnowledge": ToolPolicy(ToolExecutionMode.READ, "读取创作资料来源"),
    "getChapterContent": ToolPolicy(ToolExecutionMode.READ, "读取章节正文"),
    "listWritingChapters": ToolPolicy(ToolExecutionMode.READ, "查看写作章节"),
    "batchGetChapterContents": ToolPolicy(ToolExecutionMode.READ, "批量读取章节"),
    "getBookCharacters": ToolPolicy(ToolExecutionMode.READ, "读取人物设定"),
    "listBookCharacters": ToolPolicy(ToolExecutionMode.READ, "查看人物列表"),
    "getStoryHealthDashboard": ToolPolicy(ToolExecutionMode.READ, "读取故事健康度"),
    "getWritingStatsDashboard": ToolPolicy(ToolExecutionMode.READ, "读取写作统计"),
    "searchSparkIdeas": ToolPolicy(ToolExecutionMode.READ, "检索设定与伏笔"),
    "searchMemories": ToolPolicy(ToolExecutionMode.READ, "检索长期记忆"),
    "queryOutline": ToolPolicy(ToolExecutionMode.READ, "读取大纲"),
    "getGlobalOutline": ToolPolicy(ToolExecutionMode.READ, "读取总纲"),
    "listOutlines": ToolPolicy(ToolExecutionMode.READ, "查看大纲列表"),
    "listSettingEntities": ToolPolicy(ToolExecutionMode.READ, "查看世界设定列表"),
    "getSettingEntities": ToolPolicy(ToolExecutionMode.READ, "读取世界设定"),
    "getStoryBackground": ToolPolicy(ToolExecutionMode.READ, "读取故事背景"),
    "searchWritingTechniques": ToolPolicy(ToolExecutionMode.READ, "检索已授权写作技法"),
    "readWritingTechnique": ToolPolicy(ToolExecutionMode.READ, "读取写作技法文件"),
    "readContinuationSourceSection": ToolPolicy(
        ToolExecutionMode.READ, "读取冻结来源章节"
    ),

    # These handlers emit a reviewable diff and do not persist it.
    "editChapterContent": ToolPolicy(
        ToolExecutionMode.PROPOSE,
        "提议修改章节",
        ToolRiskLevel.WRITE,
    ),
    "updateCharacter": ToolPolicy(
        ToolExecutionMode.PROPOSE,
        "提议修改人物设定",
        ToolRiskLevel.WRITE,
    ),
    "updateSettingEntity": ToolPolicy(
        ToolExecutionMode.PROPOSE,
        "提议修改世界设定",
        ToolRiskLevel.WRITE,
    ),
    "editStoryBackground": ToolPolicy(
        ToolExecutionMode.PROPOSE,
        "提议修改故事背景",
        ToolRiskLevel.WRITE,
    ),

    # Direct persistence requires an explicit desktop approval.
    "createWritingChapter": ToolPolicy(
        ToolExecutionMode.CONFIRM,
        "创建写作章节",
        ToolRiskLevel.WRITE,
    ),
    "createCharacter": ToolPolicy(
        ToolExecutionMode.CONFIRM,
        "创建人物",
        ToolRiskLevel.WRITE,
    ),
    "deleteCharacter": ToolPolicy(
        ToolExecutionMode.CONFIRM,
        "删除人物",
        ToolRiskLevel.DESTRUCTIVE,
    ),
    "addSparkIdea": ToolPolicy(
        ToolExecutionMode.CONFIRM,
        "新增设定",
        ToolRiskLevel.WRITE,
    ),
    "updateSparkIdea": ToolPolicy(
        ToolExecutionMode.CONFIRM,
        "修改设定",
        ToolRiskLevel.WRITE,
    ),
    "deleteSparkIdea": ToolPolicy(
        ToolExecutionMode.CONFIRM,
        "删除设定",
        ToolRiskLevel.DESTRUCTIVE,
    ),
    "addForeshadowing": ToolPolicy(
        ToolExecutionMode.CONFIRM,
        "新增伏笔",
        ToolRiskLevel.WRITE,
    ),
    "createMemory": ToolPolicy(
        ToolExecutionMode.CONFIRM,
        "保存长期记忆",
        ToolRiskLevel.WRITE,
    ),
    "updateMemory": ToolPolicy(
        ToolExecutionMode.CONFIRM,
        "修改长期记忆",
        ToolRiskLevel.WRITE,
    ),
    "archiveMemory": ToolPolicy(
        ToolExecutionMode.CONFIRM,
        "归档长期记忆",
        ToolRiskLevel.WRITE,
    ),
    "linkMemories": ToolPolicy(
        ToolExecutionMode.CONFIRM,
        "关联长期记忆",
        ToolRiskLevel.WRITE,
    ),
    "resolveForeshadowing": ToolPolicy(
        ToolExecutionMode.CONFIRM,
        "标记伏笔已回收",
        ToolRiskLevel.WRITE,
    ),
    "editGlobalOutline": ToolPolicy(
        ToolExecutionMode.CONFIRM,
        "修改总纲",
        ToolRiskLevel.WRITE,
    ),
    "updateOutline": ToolPolicy(
        ToolExecutionMode.CONFIRM,
        "修改大纲",
        ToolRiskLevel.WRITE,
    ),
    "createSettingEntity": ToolPolicy(
        ToolExecutionMode.CONFIRM,
        "创建世界设定",
        ToolRiskLevel.WRITE,
    ),
    "deleteSettingEntity": ToolPolicy(
        ToolExecutionMode.CONFIRM,
        "删除世界设定",
        ToolRiskLevel.DESTRUCTIVE,
    ),
}


def policy_coverage(tool_names: Iterable[str]) -> tuple[set[str], set[str]]:
    """Return ``(unclassified_tools, policies_without_tools)``."""

    registered = {
        str(name).strip()
        for name in tool_names
        if str(name).strip()
    }
    policy_names = set(WRITING_TOOL_POLICIES)
    return registered - policy_names, policy_names - registered
