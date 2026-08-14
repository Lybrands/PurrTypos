"""Closed screenplay semantics compiled from one Root Run TaskSpec."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping

from purra.contracts import TaskSpec, TaskStep
from purra.json_values import thaw_json_mapping


class ScreenplayIntentAction(StrEnum):
    ANSWER = "answer"
    CREATE = "create"
    REVISE = "revise"
    REVIEW = "review"


class ScreenplayIntentCommandMismatchError(ValueError):
    code = "screenplay_intent_command_mismatch"


class ContinuationStartLost(RuntimeError):
    """A valid continuation starter lost its durable reservation fence."""


class ScreenplayRootStartLost(RuntimeError):
    """A claimed Turn lost its authority before the Root became durable."""


class ScreenplayScopeKind(StrEnum):
    CURRENT_STAGE = "current_stage"
    NEXT_EPISODES = "next_episodes"
    EPISODES = "episodes"
    ALL_REMAINING = "all_remaining"


class ScreenplayPlanPhase(StrEnum):
    EVIDENCE = "evidence"
    CREATION = "creation"
    REVIEW = "review"
    DELIVERY = "delivery"


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


def _require_exact_fields(
    value: Mapping[str, Any],
    expected: frozenset[str],
    label: str,
) -> None:
    if frozenset(value) != expected:
        raise ValueError(f"screenplay {label} fields are invalid")


@dataclass(frozen=True, slots=True)
class ScreenplayPlanBinding:
    step_id: str
    phase: ScreenplayPlanPhase

    def __post_init__(self) -> None:
        if not isinstance(self.step_id, str):
            raise ValueError("screenplay plan binding step id must be a string")
        step_id = self.step_id.strip()
        if not step_id:
            raise ValueError("screenplay plan binding step id is required")
        object.__setattr__(self, "step_id", step_id)
        if not isinstance(self.phase, str):
            raise ValueError("screenplay plan binding phase must be a string")
        try:
            phase = ScreenplayPlanPhase(self.phase.strip())
        except ValueError as error:
            raise ValueError("screenplay plan binding phase is invalid") from error
        object.__setattr__(self, "phase", phase)

    @classmethod
    def from_mapping(cls, value: object) -> "ScreenplayPlanBinding":
        if not isinstance(value, Mapping):
            raise ValueError("screenplay plan binding must be an object")
        _require_exact_fields(
            value,
            frozenset({"stepId", "phase"}),
            "plan binding",
        )
        step_id = value.get("stepId")
        phase = value.get("phase")
        if not isinstance(step_id, str):
            raise ValueError("screenplay plan binding step id must be a string")
        if not isinstance(phase, str):
            raise ValueError("screenplay plan binding phase must be a string")
        return cls(step_id=step_id, phase=phase)

    def to_mapping(self) -> dict[str, str]:
        return {"stepId": self.step_id, "phase": self.phase.value}


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

    @classmethod
    def from_task_spec_mapping(cls, value: object) -> "ScreenplayIntentScope":
        if not isinstance(value, Mapping):
            raise ValueError("screenplay scope must be an object")
        raw_kind = value.get("kind")
        if not isinstance(raw_kind, str):
            raise ValueError("screenplay scope kind must be a string")
        try:
            kind = ScreenplayScopeKind(raw_kind.strip())
        except ValueError as error:
            raise ValueError("screenplay scope kind is invalid") from error
        expected_fields = {
            ScreenplayScopeKind.CURRENT_STAGE: frozenset({"kind"}),
            ScreenplayScopeKind.NEXT_EPISODES: frozenset({"kind", "count"}),
            ScreenplayScopeKind.EPISODES: frozenset({"kind", "episodeNumbers"}),
            ScreenplayScopeKind.ALL_REMAINING: frozenset({"kind"}),
        }[kind]
        _require_exact_fields(value, expected_fields, "scope")
        if kind is ScreenplayScopeKind.NEXT_EPISODES:
            count = value.get("count")
            if isinstance(count, bool) or not isinstance(count, int):
                raise ValueError("screenplay scope count must be an integer")
        if kind is ScreenplayScopeKind.EPISODES:
            numbers = value.get("episodeNumbers")
            if (
                isinstance(numbers, (str, bytes, bytearray))
                or not isinstance(numbers, Sequence)
                or any(
                    isinstance(number, bool) or not isinstance(number, int)
                    for number in numbers
                )
            ):
                raise ValueError(
                    "screenplay scope episode numbers must be integer list"
                )
        return cls(
            kind=kind,
            count=value.get("count") if "count" in value else None,
            episode_numbers=tuple(value.get("episodeNumbers") or ()),
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
    plan_bindings: tuple[ScreenplayPlanBinding, ...] = ()

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
        bindings = tuple(self.plan_bindings)
        if any(not isinstance(value, ScreenplayPlanBinding) for value in bindings):
            raise TypeError("screenplay plan bindings are invalid")
        if len({value.step_id for value in bindings}) != len(bindings):
            raise ValueError("screenplay plan steps must be bound exactly once")
        object.__setattr__(self, "plan_bindings", bindings)

    @classmethod
    def from_task_spec(
        cls,
        task_spec: TaskSpec,
        plan_steps: Sequence[TaskStep],
    ) -> "ScreenplayIntent":
        if not isinstance(task_spec, TaskSpec):
            raise TypeError("screenplay intent requires a TaskSpec")
        steps = tuple(plan_steps)
        if not steps or any(not isinstance(step, TaskStep) for step in steps):
            raise TypeError("screenplay intent requires TaskPlan steps")
        step_ids = tuple(step.id for step in steps)
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("screenplay plan step ids must be unique")
        target = thaw_json_mapping(task_spec.target)
        if frozenset(target) != frozenset({"screenplay"}):
            raise ValueError("TaskSpec target must contain only screenplay")
        raw = target.get("screenplay")
        if not isinstance(raw, Mapping):
            raise ValueError("TaskSpec target screenplay must be an object")
        _require_exact_fields(
            raw,
            frozenset({"version", "scope", "stepBindings"}),
            "TaskSpec target",
        )
        version = raw.get("version")
        if type(version) is not int or version != 1:
            raise ValueError("screenplay TaskSpec version must be 1")
        scope = ScreenplayIntentScope.from_task_spec_mapping(raw.get("scope"))
        raw_bindings = raw.get("stepBindings")
        if isinstance(raw_bindings, (str, bytes, bytearray)) or not isinstance(
            raw_bindings,
            Sequence,
        ):
            raise ValueError("screenplay stepBindings must be a list")
        bindings = tuple(
            ScreenplayPlanBinding.from_mapping(value)
            for value in raw_bindings
        )
        binding_ids = tuple(binding.step_id for binding in bindings)
        if len(binding_ids) != len(set(binding_ids)):
            raise ValueError("screenplay plan steps must be bound exactly once")
        if set(binding_ids) != set(step_ids):
            raise ValueError("screenplay bindings must match all plan steps")

        action = ScreenplayIntentAction(_text(task_spec.operation))
        deliverable = _text(task_spec.deliverable) or None
        if action is ScreenplayIntentAction.ANSWER:
            if deliverable is not None:
                raise ValueError("answer TaskSpec cannot declare a deliverable")
        else:
            if deliverable not in SCREENPLAY_DELIVERABLE_ROLES:
                raise ValueError("formal TaskSpec requires a valid deliverable")
            if action is ScreenplayIntentAction.REVIEW and deliverable != "review":
                raise ValueError("review TaskSpec requires review deliverable")
            if action is not ScreenplayIntentAction.REVIEW and deliverable == "review":
                raise ValueError("review deliverable requires review operation")
        return cls(
            action=action,
            instruction=task_spec.instruction or task_spec.goal,
            scope=scope,
            constraints=task_spec.constraints,
            preserve=task_spec.preserve,
            requested_deliverable=deliverable,
            plan_bindings=bindings,
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ScreenplayIntent":
        return cls(
            action=ScreenplayIntentAction(_text(value.get("action"))),
            instruction=_text(value.get("instruction")),
            scope=ScreenplayIntentScope.from_mapping(value.get("scope")),
            constraints=tuple(value.get("constraints") or ()),
            preserve=tuple(value.get("preserve") or ()),
            requested_deliverable=_text(value.get("requestedDeliverable")) or None,
            plan_bindings=tuple(
                ScreenplayPlanBinding.from_mapping(item)
                for item in value.get("stepBindings") or ()
            ),
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
            **(
                {
                    "stepBindings": [
                        binding.to_mapping() for binding in self.plan_bindings
                    ]
                }
                if self.plan_bindings
                else {}
            ),
        }


@dataclass(frozen=True, slots=True)
class ScreenplayStageCommand:
    action: ScreenplayIntentAction
    target_role: str
    scope: ScreenplayIntentScope
    kind: str = "stage_action"

    def __post_init__(self) -> None:
        action = ScreenplayIntentAction(self.action)
        target_role = _text(self.target_role)
        kind = _text(self.kind)
        if kind != "stage_action":
            raise ValueError("screenplay stage command kind is invalid")
        if action is ScreenplayIntentAction.ANSWER:
            raise ValueError("screenplay stage command cannot be answer")
        if target_role not in SCREENPLAY_DELIVERABLE_ROLES:
            raise ValueError("screenplay stage command target is invalid")
        if not isinstance(self.scope, ScreenplayIntentScope):
            raise TypeError("screenplay stage command scope is invalid")
        if action is ScreenplayIntentAction.REVIEW and target_role != "review":
            raise ValueError("review stage command requires review target")
        if action is not ScreenplayIntentAction.REVIEW and target_role == "review":
            raise ValueError("review target requires review action")
        _validate_stage_scope(self.scope)
        object.__setattr__(self, "action", action)
        object.__setattr__(self, "target_role", target_role)
        object.__setattr__(self, "kind", kind)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ScreenplayStageCommand":
        return cls(
            kind=_text(value.get("kind")),
            action=ScreenplayIntentAction(_text(value.get("action"))),
            target_role=_text(value.get("targetRole")),
            scope=ScreenplayIntentScope.from_mapping(value.get("scope")),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "action": self.action.value,
            "targetRole": self.target_role,
            "scope": self.scope.to_mapping(),
        }

    def require_compatible(self, intent: ScreenplayIntent) -> None:
        if not isinstance(intent, ScreenplayIntent):
            raise TypeError("screenplay stage command requires an Intent")
        if (
            intent.action is not self.action
            or intent.requested_deliverable != self.target_role
            or intent.scope.kind is not self.scope.kind
            or intent.scope.count != self.scope.count
            or intent.scope.episode_numbers != self.scope.episode_numbers
        ):
            raise ScreenplayIntentCommandMismatchError(
                "stage command does not match planned screenplay intent"
            )


def _validate_stage_scope(scope: ScreenplayIntentScope) -> None:
    if scope.kind is ScreenplayScopeKind.NEXT_EPISODES:
        if scope.episode_numbers:
            raise ValueError("next_episodes stage command cannot list episodes")
        return
    if scope.kind is ScreenplayScopeKind.EPISODES:
        if scope.count is not None:
            raise ValueError("episodes stage command cannot include count")
        return
    if scope.count is not None or scope.episode_numbers:
        raise ValueError("stage command scope contains incompatible fields")


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
    "ScreenplayIntentCommandMismatchError",
    "ScreenplayIntentScope",
    "ScreenplayPlanBinding",
    "ScreenplayPlanPhase",
    "ScreenplayStageCommand",
    "ScreenplayScopeKind",
    "SCREENPLAY_DELIVERABLE_ROLES",
    "ReviewEpisodeInputRef",
    "ReviewEpisodeResult",
]
