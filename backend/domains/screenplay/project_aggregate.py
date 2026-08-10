"""Pure business rules for the screenplay v2 project aggregate."""

from __future__ import annotations

from collections.abc import Iterable, Mapping


DELIVERABLE_ROLES = (
    "sourceAnalysis",
    "creativeBrief",
    "structure",
    "sceneList",
    "screenplayDraft",
    "review",
)

V2_TO_LEGACY_FORMAT = {
    "shortFilm": "短片",
    "featureFilm": "电影",
    "singleEpisode": "单集剧",
    "series": "连续剧",
    "verticalSeries": "竖屏短剧",
}
LEGACY_TO_V2_FORMAT = {
    legacy: public for public, legacy in V2_TO_LEGACY_FORMAT.items()
}

STAGE_TARGET_ROLE = {
    "orientation": "sourceAnalysis",
    "brief": "creativeBrief",
    "structure": "structure",
    "scenes": "sceneList",
    "draft": "screenplayDraft",
    "review": "review",
}


def applicable_deliverable_roles(source_kind: str) -> tuple[str, ...]:
    if source_kind == "book":
        return DELIVERABLE_ROLES
    return tuple(role for role in DELIVERABLE_ROLES if role != "sourceAnalysis")


def public_format(value: object) -> str:
    normalized = str(value or "").strip()
    if normalized in V2_TO_LEGACY_FORMAT:
        return normalized
    return LEGACY_TO_V2_FORMAT.get(normalized, "singleEpisode")


def legacy_format(value: object) -> str:
    normalized = str(value or "").strip()
    if normalized in LEGACY_TO_V2_FORMAT:
        return normalized
    return V2_TO_LEGACY_FORMAT.get(normalized, "单集剧")


def derive_stage(
    *,
    source_kind: str,
    head_roles: Iterable[str],
    head_contents: Mapping[str, Mapping[str, object]] | None = None,
    has_current_finalization: bool = False,
    legacy_completed: bool = False,
) -> str:
    heads = {str(role) for role in head_roles}
    if source_kind == "book" and "sourceAnalysis" not in heads:
        return "orientation"
    if "creativeBrief" not in heads:
        return "brief"
    if "structure" not in heads:
        return "structure"
    if "sceneList" not in heads:
        return "scenes"
    if "screenplayDraft" not in heads:
        return "draft"
    if head_contents is not None:
        draft = head_contents.get("screenplayDraft", {})
        if draft and draft.get("isComplete") is not True:
            return "draft"
    if "review" not in heads:
        return "review"
    if has_current_finalization or legacy_completed:
        return "completed"
    return "review"


def next_actions(
    stage: str,
    *,
    head_contents: Mapping[str, Mapping[str, object]] | None = None,
    review_state: Mapping[str, object] | None = None,
) -> list[dict[str, str]]:
    role = STAGE_TARGET_ROLE.get(str(stage))
    if str(stage) == "review":
        if review_state is not None:
            action = review_state.get("nextAction")
            return [dict(action)] if isinstance(action, Mapping) else []
        if head_contents is not None and head_contents.get("review"):
            return []
    if role is None:
        return []
    return [{
        "type": "generateDeliverable",
        "targetRole": role,
    }]


__all__ = [
    "DELIVERABLE_ROLES",
    "applicable_deliverable_roles",
    "derive_stage",
    "legacy_format",
    "next_actions",
    "public_format",
]
