"""Localized user-facing names for Screenplay domain tools."""

from __future__ import annotations

from typing import Mapping


def _names(zh_cn: str, en_us: str) -> Mapping[str, str]:
    return {"zh-CN": zh_cn, "en-US": en_us}


SCREENPLAY_TOOL_DISPLAY_NAMES: Mapping[str, Mapping[str, str]] = {
    "analyzeSourceMaterial": _names(
        "生成原作范围分析", "Analyze Source Material"
    ),
    "generateCreativeBrief": _names(
        "生成创作简报", "Generate Creative Brief"
    ),
    "generateScreenplayStructure": _names(
        "生成剧本结构", "Generate Screenplay Structure"
    ),
    "generateSceneList": _names("生成场景表", "Generate Scene List"),
    "continueScreenplayDraft": _names(
        "继续创作剧本正文", "Continue Screenplay Draft"
    ),
    "reviewCurrentDraft": _names(
        "审阅当前剧本", "Review Current Draft"
    ),
    "reviseCurrentDraft": _names(
        "修订当前剧本", "Revise Current Draft"
    ),
    "getScreenplayProject": _names("读取剧本项目", "Read Screenplay Project"),
    "getScreenplayDocument": _names("读取剧本文档", "Read Screenplay Document"),
    "getScreenplayEpisodeContext": _names(
        "读取分集文档", "Read Screenplay Episode Context"
    ),
    "getScreenplayDraftContext": _names(
        "查看正文创作上下文", "Read Screenplay Draft Context"
    ),
    "getSourceBookOverview": _names("读取原作概览", "Read Source Overview"),
    "getSourceCoveragePlan": _names("规划原作阅读范围", "Plan Source Coverage"),
    "readSourceCoverageBatch": _names("阅读原作范围", "Read Source Coverage"),
    "searchSourceMaterial": _names("检索原作素材", "Search Source Material"),
    "readSourcePassages": _names("精读关键原文", "Read Source Passages"),
    "getSourceCharacters": _names("读取原作人物", "Read Source Characters"),
    "getSourceWorldSettings": _names(
        "读取原作世界设定", "Read Source World Settings"
    ),
    "beginSourceAnalysisArtifact": _names(
        "开始整理原作分析", "Begin Source Analysis"
    ),
    "appendSourceAnalysisBatch": _names(
        "追加原作分析条目", "Append Source Analysis Items"
    ),
    "finalizeSourceAnalysisProposal": _names(
        "完成原作范围分析", "Finalize Source Analysis"
    ),
    "beginCreativeBriefArtifact": _names(
        "开始整理创作简报", "Begin Creative Brief"
    ),
    "appendCreativeBriefBatch": _names(
        "追加创作简报条目", "Append Creative Brief Items"
    ),
    "finalizeCreativeBriefProposal": _names(
        "完成创作简报提案", "Finalize Creative Brief"
    ),
    "beginScreenplayStructureArtifact": _names(
        "开始整理剧本结构", "Begin Screenplay Structure"
    ),
    "appendScreenplayStructureBatch": _names(
        "追加剧本结构条目", "Append Screenplay Structure Items"
    ),
    "finalizeScreenplayStructureProposal": _names(
        "完成剧本结构提案", "Finalize Screenplay Structure"
    ),
    "beginSceneListArtifact": _names("开始整理场景表", "Begin Scene List"),
    "appendSceneListBatch": _names("追加场景表批次", "Append Scene List Batch"),
    "finalizeSceneListProposal": _names("完成场景表提案", "Finalize Scene List"),
    "proposeSceneDraft": _names("创作剧本正文", "Draft Screenplay Scene"),
    "beginScreenplayReviewArtifact": _names(
        "开始整理剧本审阅", "Begin Screenplay Review"
    ),
    "appendScreenplayReviewBatch": _names(
        "追加剧本审阅条目", "Append Screenplay Review Items"
    ),
    "finalizeScreenplayReviewProposal": _names(
        "完成剧本审阅报告", "Finalize Screenplay Review"
    ),
    "beginScreenplayRevisionArtifact": _names(
        "开始整理剧本修订", "Begin Screenplay Revision"
    ),
    "appendScreenplayRevisionBatch": _names(
        "追加剧本修订场景", "Append Screenplay Revisions"
    ),
    "appendScreenplayRevisionResolutionBatch": _names(
        "追加审阅问题回写", "Append Review Resolutions"
    ),
    "finalizeScreenplayRevisionProposal": _names(
        "完成剧本修订提案", "Finalize Screenplay Revision"
    ),
}


__all__ = ["SCREENPLAY_TOOL_DISPLAY_NAMES"]
