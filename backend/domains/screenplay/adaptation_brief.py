"""Validate the creative decisions that turn source analysis into adaptation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


ADAPTATION_ACTIONS = frozenset({
    "preserve",
    "compress",
    "merge",
    "omit",
    "reorder",
    "transform",
    "invent",
})
SERIES_FORMATS = frozenset({"连续剧", "竖屏短剧"})


def normalize_creative_brief(
    brief: Mapping[str, Any],
    *,
    project_format: str,
    source_analysis: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return a normalized brief and enforce adaptation-only requirements."""

    normalized = dict(brief)
    logline = str(brief.get("logline") or "").strip()
    core_conflict = str(brief.get("coreConflict") or "").strip()
    if not logline or not core_conflict:
        raise ValueError("创作简报必须包含一句话梗概和核心冲突")
    normalized["logline"] = logline
    normalized["coreConflict"] = core_conflict
    if source_analysis is None:
        return normalized

    normalized["formatPlan"] = _normalize_format_plan(
        brief.get("formatPlan"),
        project_format=project_format,
    )
    evidence = _analysis_evidence(source_analysis)
    normalized["adaptationDecisions"] = _normalize_decisions(
        brief.get("adaptationDecisions"),
        evidence=evidence,
    )
    acknowledged = _string_list(
        brief.get("acknowledgedSourceLimitations"),
    )
    source_limitations = _analysis_limitations(source_analysis)
    missing_limitations = set(source_limitations).difference(acknowledged)
    if missing_limitations:
        raise ValueError("改编方案必须承接原作范围分析中全部阅读局限")
    normalized["acknowledgedSourceLimitations"] = acknowledged
    return normalized


def _normalize_format_plan(
    value: object,
    *,
    project_format: str,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("改编方案必须包含结构化成片规模")
    target_format = str(value.get("targetFormat") or "").strip()
    if target_format != project_format:
        raise ValueError("成片规模中的剧本形态必须与项目设置一致")
    scope_strategy = str(value.get("scopeStrategy") or "").strip()
    narrative_endpoint = str(value.get("narrativeEndpoint") or "").strip()
    if not scope_strategy or not narrative_endpoint:
        raise ValueError("成片规模必须说明范围压缩策略和叙事终点")
    normalized = {
        "targetFormat": target_format,
        "scopeStrategy": scope_strategy,
        "narrativeEndpoint": narrative_endpoint,
    }
    if project_format in SERIES_FORMATS:
        normalized["episodeCount"] = _positive_int(
            value.get("episodeCount"),
            "连续剧或竖屏短剧必须确定集数",
            maximum=1000,
        )
        normalized["episodeDurationMinutes"] = _positive_int(
            value.get("episodeDurationMinutes"),
            "连续剧或竖屏短剧必须确定单集时长",
            maximum=300,
        )
    else:
        normalized["targetDurationMinutes"] = _positive_int(
            value.get("targetDurationMinutes"),
            "短片、电影或单集剧必须确定目标时长",
            maximum=600,
        )
    return normalized


def _normalize_decisions(
    value: object,
    *,
    evidence: set[tuple[str, str]],
) -> list[dict[str, Any]]:
    if not _is_list(value) or not value:
        raise ValueError("改编方案必须包含至少一条具体改编决策")
    if len(value) > 100:
        raise ValueError("改编方案最多包含 100 条改编决策")
    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping):
            raise ValueError("每条改编决策都必须是结构化对象")
        decision_id = str(item.get("id") or "").strip()
        action = str(item.get("action") or "").strip()
        subject = str(item.get("subject") or "").strip()
        rationale = str(item.get("rationale") or "").strip()
        screen_intent = str(item.get("screenIntent") or "").strip()
        if (
            not decision_id
            or decision_id in seen_ids
            or action not in ADAPTATION_ACTIONS
            or not subject
            or not rationale
            or not screen_intent
        ):
            raise ValueError("改编决策的 ID、动作、对象、理由和银幕意图必须完整且有效")
        seen_ids.add(decision_id)
        anchors = _normalize_anchors(item.get("sourceAnchors"))
        if action != "invent" and not anchors:
            raise ValueError("除新增内容外，每条改编决策都必须锚定原作分析证据")
        if not set(anchors).issubset(evidence):
            raise ValueError("改编决策引用了已接受原作范围分析中不存在的证据")
        normalized.append({
            "id": decision_id,
            "action": action,
            "subject": subject,
            "rationale": rationale,
            "screenIntent": screen_intent,
            "sourceAnchors": [
                {"sourceType": source_type, "sourceId": source_id}
                for source_type, source_id in anchors
            ],
        })
    return normalized


def _normalize_anchors(value: object) -> list[tuple[str, str]]:
    if not _is_list(value):
        raise ValueError("改编决策的 sourceAnchors 必须是数组")
    if len(value) > 30:
        raise ValueError("每条改编决策最多引用 30 个原作证据锚点")
    result: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in value:
        if not isinstance(item, Mapping):
            raise ValueError("每个原作证据锚点都必须是结构化对象")
        key = (
            str(item.get("sourceType") or "").strip(),
            str(item.get("sourceId") or "").strip(),
        )
        if not all(key):
            raise ValueError("每个原作证据锚点都必须包含类型和 ID")
        if key not in seen:
            seen.add(key)
            result.append(key)
    return result


def _analysis_evidence(
    source_analysis: Mapping[str, Any],
) -> set[tuple[str, str]]:
    analysis = source_analysis.get("analysis")
    if not isinstance(analysis, Mapping):
        return set()
    evidence = analysis.get("evidence")
    if not _is_list(evidence):
        return set()
    return {
        (
            str(item.get("sourceType") or "").strip(),
            str(item.get("sourceId") or "").strip(),
        )
        for item in evidence
        if isinstance(item, Mapping)
        and str(item.get("sourceType") or "").strip()
        and str(item.get("sourceId") or "").strip()
    }


def _analysis_limitations(
    source_analysis: Mapping[str, Any],
) -> list[str]:
    analysis = source_analysis.get("analysis")
    if not isinstance(analysis, Mapping):
        return []
    coverage = analysis.get("coverage")
    if not isinstance(coverage, Mapping):
        return []
    return _string_list(coverage.get("limitations"))


def _string_list(value: object) -> list[str]:
    if not _is_list(value):
        return []
    return list(dict.fromkeys(
        str(item).strip() for item in value if str(item).strip()
    ))


def _is_list(value: object) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes))


def _positive_int(value: object, message: str, *, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ValueError(message) from None
    if parsed <= 0 or parsed > maximum:
        raise ValueError(message)
    return parsed
