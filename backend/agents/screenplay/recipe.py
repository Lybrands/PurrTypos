"""PurrA recipe compiler driven exclusively by the Screenplay Part registry."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence as SequenceABC
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from agents.screenplay.contracts import (
    SCREENPLAY_REPLACEMENT_RECIPE_VERSION,
    SCREENPLAY_REPLACEMENT_SCHEMA_VERSION,
    ScreenplayPartCompletion,
    ScreenplayPartKind,
    ScreenplaySourceItemRef,
    screenplay_part_definition,
)
from agents.screenplay.access_contract import screenplay_part_tool_names
from purra.contracts import ExecutionRecipe, ExecutionRecipeStep


@dataclass(frozen=True, slots=True)
class ScreenplayRecipePart:
    id: str
    kind: ScreenplayPartKind
    semantic_key: str
    depends_on: tuple[str, ...] = ()
    source_revision_refs: tuple[str, ...] = ()
    deliverable_revision_scope: Mapping[str, str] | None = None
    source_book_id: str | None = None
    source_items: tuple[ScreenplaySourceItemRef, ...] = ()
    episode_number: int | None = None
    scene_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", ScreenplayPartKind(self.kind))
        for name in ("id", "semantic_key"):
            value = str(getattr(self, name) or "").strip()
            if not value:
                raise ValueError(f"Screenplay recipe Part {name} is required")
            object.__setattr__(self, name, value)
        if not isinstance(self.depends_on, SequenceABC) or isinstance(
            self.depends_on, (str, bytes)
        ):
            raise ValueError("Screenplay recipe dependencies are invalid")
        dependencies = tuple(str(value or "").strip() for value in self.depends_on)
        if any(not value for value in dependencies) or len(set(dependencies)) != len(dependencies):
            raise ValueError("Screenplay recipe dependencies are invalid")
        object.__setattr__(self, "depends_on", dependencies)
        if not isinstance(self.source_revision_refs, SequenceABC) or isinstance(
            self.source_revision_refs, (str, bytes)
        ):
            raise ValueError("Screenplay recipe source revisions are invalid")
        source_revision_refs = tuple(
            str(value or "").strip() for value in self.source_revision_refs
        )
        if (
            any(not value for value in source_revision_refs)
            or len(set(source_revision_refs)) != len(source_revision_refs)
        ):
            raise ValueError("Screenplay recipe source revisions are invalid")
        object.__setattr__(self, "source_revision_refs", source_revision_refs)
        # None is rejected: every Part, including structure, must declare scope.
        if self.deliverable_revision_scope is None or not isinstance(
            self.deliverable_revision_scope, Mapping
        ):
            raise ValueError("Screenplay recipe Part must declare revision scope")
        revision_scope = {
            str(role or "").strip(): str(revision_id or "").strip()
            for role, revision_id in self.deliverable_revision_scope.items()
        }
        if any(not role or not revision_id for role, revision_id in revision_scope.items()):
            raise ValueError("Screenplay recipe revision scope is invalid")
        object.__setattr__(
            self,
            "deliverable_revision_scope",
            MappingProxyType(revision_scope),
        )
        source_book_id = str(self.source_book_id or "").strip() or None
        if not isinstance(self.source_items, SequenceABC) or isinstance(
            self.source_items, (str, bytes)
        ):
            raise ValueError("Screenplay recipe source items are invalid")
        source_items = tuple(self.source_items)
        if any(not isinstance(item, ScreenplaySourceItemRef) for item in source_items):
            raise ValueError("Screenplay recipe source items are invalid")
        source_keys = tuple((item.source_type, item.source_id) for item in source_items)
        if len(set(source_keys)) != len(source_keys):
            raise ValueError("Screenplay recipe source items must be unique")
        if bool(source_book_id) != bool(source_items):
            raise ValueError("Screenplay recipe source book and items must match")
        if self.episode_number is not None and (
            type(self.episode_number) is not int or self.episode_number < 1
        ):
            raise ValueError("Screenplay recipe episode number must be positive")
        scene_id = str(self.scene_id or "").strip() or None
        if scene_id is not None and self.episode_number is None:
            raise ValueError("Screenplay recipe scene scope requires an episode")
        if self.kind is ScreenplayPartKind.DRAFT_SCENE and (
            self.episode_number is None or scene_id != self.semantic_key
        ):
            raise ValueError("Screenplay recipe draft scene identity is invalid")
        if self.kind is ScreenplayPartKind.EPISODE_METADATA and (
            self.episode_number is None or scene_id is not None
        ):
            raise ValueError("Screenplay recipe episode metadata identity is invalid")
        object.__setattr__(self, "source_book_id", source_book_id)
        object.__setattr__(self, "source_items", source_items)
        object.__setattr__(self, "scene_id", scene_id)

    def to_mapping(self) -> dict[str, object]:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "semanticKey": self.semantic_key,
            "dependsOn": list(self.depends_on),
            "sourceRevisionRefs": list(self.source_revision_refs),
            "deliverableRevisionScope": dict(
                self.deliverable_revision_scope or {}
            ),
            "sourceBookId": self.source_book_id,
            "sourceItems": [item.to_mapping() for item in self.source_items],
            "episodeNumber": self.episode_number,
            "sceneId": self.scene_id,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ScreenplayRecipePart":
        required = {
            "id", "kind", "semanticKey", "dependsOn", "sourceRevisionRefs",
            "deliverableRevisionScope", "sourceBookId", "sourceItems",
            "episodeNumber", "sceneId",
        }
        if not isinstance(value, Mapping) or set(value) != required:
            raise ValueError("Screenplay host recipe Part shape is invalid")
        if (
            not isinstance(value["dependsOn"], SequenceABC)
            or isinstance(value["dependsOn"], (str, bytes))
            or not isinstance(value["sourceRevisionRefs"], SequenceABC)
            or isinstance(value["sourceRevisionRefs"], (str, bytes))
            or not isinstance(value["deliverableRevisionScope"], Mapping)
            or not isinstance(value["sourceItems"], SequenceABC)
            or isinstance(value["sourceItems"], (str, bytes))
        ):
            raise ValueError("Screenplay host recipe Part collections are invalid")
        return cls(
            id=value["id"],
            kind=value["kind"],
            semantic_key=value["semanticKey"],
            depends_on=tuple(value["dependsOn"]),
            source_revision_refs=tuple(value["sourceRevisionRefs"]),
            deliverable_revision_scope=value["deliverableRevisionScope"],
            source_book_id=value["sourceBookId"],
            source_items=tuple(
                ScreenplaySourceItemRef.from_mapping(item)
                for item in value["sourceItems"]
            ),
            episode_number=value["episodeNumber"],
            scene_id=value["sceneId"],
        )


@dataclass(frozen=True, slots=True)
class ScreenplayHostRecipeSpec:
    """Application-owned formal intent used to compile the durable DAG."""

    operation: str
    target_role: str
    max_parallelism: int
    parts: tuple[ScreenplayRecipePart, ...]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ScreenplayHostRecipeSpec":
        required = {
            "recipeVersion", "operation", "targetRole", "maxParallelism", "parts",
        }
        if not isinstance(value, Mapping) or set(value) != required:
            raise ValueError("Screenplay host recipe shape is invalid")
        if value["recipeVersion"] != SCREENPLAY_REPLACEMENT_RECIPE_VERSION:
            raise ValueError("Screenplay host recipe version is invalid")
        operation = str(value["operation"] or "").strip()
        target_role = str(value["targetRole"] or "").strip()
        if operation not in {"create", "revise", "review"}:
            raise ValueError("Screenplay host recipe operation is invalid")
        if not target_role:
            raise ValueError("Screenplay host recipe target role is required")
        if type(value["maxParallelism"]) is not int or value["maxParallelism"] < 1:
            raise ValueError("Screenplay host recipe maxParallelism is invalid")
        if (
            not isinstance(value["parts"], SequenceABC)
            or isinstance(value["parts"], (str, bytes))
            or not value["parts"]
        ):
            raise ValueError("Screenplay host recipe Parts are required")
        parts = tuple(ScreenplayRecipePart.from_mapping(item) for item in value["parts"])
        by_kind = {
            kind: tuple(part for part in parts if part.kind is kind)
            for kind in (
                ScreenplayPartKind.HOST_PROJECTION,
                ScreenplayPartKind.VALIDATION,
                ScreenplayPartKind.FINAL_RESPONSE,
            )
        }
        if any(len(values) != 1 for values in by_kind.values()):
            raise ValueError("Screenplay host recipe settlement chain is invalid")
        projection = by_kind[ScreenplayPartKind.HOST_PROJECTION][0]
        validation = by_kind[ScreenplayPartKind.VALIDATION][0]
        final_response = by_kind[ScreenplayPartKind.FINAL_RESPONSE][0]
        if (
            validation.depends_on != (projection.id,)
            or final_response.depends_on != (validation.id,)
            or parts[-3:] != (projection, validation, final_response)
        ):
            raise ValueError("Screenplay host recipe settlement chain is invalid")
        return cls(
            operation=operation,
            target_role=target_role,
            max_parallelism=value["maxParallelism"],
            parts=parts,
        )

    def compile(
        self,
        *,
        project_id: str,
        plan_step_ids: Sequence[str] = (),
    ) -> ExecutionRecipe:
        return compile_screenplay_replacement_recipe(
            project_id=project_id,
            target_role=self.target_role,
            parts=self.parts,
            max_parallelism=self.max_parallelism,
            plan_step_ids=plan_step_ids,
        )

    def to_mapping(self) -> dict[str, object]:
        return {
            "recipeVersion": SCREENPLAY_REPLACEMENT_RECIPE_VERSION,
            "operation": self.operation,
            "targetRole": self.target_role,
            "maxParallelism": self.max_parallelism,
            "parts": [part.to_mapping() for part in self.parts],
        }


def compile_screenplay_replacement_recipe(
    *,
    project_id: str,
    target_role: str,
    parts: Sequence[ScreenplayRecipePart],
    max_parallelism: int,
    plan_step_ids: Sequence[str] = (),
) -> ExecutionRecipe:
    project = str(project_id or "").strip()
    role = str(target_role or "").strip()
    frozen = tuple(parts)
    if not project or not role or not frozen:
        raise ValueError("Screenplay replacement recipe scope is incomplete")
    if type(max_parallelism) is not int or max_parallelism < 1:
        raise ValueError("Screenplay replacement max_parallelism must be positive")
    planner_steps = tuple(str(value or "").strip() for value in plan_step_ids)
    if any(not value for value in planner_steps) or len(set(planner_steps)) != len(planner_steps):
        raise ValueError("Screenplay replacement plan step ids are invalid")
    if len(planner_steps) > len(frozen):
        raise ValueError("Planner has more steps than Screenplay recipe Parts")
    ids = tuple(item.id for item in frozen)
    if len(set(ids)) != len(ids):
        raise ValueError("Screenplay replacement Part ids must be unique")
    semantic_keys = tuple(item.semantic_key for item in frozen)
    if len(set(semantic_keys)) != len(semantic_keys):
        raise ValueError("Screenplay replacement semantic keys must be unique")
    known: set[str] = set()
    preceding_model_parts: list[str] = []
    for item in frozen:
        if any(value not in known for value in item.depends_on):
            raise ValueError("Screenplay replacement dependencies must point backward")
        definition = screenplay_part_definition(item.kind)
        if item.kind is ScreenplayPartKind.HOST_PROJECTION and set(
            item.depends_on
        ) != set(preceding_model_parts):
            raise ValueError(
                "Screenplay host projection must consume every model Part"
            )
        if definition.completion is not ScreenplayPartCompletion.HOST_ONLY:
            preceding_model_parts.append(item.id)
        known.add(item.id)

    canonical_parts = [_part_mapping(item) for item in frozen]
    digest = "sha256:" + hashlib.sha256(json.dumps(
        {
            "projectId": project,
            "targetRole": role,
            "maxParallelism": min(max_parallelism, len(frozen)),
            "parts": canonical_parts,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")).hexdigest()
    return ExecutionRecipe(
        kind="screenplay.purra-native",
        steps=tuple(
            _recipe_step(
                item,
                plan_step_id=(
                    planner_steps[min(index * len(planner_steps) // len(frozen), len(planner_steps) - 1)]
                    if planner_steps else None
                ),
                dependency_part_keys=tuple(
                    next(candidate.semantic_key for candidate in frozen if candidate.id == dependency)
                    for dependency in item.depends_on
                ),
            )
            for index, item in enumerate(frozen)
        ),
        max_parallelism=min(max_parallelism, len(frozen)),
        metadata={
            "recipeVersion": SCREENPLAY_REPLACEMENT_RECIPE_VERSION,
            "screenplaySchemaVersion": SCREENPLAY_REPLACEMENT_SCHEMA_VERSION,
            "projectId": project,
            "targetRole": role,
            "recipeDigest": digest,
        },
    )


def _part_mapping(part: ScreenplayRecipePart) -> dict[str, object]:
    definition = screenplay_part_definition(part.kind)
    return {
        "id": part.id,
        "kind": part.kind.value,
        "semanticKey": part.semantic_key,
        "dependsOn": list(part.depends_on),
        "sourceRevisionRefs": list(part.source_revision_refs),
        "deliverableRevisionScope": dict(part.deliverable_revision_scope or {}),
        "sourceBookId": part.source_book_id,
        "sourceItems": [item.to_mapping() for item in part.source_items],
        "episodeNumber": part.episode_number,
        "sceneId": part.scene_id,
        "completion": definition.completion.value,
        "toolProfile": definition.tool_profile,
        "toolNames": list(screenplay_part_tool_names(part.kind)),
        "presentationKey": definition.presentation_key,
        "maxAttempts": definition.max_attempts,
    }


def _recipe_step(
    part: ScreenplayRecipePart,
    *,
    plan_step_id: str | None,
    dependency_part_keys: tuple[str, ...],
) -> ExecutionRecipeStep:
    definition = screenplay_part_definition(part.kind)
    return ExecutionRecipeStep(
        # PurrA's public recipe compiler uses step.id as the durable Unit
        # semantic_key. Keep the host's local recipe id only as metadata and
        # use the canonical Part key for both durable identity and dependency
        # edges.
        id=part.semantic_key,
        kind=part.kind.value,
        depends_on=dependency_part_keys,
        executor="screenplay.purra-native",
        plan_step_id=plan_step_id,
        max_attempts=definition.max_attempts,
        metadata={
            **{
                key: value for key, value in _part_mapping(part).items()
                if key not in {"id", "kind", "dependsOn", "maxAttempts"}
            },
            "recipePartId": part.id,
            "partKind": part.kind.value,
            "dependencyPartKeys": list(dependency_part_keys),
        },
    )


__all__ = [
    "ScreenplayHostRecipeSpec",
    "ScreenplayRecipePart",
    "compile_screenplay_replacement_recipe",
]
