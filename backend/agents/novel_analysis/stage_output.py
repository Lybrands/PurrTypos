"""Durable public narration derived from validated analysis artifacts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone

from agents.novel_analysis.attempt_artifact import NovelAnalysisAttemptArtifactStore
from purra.output import (
    AgentOutputEventDraft,
    OutputChannel,
    OutputEventKind,
    OutputSource,
    OutputVisibility,
)


STAGE_OUTPUT_EVENT_TYPE = "novel_analysis.stage_output"


class NovelAnalysisStageOutput:
    """Publish one bounded, factual paragraph for each semantic milestone."""

    def __init__(self, db, *, output_repository, publisher) -> None:
        self._db = db
        self._output = output_repository
        self._publisher = publisher
        self._artifacts = NovelAnalysisAttemptArtifactStore(db)

    async def publish(self, context, result) -> None:
        kind = str(context.unit.metadata.get("unitKind") or "")
        if kind in {"map", "reduce"} and not await self._is_pass_root(context):
            return
        if kind not in {"map", "reduce", "synthesize", "skill", "review"}:
            return
        artifact_id = str(result.metadata.get("artifactId") or "").strip()
        if not artifact_id:
            return
        payload = await self._artifacts.load_payload(artifact_id)
        text = build_stage_output_text(kind, payload, context.unit.metadata)
        if not text:
            return
        turn = await self._db.fetch_one(
            "SELECT turn_id FROM ai_agent_run_events WHERE run_id = ? "
            "AND source_event_key = ? AND kind = 'run.lifecycle' "
            "AND event_id IS NOT NULL ORDER BY sequence LIMIT 1",
            [context.run_id, f"run:{context.run_id}:running"],
        )
        stage_id = f"{context.task.id}:{context.unit.id}"
        event = await self._output.append_event(AgentOutputEventDraft(
            source_event_key=(
                f"novel-analysis-stage:{context.run_id}:{stage_id}"
            ),
            run_id=context.run_id,
            turn_id=str((turn or {}).get("turn_id") or "") or None,
            output_stream_id=None,
            invocation_id=None,
            source=OutputSource.RUNTIME,
            kind=OutputEventKind.RUNTIME,
            channel=OutputChannel.COMMENTARY,
            visibility=OutputVisibility.PUBLIC,
            occurred_at=datetime.now(timezone.utc),
            payload={
                "eventType": STAGE_OUTPUT_EVENT_TYPE,
                "data": {
                    "stageId": stage_id,
                    "unitId": context.unit.id,
                    "text": text,
                },
            },
        ))
        await self._publisher.publish_committed(event)

    async def _is_pass_root(self, context) -> bool:
        row = await self._db.fetch_one(
            "SELECT 1 AS present FROM ai_agent_long_task_units target, "
            "json_each(target.dependencies_json) dependency "
            "WHERE target.task_id = ? "
            "AND target.unit_id = 'synthesize:whole-work' "
            "AND dependency.value = ? LIMIT 1",
            [context.task.id, context.unit.id],
        )
        return row is not None


def build_stage_output_text(
    kind: str,
    payload: Mapping[str, object],
    metadata: Mapping[str, object],
) -> str:
    if kind in {"map", "reduce"}:
        dimensions = _strings(metadata.get("dimensions"))
        heading = _dimension_heading(dimensions)
        insights = _finding_insights(payload.get("findings"))
        if not insights:
            return ""
        return _bounded(f"{heading}已经形成较稳定的判断。{'；'.join(insights)}。")
    if kind == "synthesize":
        insights = _material_insights(payload)
        if not insights:
            return "整部作品的综合分析已经形成，正在核对资料之间的一致性与完整性。"
        return _bounded(
            f"整部作品的综合分析已经形成。{'；'.join(insights)}。接下来核对这些结论是否完整且相互一致。"
        )
    if kind == "skill":
        return "已创建完整写作 Skill，可在分析结果中按目录查看和编辑。"
    if kind == "review":
        insights = _material_insights(payload)
        if not insights:
            return "审核已经完成，整书分析资料通过一致性检查，可以进入人工确认。"
        return _bounded(
            f"审核已经完成，核心结论与整书资料保持一致。{'；'.join(insights)}。分析结果可以进入人工确认。"
        )
    return ""


def _dimension_heading(dimensions: tuple[str, ...]) -> str:
    values = set(dimensions)
    if values & {"language", "pacing", "foreshadowing", "style", "craft", "techniques"}:
        return "写作技法分析"
    if values & {"characters", "relationships", "setting", "worldbuilding", "plot"}:
        return "人物、设定与情节分析"
    return "当前分析阶段"


def _finding_insights(value: object) -> list[str]:
    if not _sequence(value):
        return []
    insights = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        subject = str(item.get("subject") or "").strip()
        analysis = _clean(str(item.get("analysis") or ""), 118)
        if analysis:
            insights.append(f"{subject}：{analysis}" if subject else analysis)
        if len(insights) == 2:
            break
    return insights


def _material_insights(payload: Mapping[str, object]) -> list[str]:
    insights = []
    facts = payload.get("facts")
    if _sequence(facts):
        for item in facts:
            if not isinstance(item, Mapping):
                continue
            subject = str(item.get("subjectKey") or "").strip()
            value = _clean(str(item.get("value") or ""), 105)
            if value:
                insights.append(f"{subject}：{value}" if subject else value)
            if len(insights) == 2:
                break
    if len(insights) < 2:
        cards = payload.get("craftCards")
        if _sequence(cards):
            for item in cards:
                if not isinstance(item, Mapping):
                    continue
                title = str(item.get("title") or "").strip()
                body = _clean(str(item.get("bodyMarkdown") or ""), 105)
                if title and body:
                    insights.append(f"{title}：{body}")
                    break
    return insights[:2]


def _strings(value: object) -> tuple[str, ...]:
    if not _sequence(value):
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())


def _sequence(value: object) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes))


def _clean(value: str, limit: int) -> str:
    text = " ".join(value.replace("\n", " ").split()).strip("；。 ")
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _bounded(value: str) -> str:
    # The shared public-narration boundary rejects paragraphs over 280 chars.
    return _clean(value, 270)


__all__ = [
    "NovelAnalysisStageOutput",
    "STAGE_OUTPUT_EVENT_TYPE",
    "build_stage_output_text",
]
