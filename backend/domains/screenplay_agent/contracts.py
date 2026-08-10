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


__all__ = [
    "ScreenplayIntent",
    "ScreenplayIntentAction",
    "ScreenplayIntentScope",
    "ScreenplayScopeKind",
    "SCREENPLAY_DELIVERABLE_ROLES",
]
