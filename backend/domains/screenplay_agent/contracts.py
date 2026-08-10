"""Closed semantic contracts produced by the screenplay intent Planner."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping


class ScreenplayIntentAction(StrEnum):
    ANSWER = "answer"
    CREATE = "create"
    REVISE = "revise"
    REVIEW = "review"


class ScreenplayScopeKind(StrEnum):
    CURRENT_STAGE = "current_stage"
    NEXT_EPISODES = "next_episodes"
    EPISODES = "episodes"
    ALL_REMAINING = "all_remaining"


SCREENPLAY_DELIVERABLE_ROLES = frozenset({
    "sourceAnalysis",
    "creativeBrief",
    "structure",
    "sceneList",
    "screenplayDraft",
    "review",
})


def _text(value: object) -> str:
    return str(value or "").strip()


def _text_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    result = tuple(dict.fromkeys(
        text for item in value if (text := _text(item))
    ))
    return result


@dataclass(frozen=True, slots=True)
class ScreenplayIntentScope:
    kind: ScreenplayScopeKind = ScreenplayScopeKind.CURRENT_STAGE
    count: int | None = None
    episode_numbers: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", ScreenplayScopeKind(self.kind))
        numbers = tuple(int(value) for value in self.episode_numbers)
        if any(value <= 0 for value in numbers) or len(numbers) != len(set(numbers)):
            raise ValueError("episode numbers must be unique positive integers")
        object.__setattr__(self, "episode_numbers", numbers)
        if self.count is not None:
            count = int(self.count)
            if not 1 <= count <= 100:
                raise ValueError("episode count must be between 1 and 100")
            object.__setattr__(self, "count", count)
        if self.kind is ScreenplayScopeKind.NEXT_EPISODES and self.count is None:
            raise ValueError("next_episodes scope requires count")
        if self.kind is ScreenplayScopeKind.EPISODES and not numbers:
            raise ValueError("episodes scope requires episode numbers")

    @classmethod
    def from_mapping(cls, value: object) -> "ScreenplayIntentScope":
        raw = value if isinstance(value, Mapping) else {}
        return cls(
            kind=ScreenplayScopeKind(
                _text(raw.get("kind")) or ScreenplayScopeKind.CURRENT_STAGE
            ),
            count=raw.get("count") if raw.get("count") is not None else None,
            episode_numbers=tuple(raw.get("episodeNumbers") or ()),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            **({"count": self.count} if self.count is not None else {}),
            **(
                {"episodeNumbers": list(self.episode_numbers)}
                if self.episode_numbers
                else {}
            ),
        }


@dataclass(frozen=True, slots=True)
class ScreenplayIntent:
    action: ScreenplayIntentAction
    instruction: str
    scope: ScreenplayIntentScope = ScreenplayIntentScope()
    constraints: tuple[str, ...] = ()
    preserve: tuple[str, ...] = ()
    requested_deliverable: str | None = None
    reply: str | None = None

    def __post_init__(self) -> None:
        action = ScreenplayIntentAction(self.action)
        instruction = _text(self.instruction)
        if not instruction:
            raise ValueError("screenplay intent instruction is required")
        object.__setattr__(self, "action", action)
        object.__setattr__(self, "instruction", instruction)
        if not isinstance(self.scope, ScreenplayIntentScope):
            raise TypeError("screenplay intent scope is invalid")
        object.__setattr__(self, "constraints", _text_tuple(self.constraints))
        object.__setattr__(self, "preserve", _text_tuple(self.preserve))
        object.__setattr__(
            self,
            "requested_deliverable",
            _text(self.requested_deliverable) or None,
        )
        if (
            self.requested_deliverable is not None
            and self.requested_deliverable not in SCREENPLAY_DELIVERABLE_ROLES
        ):
            raise ValueError("requested deliverable is invalid")
        object.__setattr__(self, "reply", _text(self.reply) or None)
        if action is ScreenplayIntentAction.ANSWER and self.reply is None:
            raise ValueError("answer intent requires a reply")
        if action is not ScreenplayIntentAction.ANSWER and self.reply is not None:
            raise ValueError("actionable screenplay intent cannot contain a final reply")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ScreenplayIntent":
        return cls(
            action=ScreenplayIntentAction(_text(value.get("action"))),
            instruction=_text(value.get("instruction")),
            scope=ScreenplayIntentScope.from_mapping(value.get("scope")),
            constraints=tuple(value.get("constraints") or ()),
            preserve=tuple(value.get("preserve") or ()),
            requested_deliverable=_text(value.get("requestedDeliverable")) or None,
            reply=_text(value.get("reply")) or None,
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "action": self.action.value,
            "instruction": self.instruction,
            "scope": self.scope.to_mapping(),
            "constraints": list(self.constraints),
            "preserve": list(self.preserve),
            **(
                {"requestedDeliverable": self.requested_deliverable}
                if self.requested_deliverable
                else {}
            ),
            **({"reply": self.reply} if self.reply else {}),
        }


@dataclass(frozen=True, slots=True)
class ReviewEpisodeInputRef:
    reviewed_revision_id: str
    episode_number: int
    scene_part_refs: tuple[str, ...]
    scene_plan_revision_id: str
    content_digest: str

    def __post_init__(self) -> None:
        for name in (
            "reviewed_revision_id",
            "scene_plan_revision_id",
            "content_digest",
        ):
            value = _text(getattr(self, name))
            if not value:
                raise ValueError(f"review episode input {name} is required")
            object.__setattr__(self, name, value)
        number = int(self.episode_number)
        if number <= 0:
            raise ValueError("review episode input number must be positive")
        object.__setattr__(self, "episode_number", number)
        refs = _text_tuple(self.scene_part_refs)
        if not refs:
            raise ValueError("review episode input scene Part refs are required")
        object.__setattr__(self, "scene_part_refs", refs)

    def to_mapping(self) -> dict[str, Any]:
        return {
            "reviewedRevisionId": self.reviewed_revision_id,
            "episodeNumber": self.episode_number,
            "scenePartRefs": list(self.scene_part_refs),
            "scenePlanRevisionId": self.scene_plan_revision_id,
            "contentDigest": self.content_digest,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ReviewEpisodeInputRef":
        return cls(
            reviewed_revision_id=_text(value.get("reviewedRevisionId")),
            episode_number=int(value.get("episodeNumber") or 0),
            scene_part_refs=tuple(value.get("scenePartRefs") or ()),
            scene_plan_revision_id=_text(value.get("scenePlanRevisionId")),
            content_digest=_text(value.get("contentDigest")),
        )


_REVIEW_EXECUTION_FIELDS = frozenset({
    "error",
    "errorCode",
    "executionError",
    "failedEpisodes",
    "failure",
    "failureCode",
})


@dataclass(frozen=True, slots=True)
class ReviewEpisodeResult:
    episode_number: int
    reviewed_revision_id: str
    reviewed_content_digest: str
    issues: tuple[Mapping[str, Any], ...]
    verdict: str
    part_receipts: tuple[str, ...]

    def __post_init__(self) -> None:
        number = int(self.episode_number)
        if number <= 0:
            raise ValueError("review episode result number must be positive")
        object.__setattr__(self, "episode_number", number)
        for name in ("reviewed_revision_id", "reviewed_content_digest"):
            value = _text(getattr(self, name))
            if not value:
                raise ValueError(f"review episode result {name} is required")
            object.__setattr__(self, name, value)
        verdict = _text(self.verdict)
        if verdict not in {"ready", "revise", "major_rework"}:
            raise ValueError("review episode result verdict is invalid")
        object.__setattr__(self, "verdict", verdict)
        issues = tuple(dict(value) for value in self.issues)
        object.__setattr__(self, "issues", issues)
        receipts = _text_tuple(self.part_receipts)
        if len(receipts) != 5:
            raise ValueError("review episode result requires five Part receipts")
        object.__setattr__(self, "part_receipts", receipts)

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
        *,
        part_receipts: tuple[str, ...],
    ) -> "ReviewEpisodeResult":
        contaminated = _REVIEW_EXECUTION_FIELDS.intersection(value)
        if contaminated:
            raise ValueError("review episode result contains execution metadata")
        if str(value.get("reviewStatus") or "") != "completed":
            raise ValueError("review episode result is not completed")
        if int(value.get("inputContractVersion") or 0) < 2:
            raise ValueError("review episode result input is not verified")
        raw_issues = value.get("issues")
        if not isinstance(raw_issues, list):
            raise ValueError("review episode result issues are required")
        return cls(
            episode_number=int(value.get("reviewedEpisode") or 0),
            reviewed_revision_id=_text(value.get("reviewedDraftId")),
            reviewed_content_digest=_text(value.get("reviewedContentDigest")),
            issues=tuple(
                dict(item) for item in raw_issues if isinstance(item, Mapping)
            ),
            verdict=_text(value.get("verdict")),
            part_receipts=part_receipts,
        )

    def to_mapping(self) -> dict[str, Any]:
        issues = [dict(value) for value in self.issues]
        return {
            "verdict": self.verdict,
            "issues": issues,
            "issueCount": len(issues),
            "criticalIssueCount": sum(
                str(issue.get("severity") or "") == "critical"
                for issue in issues
            ),
            "reviewedEpisode": self.episode_number,
            "reviewedDraftId": self.reviewed_revision_id,
            "reviewedContentDigest": self.reviewed_content_digest,
            "reviewDimensions": [
                "continuity",
                "character_arc",
                "structure_rhythm",
                "dialogue",
                "format",
            ],
            "reviewStatus": "completed",
            "inputContractVersion": 2,
            "partReceipts": list(self.part_receipts),
        }


__all__ = [
    "ScreenplayIntent",
    "ScreenplayIntentAction",
    "ScreenplayIntentScope",
    "ScreenplayScopeKind",
    "SCREENPLAY_DELIVERABLE_ROLES",
    "ReviewEpisodeInputRef",
    "ReviewEpisodeResult",
]
