"""Stream Root-Agent narration from validated Novel Analysis artifacts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from agents.novel_analysis.attempt_artifact import NovelAnalysisAttemptArtifactStore


class NovelAnalysisStageOutput:
    """Ask the owning Root Agent to narrate each completed semantic milestone."""

    def __init__(self, db, *, reporter, runtime) -> None:
        self._db = db
        self._reporter = reporter
        self._runtime = runtime
        self._artifacts = NovelAnalysisAttemptArtifactStore(db)

    async def publish(self, context, result, signal=None) -> None:
        kind = str(context.unit.metadata.get("unitKind") or "")
        if kind in {"map", "reduce"} and not await self._is_pass_root(context):
            return
        if kind not in {"map", "reduce", "synthesize", "skill", "review"}:
            return
        artifact_id = str(result.metadata.get("artifactId") or "").strip()
        if not artifact_id:
            return
        payload = await self._artifacts.load_payload(artifact_id)
        facts = build_stage_output_facts(kind, payload, context.unit.metadata)
        if not facts:
            return
        await self._reporter(
            context=context,
            facts=facts,
            runtime=self._runtime,
            signal=signal,
        )

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


def build_stage_output_facts(
    kind: str,
    payload: Mapping[str, object],
    metadata: Mapping[str, object],
) -> dict[str, object]:
    """Expose bounded semantic facts to the Root model without authored prose."""

    title = str(metadata.get("displayTitle") or kind).strip()
    base: dict[str, object] = {"stageTitle": title, "stageKind": kind}
    if kind in {"map", "reduce"}:
        findings = _selected_records(
            payload.get("findings"),
            ("dimension", "subject", "analysis"),
        )
        conflicts = _selected_records(
            payload.get("conflicts"),
            ("dimension", "subject", "description"),
        )
        if not findings and not conflicts:
            return {}
        return {
            **base,
            "dimensions": _strings(metadata.get("dimensions")),
            "findings": findings,
            "conflicts": conflicts,
        }
    if kind in {"synthesize", "review"}:
        return {
            **base,
            "summaryMarkdown": str(payload.get("summaryMarkdown") or "").strip(),
            "facts": _selected_records(
                payload.get("facts"),
                ("factKind", "subjectKey", "predicate", "value"),
            ),
            "craftCards": _selected_records(
                payload.get("craftCards"),
                ("title", "bodyMarkdown"),
            ),
        }
    technique = payload.get("techniqueResult")
    if not isinstance(technique, Mapping):
        return {}
    return {
        **base,
        "techniqueResult": {
            key: technique[key]
            for key in ("status", "evidenceRefs", "scopeNotes", "reason")
            if key in technique
        },
    }


def _selected_records(
    value: object,
    keys: tuple[str, ...],
    *,
    maximum: int = 8,
) -> list[dict[str, object]]:
    if not _sequence(value):
        return []
    result = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        selected = {key: item[key] for key in keys if key in item}
        if selected:
            result.append(selected)
        if len(result) >= maximum:
            break
    return result


def _strings(value: object) -> list[str]:
    if not _sequence(value):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _sequence(value: object) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes))


__all__ = ["NovelAnalysisStageOutput", "build_stage_output_facts"]
