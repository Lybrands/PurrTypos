"""Trace accepted adaptation decisions into screenplay structure units."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def normalize_structure_trace(
    *,
    kind: str,
    units: object,
    decision_coverage: object,
    creative_brief: Mapping[str, Any],
    require_adaptation_decisions: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if kind not in {"beat_sheet", "episode_outline"}:
        raise ValueError("不支持的剧本结构类型")
    normalized_units = _normalize_units(kind, units)
    decision_actions = _brief_decision_actions(creative_brief)
    if require_adaptation_decisions and not decision_actions:
        raise ValueError("书架改编项目必须先接受包含改编决策的新版创作简报")
    normalized_coverage = _normalize_coverage(
        decision_coverage,
        decision_actions=decision_actions,
        structure_unit_ids={
            str(item["id"]) for item in normalized_units
        },
    )
    if kind == "episode_outline":
        expected_count = _brief_episode_count(creative_brief)
        if expected_count is not None and len(normalized_units) != expected_count:
            raise ValueError("分集结构的集数必须与已接受创作简报的成片规模一致")
    return normalized_units, normalized_coverage


def _normalize_units(kind: str, value: object) -> list[dict[str, Any]]:
    if not _is_list(value) or not value:
        label = "节拍表" if kind == "beat_sheet" else "分集结构"
        raise ValueError(f"{label}必须包含至少一个结构单元")
    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(value, start=1):
        if not isinstance(item, Mapping):
            raise ValueError("每个结构单元都必须是结构化对象")
        unit_id = str(item.get("id") or "").strip()
        if not unit_id or unit_id in seen_ids:
            raise ValueError("每个结构单元必须具有唯一且非空的稳定 ID")
        seen_ids.add(unit_id)
        expected_key = "order" if kind == "beat_sheet" else "number"
        try:
            position = int(item.get(expected_key))
        except (TypeError, ValueError):
            raise ValueError("结构单元序号必须是连续正整数") from None
        if position != index:
            raise ValueError("结构单元序号必须从 1 开始连续递增")
        required_text = (
            ("label", "summary")
            if kind == "beat_sheet"
            else ("title", "summary")
        )
        if any(not str(item.get(key) or "").strip() for key in required_text):
            raise ValueError("每个结构单元都必须包含标题和内容摘要")
        normalized_item = dict(item)
        normalized_item["id"] = unit_id
        normalized_item[expected_key] = position
        for key in required_text:
            normalized_item[key] = str(item.get(key) or "").strip()
        normalized.append(normalized_item)
    return normalized


def _normalize_coverage(
    value: object,
    *,
    decision_actions: dict[str, str],
    structure_unit_ids: set[str],
) -> list[dict[str, Any]]:
    if not decision_actions:
        if value is None:
            return []
        if not _is_list(value):
            raise ValueError("decisionCoverage 必须是数组")
        if value:
            raise ValueError("原创简报没有可映射的原作改编决策")
        return []
    if not _is_list(value) or not value:
        raise ValueError("结构提案必须覆盖创作简报中的全部改编决策")
    normalized: list[dict[str, Any]] = []
    seen_decisions: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping):
            raise ValueError("每条决策覆盖记录都必须是结构化对象")
        decision_id = str(item.get("decisionId") or "").strip()
        implementation = str(item.get("implementation") or "").strip()
        if (
            not decision_id
            or decision_id in seen_decisions
            or decision_id not in decision_actions
            or not implementation
        ):
            raise ValueError("决策覆盖记录包含重复、未知或不完整的决策")
        seen_decisions.add(decision_id)
        unit_ids = _string_list(item.get("structureUnitIds"))
        if not set(unit_ids).issubset(structure_unit_ids):
            raise ValueError("决策覆盖记录引用了不存在的结构单元")
        if decision_actions[decision_id] == "omit":
            if unit_ids:
                raise ValueError("删减决策不能映射到实际结构单元")
        elif not unit_ids:
            raise ValueError("非删减决策必须落到至少一个结构单元")
        normalized.append({
            "decisionId": decision_id,
            "structureUnitIds": unit_ids,
            "implementation": implementation,
        })
    if seen_decisions != set(decision_actions):
        raise ValueError("结构提案必须完整覆盖创作简报中的全部改编决策")
    return normalized


def _brief_decision_actions(
    creative_brief: Mapping[str, Any],
) -> dict[str, str]:
    brief = creative_brief.get("brief")
    if not isinstance(brief, Mapping):
        return {}
    decisions = brief.get("adaptationDecisions")
    if not _is_list(decisions):
        return {}
    return {
        str(item.get("id") or "").strip(): str(
            item.get("action") or ""
        ).strip()
        for item in decisions
        if isinstance(item, Mapping)
        and str(item.get("id") or "").strip()
    }


def _brief_episode_count(
    creative_brief: Mapping[str, Any],
) -> int | None:
    brief = creative_brief.get("brief")
    if not isinstance(brief, Mapping):
        return None
    format_plan = brief.get("formatPlan")
    if not isinstance(format_plan, Mapping):
        return None
    try:
        count = int(format_plan.get("episodeCount"))
    except (TypeError, ValueError):
        return None
    return count if count > 0 else None


def _string_list(value: object) -> list[str]:
    if not _is_list(value):
        raise ValueError("structureUnitIds 必须是数组")
    return list(dict.fromkeys(
        str(item).strip() for item in value if str(item).strip()
    ))


def _is_list(value: object) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes))
