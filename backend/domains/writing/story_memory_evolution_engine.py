"""Deterministic and conservative Story Memory evolution classification."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from domains.writing.story_memory import (
    StoryMemoryChange,
    StoryMemoryDelta,
    StoryMemoryKind,
    StoryMemoryOperation,
    StoryMemoryRecord,
    StoryMemoryStatus,
)
from domains.writing.story_memory_evolution import (
    EvolutionClassification,
    EvolutionFieldChange,
    EvolutionRecommendation,
    EvolutionRisk,
    StoryMemoryEvolutionDecision,
    StoryMemoryEvolutionReview,
)


def evaluate_story_memory_delta(
    delta: StoryMemoryDelta,
    current_records: Sequence[StoryMemoryRecord],
    *,
    source_narrative_orders: Mapping[str, int | None] | None = None,
) -> StoryMemoryEvolutionReview:
    records = tuple(current_records)
    by_key = {item.memory_key: item for item in records}
    source_orders = source_narrative_orders or {}
    decisions = tuple(
        _evaluate_change(
            delta.id,
            change,
            existing=by_key.get(change.target_key),
            all_records=records,
            source_narrative_orders=source_orders,
        )
        for change in delta.changes
    )
    return StoryMemoryEvolutionReview(
        delta_id=delta.id,
        book_id=delta.book_id,
        chapter_id=delta.chapter_id,
        decisions=decisions,
    )


def _evaluate_change(
    delta_id: str,
    change: StoryMemoryChange,
    *,
    existing: StoryMemoryRecord | None,
    all_records: Sequence[StoryMemoryRecord],
    source_narrative_orders: Mapping[str, int | None],
) -> StoryMemoryEvolutionDecision:
    if change.operation is StoryMemoryOperation.REMOVE:
        if existing is None:
            return _decision(
                delta_id,
                change,
                EvolutionClassification.DUPLICATE,
                EvolutionRecommendation.REJECT,
                EvolutionRisk.LOW,
                "目标设定已经不存在，无需重复移除。",
            )
        return _decision(
            delta_id,
            change,
            EvolutionClassification.SUPERSESSION,
            EvolutionRecommendation.REVIEW,
            EvolutionRisk.HIGH,
            "候选会使现有正式设定退出当前状态，需要人工确认其替代依据。",
            related=existing,
            field_changes=(
                EvolutionFieldChange("lifecycle", "active", "reverted"),
            ),
        )

    if existing is not None:
        return _evaluate_same_key(
            delta_id,
            change,
            existing,
            source_narrative_orders,
        )

    related, relation = _find_cross_key_relation(change, all_records)
    if related is not None and relation is EvolutionClassification.DUPLICATE:
        return _decision(
            delta_id,
            change,
            relation,
            EvolutionRecommendation.REJECT,
            EvolutionRisk.LOW,
            "候选与另一条现有设定表达相同事实，建议复用已有稳定标识。",
            related=related,
        )
    if related is not None and relation is EvolutionClassification.SUPERSESSION:
        return _decision(
            delta_id,
            change,
            relation,
            EvolutionRecommendation.REVIEW,
            EvolutionRisk.HIGH,
            "候选看起来是既有对象的新版本，但使用了不同标识，需要确认替代关系。",
            related=related,
            field_changes=_payload_diff(related.payload, change.payload),
        )
    if related is not None and relation is EvolutionClassification.CONFLICT:
        return _decision(
            delta_id,
            change,
            relation,
            EvolutionRecommendation.REVIEW,
            EvolutionRisk.HIGH,
            "候选与同一主题的现有世界事实不一致，不能作为独立新增直接通过。",
            related=related,
            field_changes=_payload_diff(related.payload, change.payload),
        )
    return _decision(
        delta_id,
        change,
        EvolutionClassification.ADDITION,
        EvolutionRecommendation.APPLY,
        EvolutionRisk.LOW,
        "未发现相同稳定标识或明显语义重叠，可作为新的故事状态候选。",
    )


def _evaluate_same_key(
    delta_id: str,
    change: StoryMemoryChange,
    existing: StoryMemoryRecord,
    source_narrative_orders: Mapping[str, int | None],
) -> StoryMemoryEvolutionDecision:
    differences = _payload_diff(existing.payload, change.payload)
    status_downgrade = _status_rank(change.status) < _status_rank(existing.status)
    if not differences and (
        existing.status is change.status or status_downgrade
    ):
        return _decision(
            delta_id,
            change,
            EvolutionClassification.DUPLICATE,
            EvolutionRecommendation.REJECT,
            EvolutionRisk.LOW,
            (
                "候选内容与当前正式设定一致，且证据状态不会提升可信度。"
                if status_downgrade
                else "候选内容与当前正式设定完全一致。"
            ),
            related=existing,
        )
    if existing.status is not change.status:
        differences = (
            *differences,
            EvolutionFieldChange(
                "status",
                existing.status.value,
                change.status.value,
            ),
        )

    current_order = source_narrative_orders.get(existing.last_source_id or "")
    candidate_order = change.source.narrative_order
    if (
        current_order is not None
        and candidate_order is not None
        and candidate_order < current_order
    ):
        return _decision(
            delta_id,
            change,
            EvolutionClassification.CONFLICT,
            EvolutionRecommendation.REVIEW,
            EvolutionRisk.HIGH,
            "候选来自更早的叙事顺序，直接覆盖会让角色或剧情状态倒退。",
            related=existing,
            field_changes=differences,
        )

    if change.kind is StoryMemoryKind.WORLD_FACT:
        return _decision(
            delta_id,
            change,
            EvolutionClassification.CONFLICT,
            EvolutionRecommendation.REVIEW,
            EvolutionRisk.HIGH,
            "同一世界事实的陈述发生变化，可能是修订、视角差异或设定冲突。",
            related=existing,
            field_changes=differences,
        )
    if change.kind is StoryMemoryKind.TIMELINE_EVENT:
        return _decision(
            delta_id,
            change,
            EvolutionClassification.CONFLICT,
            EvolutionRecommendation.REVIEW,
            EvolutionRisk.HIGH,
            "同一事件的时间或内容发生变化，需要确认是补充还是改写历史。",
            related=existing,
            field_changes=differences,
        )
    if change.kind is StoryMemoryKind.PLOT_THREAD:
        previous = str(existing.payload.get("state") or "open")
        following = str(change.payload.get("state") or "open")
        if not _valid_plot_transition(previous, following):
            return _decision(
                delta_id,
                change,
                EvolutionClassification.CONFLICT,
                EvolutionRecommendation.REVIEW,
                EvolutionRisk.HIGH,
                f"剧情线状态不能安全地从 {previous} 回退到 {following}。",
                related=existing,
                field_changes=differences,
            )
    if status_downgrade:
        return _decision(
            delta_id,
            change,
            EvolutionClassification.UPDATE,
            EvolutionRecommendation.REVIEW,
            EvolutionRisk.HIGH,
            "候选表示新的状态变化，但其证据可信度低于当前正式设定，不能直接覆盖。",
            related=existing,
            field_changes=differences,
        )
    return _decision(
        delta_id,
        change,
        EvolutionClassification.UPDATE,
        EvolutionRecommendation.APPLY,
        EvolutionRisk.MEDIUM,
        "候选沿用现有稳定标识，且变化符合可推进的故事状态更新。",
        related=existing,
        field_changes=differences,
    )


def _find_cross_key_relation(
    change: StoryMemoryChange,
    records: Sequence[StoryMemoryRecord],
) -> tuple[StoryMemoryRecord | None, EvolutionClassification | None]:
    same_kind = [item for item in records if item.kind is change.kind]
    for record in same_kind:
        if _json_equal(record.payload, change.payload):
            return record, EvolutionClassification.DUPLICATE

    if change.kind is StoryMemoryKind.WORLD_FACT:
        statement = _normalized_text(change.payload.get("statement"))
        subject = _normalized_text(change.payload.get("subjectId"))
        tags = _normalized_set(change.payload.get("tags"))
        for record in same_kind:
            other_statement = _normalized_text(record.payload.get("statement"))
            if statement and statement == other_statement:
                return record, EvolutionClassification.DUPLICATE
            other_subject = _normalized_text(record.payload.get("subjectId"))
            other_tags = _normalized_set(record.payload.get("tags"))
            if subject and subject == other_subject and tags.intersection(other_tags):
                return record, EvolutionClassification.CONFLICT

    if change.kind is StoryMemoryKind.PLOT_THREAD:
        title = _normalized_text(change.payload.get("title"))
        for record in same_kind:
            if title and title == _normalized_text(record.payload.get("title")):
                return record, EvolutionClassification.SUPERSESSION

    if change.kind is StoryMemoryKind.TIMELINE_EVENT:
        title = _normalized_text(change.payload.get("title"))
        story_time = _normalized_text(change.payload.get("storyTime"))
        for record in same_kind:
            if not title or title != _normalized_text(record.payload.get("title")):
                continue
            other_time = _normalized_text(record.payload.get("storyTime"))
            if story_time == other_time:
                return record, EvolutionClassification.SUPERSESSION
    return None, None


def _payload_diff(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
) -> tuple[EvolutionFieldChange, ...]:
    changes = []
    for key in sorted(set(before) | set(after)):
        before_value = before.get(key)
        after_value = after.get(key)
        if not _json_equal(before_value, after_value):
            changes.append(EvolutionFieldChange(key, before_value, after_value))
    return tuple(changes)


def _decision(
    delta_id: str,
    change: StoryMemoryChange,
    classification: EvolutionClassification,
    recommendation: EvolutionRecommendation,
    risk: EvolutionRisk,
    rationale: str,
    *,
    related: StoryMemoryRecord | None = None,
    field_changes: tuple[EvolutionFieldChange, ...] = (),
) -> StoryMemoryEvolutionDecision:
    return StoryMemoryEvolutionDecision(
        delta_id=delta_id,
        target_key=change.target_key,
        kind=change.kind,
        classification=classification,
        recommendation=recommendation,
        risk=risk,
        rationale=rationale,
        field_changes=field_changes,
        candidate_payload=dict(change.payload),
        source_excerpt=change.source.excerpt,
        related_memory_key=related.memory_key if related else None,
        existing_record_id=related.id if related else None,
        existing_version=related.version if related else None,
        candidate_confidence=float(change.confidence),
    )


def _valid_plot_transition(previous: str, following: str) -> bool:
    allowed = {
        "open": {"open", "advancing", "resolved", "abandoned"},
        "advancing": {"advancing", "resolved", "abandoned"},
        "resolved": {"resolved"},
        "abandoned": {"abandoned"},
    }
    return following in allowed.get(previous, {previous})


def _status_rank(status: StoryMemoryStatus) -> int:
    return {
        StoryMemoryStatus.DEPRECATED: 0,
        StoryMemoryStatus.DISPUTED: 1,
        StoryMemoryStatus.INFERRED: 2,
        StoryMemoryStatus.CONFIRMED: 3,
    }[status]


def _normalized_text(value: Any) -> str:
    text = str(value or "").strip().casefold()
    return re.sub(r"[\W_]+", "", text, flags=re.UNICODE)


def _normalized_set(value: Any) -> set[str]:
    if not isinstance(value, (list, tuple, set)):
        return set()
    return {_normalized_text(item) for item in value if _normalized_text(item)}


def _json_equal(left: Any, right: Any) -> bool:
    return json.dumps(left, ensure_ascii=False, sort_keys=True) == json.dumps(
        right,
        ensure_ascii=False,
        sort_keys=True,
    )


__all__ = ["evaluate_story_memory_delta"]
