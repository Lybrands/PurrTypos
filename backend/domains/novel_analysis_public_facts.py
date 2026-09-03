"""Public facts for the model-authored novel analysis conclusion."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from purra.contracts import AgentRunResult, RunStatus
from purra.output import PublicFact, PublicFactBundle

from application.novel_analysis_artifacts import NovelAnalysisArtifactStore
from domains.agent_policy import build_agent_final_response_policy
from domains.novel_analysis import NOVEL_ANALYSIS_ARTIFACT_REF_PREFIX


@dataclass(frozen=True, slots=True)
class NovelAnalysisPublicFactsProvider:
    db: Any

    async def facts_for(
        self,
        run_id: str,
        result: AgentRunResult,
    ) -> PublicFactBundle:
        if not isinstance(result, AgentRunResult):
            raise TypeError("novel analysis public facts require an AgentRunResult")
        if result.run_id != run_id or result.status is not RunStatus.DONE:
            raise ValueError("novel analysis public facts require the completed Run")
        artifact_ref = str(result.final_response or "").strip()
        if not artifact_ref.startswith(NOVEL_ANALYSIS_ARTIFACT_REF_PREFIX):
            raise ValueError("novel analysis completed result has no review Artifact")

        binding = await self.db.fetch_one(
            "SELECT r.prompt, task.id AS task_id "
            "FROM ai_agent_runs AS r "
            "JOIN ai_agent_long_task_runs AS relation ON relation.run_id = r.id "
            "JOIN ai_agent_long_tasks AS task ON task.id = relation.task_id "
            "JOIN ai_agent_long_task_units AS unit ON unit.task_id = task.id "
            "WHERE r.id = ? AND unit.unit_id = 'artifact:review' "
            "AND unit.status = 'completed' AND unit.output_ref = ? LIMIT 1",
            [run_id, artifact_ref],
        )
        if binding is None:
            raise ValueError("novel analysis review Artifact is not bound to the Run")

        artifact = await NovelAnalysisArtifactStore(self.db).require(artifact_ref)
        if str(artifact.get("metadata", {}).get("taskId") or "") != str(
            binding["task_id"]
        ):
            raise ValueError("novel analysis review Artifact task binding conflicts")
        return _public_bundle(
            artifact,
            user_request=str(binding.get("prompt") or "").strip(),
        )


def _public_bundle(
    artifact: Mapping[str, Any],
    *,
    user_request: str,
) -> PublicFactBundle:
    facts = tuple(artifact.get("facts") or ())
    cards = tuple(artifact.get("craftCards") or ())
    for fact_limit, card_limit, text_limit in (
        (10, 5, 360),
        (8, 4, 300),
        (6, 3, 240),
        (4, 2, 180),
    ):
        projected = (
            PublicFact("responseKind", "novelSourceAnalysis"),
            PublicFact("userRequest", _clip(user_request, 1_000)),
            PublicFact("presentationRequirements", (
                build_agent_final_response_policy()
                + "\n直接给出一篇连贯、结构清晰的 Markdown 分析答复；综合材料，"
                "不要逐条倾倒内部记录，也不要提及任务、产物、审核状态或工具。"
                "内容应覆盖全局故事概览，人物目标与冲突、关键事件及因果脉络，"
                "把写作技法按类型组织成一个写作 Skill，合并重复或过细的观察；Skill 内只写"
                "通用写作逻辑和风格特征，不带入人物、地点、具体情节或引文。原文证据如需"
                "展示必须与 Skill 描述分开。并说明证据覆盖与仍不确定之处。"
                "明确区分正文事实、角色认知和分析推断，不得把准备分析写成已经完成。"
            )),
            PublicFact(
                "storyOverview",
                _story_overview(artifact.get("storyOverview"), text_limit * 4),
            ),
            PublicFact(
                "factThreads",
                [_fact_projection(item, text_limit) for item in facts[:fact_limit]],
            ),
            PublicFact(
                "writingTechniques",
                [_card_projection(item, text_limit) for item in cards[:card_limit]],
            ),
            PublicFact("coverage", _coverage_projection(artifact.get("coverage"))),
            PublicFact(
                "uncertainties",
                [_clip(item, text_limit) for item in tuple(artifact.get("conflicts") or ())[:6]],
            ),
        )
        try:
            return PublicFactBundle(facts=projected)
        except ValueError as error:
            if "exceeds the size limit" not in str(error):
                raise
    raise ValueError("novel analysis public facts cannot fit the presentation limit")


def _story_overview(value: object, limit: int) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {
        "summaryMarkdown": _clip(value.get("summaryMarkdown"), limit),
        "evidence": _evidence_projection(value.get("evidence"), 3),
    }


def _fact_projection(value: object, limit: int) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        return {"statement": _clip(value, limit)}
    return {
        "factKind": _clip(value.get("factKind"), 64),
        "subject": _clip(value.get("subjectKey"), 120),
        "predicate": _clip(value.get("predicate"), 120),
        "value": _clip(value.get("value"), limit),
        "lifecycle": _clip(value.get("lifecycleStatus"), 64),
        "evidence": _evidence_projection(value.get("evidence"), 2),
    }


def _card_projection(value: object, limit: int) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        return {"description": _clip(value, limit)}
    return {
        "kind": _clip(value.get("cardKind"), 80),
        "title": _clip(value.get("title"), 120),
        "description": _clip(value.get("bodyMarkdown"), limit),
        "evidence": _evidence_projection(value.get("evidence"), 2),
    }


def _evidence_projection(value: object, limit: int) -> list[Mapping[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    projected = []
    for item in value[:limit]:
        if not isinstance(item, Mapping):
            continue
        projected.append({
            "sectionOrdinal": item.get("sectionOrdinal"),
            "sectionTitle": _clip(item.get("sectionTitle"), 120),
            "excerpt": _clip(item.get("excerpt"), 180),
        })
    return projected


def _coverage_projection(value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    missing = value.get("missingSectionIds")
    return {
        "ratio": value.get("ratio"),
        "totalSections": value.get("totalSections"),
        "evidencedSections": value.get("evidencedSections"),
        "missingSectionCount": len(missing) if isinstance(missing, Sequence) else 0,
    }


def _clip(value: object, limit: int) -> str:
    if value is None:
        return ""
    text = value if isinstance(value, str) else json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    text = str(text or "").strip()
    return text if len(text) <= limit else text[: max(0, limit - 1)] + "…"


__all__ = ["NovelAnalysisPublicFactsProvider"]
