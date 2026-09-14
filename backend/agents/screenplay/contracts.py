"""Canonical Part and Operation contracts for Screenplay replacement v1."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Mapping


SCREENPLAY_REPLACEMENT_SCHEMA_VERSION = 1
SCREENPLAY_REPLACEMENT_RECIPE_VERSION = 1
SCREENPLAY_REPLACEMENT_DOMAIN_NAMESPACE = "purrtypos.screenplay"
SCREENPLAY_OPERATION_MAX_MODEL_ROUNDS = 6

SCREENPLAY_DELIVERABLE_ROLES = frozenset({
    "sourceAnalysis",
    "creativeBrief",
    "structure",
    "sceneList",
    "screenplayDraft",
    "review",
})


class ScreenplayStageAction(StrEnum):
    ANSWER = "answer"
    CREATE = "create"
    REVISE = "revise"
    REVIEW = "review"


class ScreenplayStageScopeKind(StrEnum):
    CURRENT_STAGE = "current_stage"
    NEXT_EPISODES = "next_episodes"
    EPISODES = "episodes"
    ALL_REMAINING = "all_remaining"


@dataclass(frozen=True, slots=True)
class ScreenplayStageScope:
    kind: ScreenplayStageScopeKind = ScreenplayStageScopeKind.CURRENT_STAGE
    count: int | None = None
    episode_numbers: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", ScreenplayStageScopeKind(self.kind))
        numbers = tuple(int(value) for value in self.episode_numbers)
        if any(value <= 0 for value in numbers) or len(numbers) != len(set(numbers)):
            raise ValueError("episode numbers must be unique positive integers")
        object.__setattr__(self, "episode_numbers", numbers)
        if self.count is not None:
            count = int(self.count)
            if not 1 <= count <= 100:
                raise ValueError("episode count must be between 1 and 100")
            object.__setattr__(self, "count", count)
        if self.kind is ScreenplayStageScopeKind.NEXT_EPISODES and self.count is None:
            raise ValueError("next_episodes scope requires count")
        if self.kind is ScreenplayStageScopeKind.EPISODES and not numbers:
            raise ValueError("episodes scope requires episode numbers")

    @classmethod
    def from_mapping(cls, value: object) -> "ScreenplayStageScope":
        raw = value if isinstance(value, Mapping) else {}
        return cls(
            kind=ScreenplayStageScopeKind(
                str(raw.get("kind") or "").strip()
                or ScreenplayStageScopeKind.CURRENT_STAGE
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
class ScreenplayStageCommand:
    """Host-owned formal command; independent from the frozen Planner Intent."""

    action: ScreenplayStageAction
    target_role: str
    scope: ScreenplayStageScope
    kind: str = "stage_action"

    def __post_init__(self) -> None:
        action = ScreenplayStageAction(self.action)
        target_role = str(self.target_role or "").strip()
        kind = str(self.kind or "").strip()
        if kind != "stage_action":
            raise ValueError("screenplay stage command kind is invalid")
        if action is ScreenplayStageAction.ANSWER:
            raise ValueError("screenplay stage command cannot be answer")
        if target_role not in SCREENPLAY_DELIVERABLE_ROLES:
            raise ValueError("screenplay stage command target is invalid")
        if not isinstance(self.scope, ScreenplayStageScope):
            raise TypeError("screenplay stage command scope is invalid")
        if action is ScreenplayStageAction.REVIEW and target_role != "review":
            raise ValueError("review stage command requires review target")
        if action is not ScreenplayStageAction.REVIEW and target_role == "review":
            raise ValueError("review target requires review action")
        _validate_stage_scope(self.scope)
        object.__setattr__(self, "action", action)
        object.__setattr__(self, "target_role", target_role)
        object.__setattr__(self, "kind", kind)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ScreenplayStageCommand":
        return cls(
            kind=str(value.get("kind") or "").strip(),
            action=ScreenplayStageAction(
                str(value.get("action") or "").strip()
            ),
            target_role=str(value.get("targetRole") or "").strip(),
            scope=ScreenplayStageScope.from_mapping(value.get("scope")),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "action": self.action.value,
            "targetRole": self.target_role,
            "scope": self.scope.to_mapping(),
        }


def _validate_stage_scope(scope: ScreenplayStageScope) -> None:
    if scope.kind is ScreenplayStageScopeKind.NEXT_EPISODES:
        if scope.episode_numbers:
            raise ValueError("next_episodes stage command cannot list episodes")
        return
    if scope.kind is ScreenplayStageScopeKind.EPISODES:
        if scope.count is not None:
            raise ValueError("episodes stage command cannot include count")
        return
    if scope.count is not None or scope.episode_numbers:
        raise ValueError("stage command scope contains incompatible fields")


class ScreenplayPartKind(StrEnum):
    EVIDENCE = "evidence"
    DRAFT_SCENE = "draft_scene"
    EPISODE_METADATA = "episode_metadata"
    REVIEW_DIMENSION = "review_dimension"
    DOCUMENT_SECTION = "document_section"
    EXPANSION = "expansion"
    HOST_PROJECTION = "host_projection"
    VALIDATION = "validation"
    FINAL_RESPONSE = "final_response"


class ScreenplayPartCompletion(StrEnum):
    CANDIDATE_TOOL = "candidate_tool"
    HOST_CAPTURE = "host_capture"
    HOST_ONLY = "host_only"


@dataclass(frozen=True, slots=True)
class ScreenplayPartDefinition:
    kind: ScreenplayPartKind
    completion: ScreenplayPartCompletion
    tool_profile: str
    presentation_key: str
    max_attempts: int

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("Screenplay Part max_attempts must be positive")
        if not self.tool_profile or not self.presentation_key:
            raise ValueError("Screenplay Part metadata is required")


_DEFINITIONS = (
    ScreenplayPartDefinition(ScreenplayPartKind.EVIDENCE, ScreenplayPartCompletion.CANDIDATE_TOOL, "source_read_candidate", "screenplay.part.evidence", 2),
    ScreenplayPartDefinition(ScreenplayPartKind.DRAFT_SCENE, ScreenplayPartCompletion.HOST_CAPTURE, "draft_scene_read", "screenplay.part.draft_scene", 4),
    ScreenplayPartDefinition(ScreenplayPartKind.EPISODE_METADATA, ScreenplayPartCompletion.HOST_CAPTURE, "episode_metadata_read", "screenplay.part.episode_metadata", 2),
    ScreenplayPartDefinition(ScreenplayPartKind.REVIEW_DIMENSION, ScreenplayPartCompletion.CANDIDATE_TOOL, "revision_read_candidate", "screenplay.part.review_dimension", 4),
    ScreenplayPartDefinition(ScreenplayPartKind.DOCUMENT_SECTION, ScreenplayPartCompletion.CANDIDATE_TOOL, "bounded_material_candidate", "screenplay.part.document_section", 4),
    ScreenplayPartDefinition(ScreenplayPartKind.EXPANSION, ScreenplayPartCompletion.CANDIDATE_TOOL, "dependency_read_candidate", "screenplay.part.expansion", 2),
    ScreenplayPartDefinition(ScreenplayPartKind.HOST_PROJECTION, ScreenplayPartCompletion.HOST_ONLY, "host_only", "screenplay.part.host_projection", 2),
    ScreenplayPartDefinition(ScreenplayPartKind.VALIDATION, ScreenplayPartCompletion.HOST_ONLY, "host_only", "screenplay.part.validation", 2),
    ScreenplayPartDefinition(ScreenplayPartKind.FINAL_RESPONSE, ScreenplayPartCompletion.HOST_ONLY, "host_only", "screenplay.part.final_response", 2),
)
SCREENPLAY_PART_REGISTRY: Mapping[ScreenplayPartKind, ScreenplayPartDefinition] = (
    MappingProxyType({item.kind: item for item in _DEFINITIONS})
)


@dataclass(frozen=True, slots=True)
class ScreenplaySourceItemRef:
    source_type: str
    source_id: str
    content_digest: str

    def __post_init__(self) -> None:
        if self.source_type != "chapter":
            raise ValueError("Screenplay replacement source type is unsupported")
        for name in ("source_id", "content_digest"):
            value = str(getattr(self, name) or "").strip()
            if not value:
                raise ValueError(f"Screenplay source {name} is required")
            object.__setattr__(self, name, value)

    def to_mapping(self) -> dict[str, str]:
        return {
            "sourceType": self.source_type,
            "sourceId": self.source_id,
            "contentDigest": self.content_digest,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ScreenplaySourceItemRef":
        if not isinstance(value, Mapping) or set(value) != {
            "sourceType", "sourceId", "contentDigest",
        }:
            raise ValueError("Screenplay source item shape is invalid")
        return cls(
            source_type=value["sourceType"],
            source_id=value["sourceId"],
            content_digest=value["contentDigest"],
        )


@dataclass(frozen=True, slots=True)
class ScreenplayPartOperationScope:
    """The only Part attempt identity; it is deliberately not a RunBinding."""

    project_id: str
    task_id: str
    unit_id: str
    attempt: int
    part_kind: ScreenplayPartKind
    part_key: str
    target_role: str
    source_revision_refs: tuple[str, ...]
    deliverable_revision_scope: Mapping[str, str]
    dependency_part_keys: tuple[str, ...] = ()
    source_book_id: str | None = None
    source_items: tuple[ScreenplaySourceItemRef, ...] = ()
    episode_number: int | None = None
    scene_id: str | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ScreenplayPartOperationScope":
        required = {
            "schemaVersion", "projectId", "taskId", "unitId", "attempt",
            "operationScopeId", "partKind", "partKey", "targetRole",
            "sourceRevisionRefs", "deliverableRevisionScope", "sourceBookId",
            "sourceItems",
            "dependencyPartKeys",
            "episodeNumber", "sceneId",
        }
        if not isinstance(value, Mapping) or set(value) != required:
            raise ValueError("Screenplay Operation scope shape is invalid")
        if value["schemaVersion"] != SCREENPLAY_REPLACEMENT_SCHEMA_VERSION:
            raise ValueError("Screenplay Operation scope schema is invalid")
        source_refs = value["sourceRevisionRefs"]
        revisions = value["deliverableRevisionScope"]
        source_items = value["sourceItems"]
        dependency_keys = value["dependencyPartKeys"]
        if (
            not isinstance(source_refs, (list, tuple))
            or not isinstance(revisions, Mapping)
            or not isinstance(source_items, (list, tuple))
            or not isinstance(dependency_keys, (list, tuple))
        ):
            raise ValueError("Screenplay Operation revision scope is invalid")
        scope = cls(
            project_id=value["projectId"],
            task_id=value["taskId"],
            unit_id=value["unitId"],
            attempt=value["attempt"],
            part_kind=value["partKind"],
            part_key=value["partKey"],
            target_role=value["targetRole"],
            source_revision_refs=tuple(source_refs),
            deliverable_revision_scope=revisions,
            dependency_part_keys=tuple(dependency_keys),
            source_book_id=value["sourceBookId"],
            source_items=tuple(
                ScreenplaySourceItemRef.from_mapping(item)
                for item in source_items
            ),
            episode_number=value["episodeNumber"],
            scene_id=value["sceneId"],
        )
        if value["operationScopeId"] != scope.operation_scope_id:
            raise ValueError("Screenplay Operation scope identity is invalid")
        return scope

    def __post_init__(self) -> None:
        for name in ("project_id", "task_id", "unit_id", "part_key", "target_role"):
            if not str(getattr(self, name) or "").strip():
                raise ValueError(f"Screenplay Operation {name} is required")
        if type(self.attempt) is not int or self.attempt < 1:
            raise ValueError("Screenplay Operation attempt must be positive")
        object.__setattr__(self, "part_kind", ScreenplayPartKind(self.part_kind))
        if not isinstance(self.source_revision_refs, (list, tuple)):
            raise ValueError("Screenplay source revision refs must be an array")
        if not isinstance(self.deliverable_revision_scope, Mapping):
            raise ValueError("Screenplay deliverable revision scope must be an object")
        if not isinstance(self.dependency_part_keys, (list, tuple)):
            raise ValueError("Screenplay dependency Part keys must be an array")
        dependency_keys = tuple(
            str(value or "").strip() for value in self.dependency_part_keys
        )
        if (
            any(not value for value in dependency_keys)
            or len(set(dependency_keys)) != len(dependency_keys)
        ):
            raise ValueError("Screenplay dependency Part keys are invalid")
        source_book_id = str(self.source_book_id or "").strip() or None
        source_items = tuple(self.source_items)
        if any(not isinstance(item, ScreenplaySourceItemRef) for item in source_items):
            raise ValueError("Screenplay source items are invalid")
        if bool(source_book_id) != bool(source_items):
            raise ValueError("Screenplay source book and items must be declared together")
        episode_number = self.episode_number
        if episode_number is not None and (
            type(episode_number) is not int or episode_number < 1
        ):
            raise ValueError("Screenplay episode number must be positive")
        scene_id = str(self.scene_id or "").strip() or None
        if scene_id is not None and episode_number is None:
            raise ValueError("Screenplay scene scope requires an episode")
        if self.part_kind is ScreenplayPartKind.DRAFT_SCENE and (
            episode_number is None or scene_id != self.part_key
        ):
            raise ValueError("Screenplay draft scene identity is invalid")
        if self.part_kind is ScreenplayPartKind.EPISODE_METADATA and (
            episode_number is None or scene_id is not None
        ):
            raise ValueError("Screenplay episode metadata identity is invalid")
        source_refs = tuple(str(value).strip() for value in self.source_revision_refs)
        if any(not value for value in source_refs) or len(set(source_refs)) != len(source_refs):
            raise ValueError("Screenplay source revision refs must be unique non-empty ids")
        revisions = {
            str(role).strip(): str(revision_id).strip()
            for role, revision_id in self.deliverable_revision_scope.items()
        }
        if any(not role or not revision_id for role, revision_id in revisions.items()):
            raise ValueError("Screenplay deliverable revision scope is invalid")
        object.__setattr__(self, "source_revision_refs", source_refs)
        object.__setattr__(self, "deliverable_revision_scope", MappingProxyType(revisions))
        object.__setattr__(self, "source_book_id", source_book_id)
        object.__setattr__(self, "source_items", source_items)
        object.__setattr__(self, "dependency_part_keys", dependency_keys)
        object.__setattr__(self, "scene_id", scene_id)

    @property
    def operation_scope_id(self) -> str:
        return f"{self.task_id}:{self.unit_id}:{self.attempt}"

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schemaVersion": SCREENPLAY_REPLACEMENT_SCHEMA_VERSION,
            "projectId": self.project_id,
            "taskId": self.task_id,
            "unitId": self.unit_id,
            "attempt": self.attempt,
            "operationScopeId": self.operation_scope_id,
            "partKind": self.part_kind.value,
            "partKey": self.part_key,
            "targetRole": self.target_role,
            "sourceRevisionRefs": list(self.source_revision_refs),
            "deliverableRevisionScope": dict(self.deliverable_revision_scope),
            "sourceBookId": self.source_book_id,
            "sourceItems": [item.to_mapping() for item in self.source_items],
            "dependencyPartKeys": list(self.dependency_part_keys),
            "episodeNumber": self.episode_number,
            "sceneId": self.scene_id,
        }


def screenplay_part_definition(kind: ScreenplayPartKind | str) -> ScreenplayPartDefinition:
    return SCREENPLAY_PART_REGISTRY[ScreenplayPartKind(kind)]


__all__ = [
    "SCREENPLAY_DELIVERABLE_ROLES",
    "SCREENPLAY_PART_REGISTRY",
    "SCREENPLAY_REPLACEMENT_DOMAIN_NAMESPACE",
    "SCREENPLAY_REPLACEMENT_RECIPE_VERSION",
    "SCREENPLAY_REPLACEMENT_SCHEMA_VERSION",
    "ScreenplayPartCompletion",
    "ScreenplayPartDefinition",
    "ScreenplayPartKind",
    "ScreenplayPartOperationScope",
    "ScreenplaySourceItemRef",
    "ScreenplayStageAction",
    "ScreenplayStageCommand",
    "ScreenplayStageScope",
    "ScreenplayStageScopeKind",
    "screenplay_part_definition",
]
