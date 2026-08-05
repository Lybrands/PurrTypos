"""Deterministic user-facing Markdown for structured screenplay proposals."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any


def render_screenplay_proposal(
    *,
    kind: str,
    title: str,
    content: Mapping[str, Any],
) -> str:
    """Render structured domain truth without asking the model to duplicate it."""

    normalized_kind = str(kind or "").strip()
    heading = str(title or "").strip() or "剧本提案"
    if normalized_kind == "source_analysis":
        return _render_source_analysis(heading, _mapping(content.get("analysis")))
    if normalized_kind == "creative_brief":
        return _render_creative_brief(heading, _mapping(content.get("brief")))
    if normalized_kind == "beat_sheet":
        return _render_structure(
            heading,
            "节拍",
            _items(content.get("beats")),
            number_key="order",
            title_key="label",
        )
    if normalized_kind == "episode_outline":
        return _render_structure(
            heading,
            "分集",
            _items(content.get("episodes")),
            number_key="number",
            title_key="title",
        )
    if normalized_kind == "scene_list":
        return _render_scenes(heading, _items(content.get("scenes")))
    if normalized_kind == "review":
        return _render_review(heading, content)
    return "\n\n".join((
        f"# {heading}",
        "```json\n"
        + json.dumps(content, ensure_ascii=False, indent=2, default=str)
        + "\n```",
    ))


def _render_source_analysis(title: str, analysis: Mapping[str, Any]) -> str:
    sections = [f"# {title}"]
    _section(sections, "范围概述", analysis.get("rangeSummary"))
    _section(sections, "叙事概述", analysis.get("narrativeSummary"))
    coverage = _mapping(analysis.get("coverage"))
    if coverage:
        rows = [
            f"- 选定章节：{_text(coverage.get('selectedChapterCount')) or '0'}",
            f"- 完整读取：{len(_sequence(coverage.get('readChapterIds')))}",
            f"- 抽样读取：{len(_sequence(coverage.get('sampledChapterIds')))}",
        ]
        limitations = _texts(coverage.get("limitations"))
        if limitations:
            rows.extend(f"- 局限：{item}" for item in limitations)
        sections.extend(("## 覆盖情况", "\n".join(rows)))
    characters = _items(analysis.get("characters"))
    if characters:
        rows = []
        for item in characters:
            name = _text(item.get("name")) or "未命名人物"
            details = "；".join(filter(None, (
                _labeled("定位", item.get("role")),
                _labeled("目标", item.get("goal")),
                _labeled("冲突", item.get("conflict")),
            )))
            rows.append(f"- **{name}**：{details}" if details else f"- **{name}**")
        sections.extend(("## 主要人物", "\n".join(rows)))
    events = _items(analysis.get("plotEvents"))
    if events:
        rows = []
        for index, item in enumerate(events, start=1):
            order = _text(item.get("order")) or str(index)
            event = _text(item.get("event")) or "未命名事件"
            consequence = _text(item.get("consequence"))
            rows.append(
                f"{order}. **{event}**"
                + (f"\n   - 结果：{consequence}" if consequence else "")
            )
        sections.extend(("## 关键事件", "\n".join(rows)))
    for key, label in (
        ("centralConflicts", "核心冲突"),
        ("adaptationAssets", "改编资产"),
        ("continuityRisks", "连续性风险"),
        ("openQuestions", "待确认问题"),
    ):
        _bullet_section(sections, label, analysis.get(key))
    return "\n\n".join(sections)


def _render_creative_brief(title: str, brief: Mapping[str, Any]) -> str:
    sections = [f"# {title}"]
    for key, label in (
        ("audience", "目标受众"),
        ("logline", "一句话梗概"),
        ("theme", "主题"),
        ("protagonist", "主人公"),
        ("coreConflict", "核心冲突"),
    ):
        _section(sections, label, brief.get(key))
    format_plan = _mapping(brief.get("formatPlan"))
    if format_plan:
        rows = []
        for key, label in (
            ("targetFormat", "目标形式"),
            ("targetDurationMinutes", "目标时长（分钟）"),
            ("episodeCount", "集数"),
            ("episodeDurationMinutes", "单集时长（分钟）"),
            ("scopeStrategy", "范围策略"),
            ("narrativeEndpoint", "叙事终点"),
        ):
            value = _text(format_plan.get(key))
            if value:
                rows.append(f"- {label}：{value}")
        if rows:
            sections.extend(("## 成片规划", "\n".join(rows)))
    decisions = _items(brief.get("adaptationDecisions"))
    if decisions:
        rows = []
        for index, item in enumerate(decisions, start=1):
            subject = _text(item.get("subject")) or f"决策 {index}"
            action = _text(item.get("action"))
            rationale = _text(item.get("rationale"))
            intent = _text(item.get("screenIntent"))
            rows.append(
                f"{index}. **{subject}**"
                + (f"（{action}）" if action else "")
                + (f"\n   - 理由：{rationale}" if rationale else "")
                + (f"\n   - 银幕意图：{intent}" if intent else "")
            )
        sections.extend(("## 改编决策", "\n".join(rows)))
    _bullet_section(sections, "改编原则", brief.get("adaptationPrinciples"))
    _bullet_section(
        sections,
        "已确认的原作局限",
        brief.get("acknowledgedSourceLimitations"),
    )
    _bullet_section(sections, "待确认问题", brief.get("openQuestions"))
    return "\n\n".join(sections)


def _render_structure(
    title: str,
    unit_label: str,
    units: Sequence[Mapping[str, Any]],
    *,
    number_key: str,
    title_key: str,
) -> str:
    sections = [f"# {title}"]
    rows = []
    for index, item in enumerate(units, start=1):
        number = _text(item.get(number_key)) or str(index)
        label = _text(item.get(title_key)) or f"{unit_label} {number}"
        summary = _text(item.get("summary"))
        rows.append(
            f"## {number}. {label}"
            + (f"\n\n{summary}" if summary else "")
        )
    sections.extend(rows or [f"尚无{unit_label}内容。"])
    return "\n\n".join(sections)


def _render_scenes(title: str, scenes: Sequence[Mapping[str, Any]]) -> str:
    sections = [f"# {title}"]
    for index, scene in enumerate(scenes, start=1):
        order = _text(scene.get("order")) or str(index)
        heading = _text(scene.get("heading")) or f"场景 {order}"
        rows = []
        for key, label in (
            ("episodeNumber", "所属集"),
            ("location", "地点"),
            ("timeOfDay", "时间"),
            ("objective", "目标"),
            ("conflict", "冲突"),
            ("turn", "转折"),
            ("synopsis", "场景梗概"),
        ):
            value = _text(scene.get(key))
            if value:
                rows.append(f"- {label}：{value}")
        characters = _texts(scene.get("characters"))
        if characters:
            rows.insert(0, f"- 人物：{'、'.join(characters)}")
        details = "\n".join(rows)
        sections.append(
            f"## {order}. {heading}"
            + (f"\n\n{details}" if details else "")
        )
    return "\n\n".join(sections)


def _render_review(
    title: str,
    content: Mapping[str, Any],
) -> str:
    sections = [f"# {title}"]
    _section(sections, "审阅结论", content.get("summary"))
    verdict = _text(content.get("verdict"))
    if verdict:
        sections.extend(("## 结论状态", verdict))
    _bullet_section(sections, "优点", content.get("strengths"))
    issues = _items(content.get("issues"))
    if issues:
        rows = []
        for index, issue in enumerate(issues, start=1):
            problem = _text(issue.get("problem")) or f"问题 {index}"
            severity = _text(issue.get("severity"))
            recommendation = _text(issue.get("recommendation"))
            criteria = _text(issue.get("acceptanceCriteria"))
            rows.append(
                f"### {index}. {problem}"
                + (f"（{severity}）" if severity else "")
                + (
                    f"\n\n- 涉及场景：{'、'.join(_texts(issue.get('sceneIds')))}"
                    if _texts(issue.get("sceneIds"))
                    else ""
                )
                + (f"\n- 建议：{recommendation}" if recommendation else "")
                + (f"\n- 验收标准：{criteria}" if criteria else "")
            )
        sections.extend(("## 待处理问题", "\n\n".join(rows)))
    return "\n\n".join(sections)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> Sequence[Any]:
    if isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        return value
    return ()


def _items(value: Any) -> list[Mapping[str, Any]]:
    return [item for item in _sequence(value) if isinstance(item, Mapping)]


def _text(value: Any) -> str:
    if value is None or isinstance(value, (Mapping, list, tuple, set)):
        return ""
    return str(value).strip()


def _texts(value: Any) -> list[str]:
    return [text for item in _sequence(value) if (text := _text(item))]


def _section(rows: list[str], heading: str, value: Any) -> None:
    text = _text(value)
    if text:
        rows.extend((f"## {heading}", text))


def _bullet_section(rows: list[str], heading: str, value: Any) -> None:
    items = _texts(value)
    if items:
        rows.extend((f"## {heading}", "\n".join(f"- {item}" for item in items)))


def _labeled(label: str, value: Any) -> str:
    text = _text(value)
    return f"{label}：{text}" if text else ""


__all__ = ["render_screenplay_proposal"]
