"""Build the host-owned durable screenplay draft workflow.

The Core Planner chooses the user-visible draft capability and semantic scope.
Once the application has bound that scope to authoritative scene-list rows,
this module creates the stable execution DAG: checkpointed Writers, one global
review barrier, conditional per-scene Rewriters, and final proposal assembly.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from agent_core.contracts import ExecutionRecipe, ExecutionRecipeStep

MAX_SCREENPLAY_CHILD_AGENTS = 3


class ScreenplayDraftUnitKind(StrEnum):
    SCENE_GENERATION = "scene_generation"
    CONTINUITY_REVIEW = "continuity_review"
    SCENE_REVISION = "scene_revision"
    FINALIZE = "finalize"


@dataclass(frozen=True, slots=True)
class ScreenplayDraftWorkflowUnit:
    id: str
    kind: ScreenplayDraftUnitKind
    position: int
    depends_on: tuple[str, ...] = ()
    scene_ids: tuple[str, ...] = ()
    scene_headings: tuple[str, ...] = ()
    agent_role: str | None = None
    label: str = ""

    def to_metadata(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "id": self.id,
            "plannerStepId": self.id,
            "kind": self.kind.value,
            "position": self.position,
            "dependsOn": list(self.depends_on),
            "label": self.label,
        }
        if self.scene_ids:
            value["sceneIds"] = list(self.scene_ids)
            value["sceneHeadings"] = list(self.scene_headings)
        if self.agent_role:
            value["agentRole"] = self.agent_role
        return value


@dataclass(frozen=True, slots=True)
class ScreenplayDraftWorkflow:
    units: tuple[ScreenplayDraftWorkflowUnit, ...]

    @property
    def model_call_count(self) -> int:
        """Planned calls if every scene needs one revision; retries excluded."""

        return sum(unit.agent_role is not None for unit in self.units)

    @property
    def minimum_model_call_count(self) -> int:
        """Calls required before optional targeted revision."""

        return sum(
            unit.agent_role is not None
            and unit.kind is not ScreenplayDraftUnitKind.SCENE_REVISION
            for unit in self.units
        )

    @property
    def max_parallelism(self) -> int:
        levels: dict[str, int] = {}
        widths: dict[int, int] = {}
        for unit in self.units:
            level = (
                0
                if not unit.depends_on
                else 1 + max(levels[item] for item in unit.depends_on)
            )
            levels[unit.id] = level
            if unit.agent_role is not None:
                widths[level] = widths.get(level, 0) + 1
        return min(
            MAX_SCREENPLAY_CHILD_AGENTS,
            max(widths.values(), default=1),
        )

    def to_metadata(self) -> list[dict[str, Any]]:
        return [unit.to_metadata() for unit in self.units]

    def to_execution_recipe(self) -> ExecutionRecipe:
        """Lower the domain workflow to Core's opaque mechanical DAG."""

        return ExecutionRecipe(
            kind="screenplay_draft_generation",
            max_parallelism=self.max_parallelism,
            steps=tuple(
                ExecutionRecipeStep(
                    id=unit.id,
                    kind=unit.kind.value,
                    depends_on=unit.depends_on,
                    executor=unit.agent_role,
                    metadata={
                        "plannerStepId": unit.id,
                        "label": unit.label,
                        "sceneIds": list(unit.scene_ids),
                        "sceneHeadings": list(unit.scene_headings),
                        **(
                            {"agentRole": unit.agent_role}
                            if unit.agent_role
                            else {}
                        ),
                    },
                )
                for unit in self.units
            ),
        )


def build_screenplay_draft_workflow(
    scenes: Sequence[Mapping[str, Any]],
) -> ScreenplayDraftWorkflow:
    """Compile the mechanical draft DAG from the host-bound scene range.

    The model decides creative intent and scope.  It does not need authority
    over this stable execution topology.  Writers checkpoint one scene at a
    time in parallel episode lanes; one global Reviewer emits only compact
    issues; conditional Rewriters then touch only affected scenes.
    """

    normalized = tuple(_normalize_scene(scene) for scene in scenes)
    if not normalized:
        raise ValueError("screenplay draft workflow requires target scenes")
    if len({scene["id"] for scene in normalized}) != len(normalized):
        raise ValueError("screenplay workflow scene ids must be unique")

    units: list[ScreenplayDraftWorkflowUnit] = []
    writer_ids: list[str] = []
    writer_by_scene: dict[str, str] = {}
    previous_writer_by_episode: dict[int, str] = {}
    for scene in normalized:
        scene_id = str(scene["id"])
        episode = int(scene.get("episodeNumber") or 1)
        unit_id = f"write_{scene_id}"
        previous = previous_writer_by_episode.get(episode)
        units.append(ScreenplayDraftWorkflowUnit(
            id=unit_id,
            kind=ScreenplayDraftUnitKind.SCENE_GENERATION,
            position=len(units),
            depends_on=((previous,) if previous else ()),
            scene_ids=(scene_id,),
            scene_headings=(str(scene.get("heading") or ""),),
            agent_role="screenplay_writer",
            label=f"创作 {str(scene.get('heading') or scene_id)}",
        ))
        writer_ids.append(unit_id)
        writer_by_scene[scene_id] = unit_id
        previous_writer_by_episode[episode] = unit_id

    review_id = "review_draft_continuity"
    all_scene_ids = tuple(str(scene["id"]) for scene in normalized)
    units.append(ScreenplayDraftWorkflowUnit(
        id=review_id,
        kind=ScreenplayDraftUnitKind.CONTINUITY_REVIEW,
        position=len(units),
        depends_on=tuple(writer_ids),
        scene_ids=all_scene_ids,
        scene_headings=tuple(str(scene.get("heading") or "") for scene in normalized),
        agent_role="screenplay_reviewer",
        label="审阅跨集连续性",
    ))

    revision_ids: list[str] = []
    for scene in normalized:
        scene_id = str(scene["id"])
        unit_id = f"revise_{scene_id}"
        units.append(ScreenplayDraftWorkflowUnit(
            id=unit_id,
            kind=ScreenplayDraftUnitKind.SCENE_REVISION,
            position=len(units),
            depends_on=(writer_by_scene[scene_id], review_id),
            scene_ids=(scene_id,),
            scene_headings=(str(scene.get("heading") or ""),),
            agent_role="screenplay_rewriter",
            label=f"按审阅结果修订 {str(scene.get('heading') or scene_id)}",
        ))
        revision_ids.append(unit_id)

    units.append(ScreenplayDraftWorkflowUnit(
        id="propose_draft",
        kind=ScreenplayDraftUnitKind.FINALIZE,
        position=len(units),
        depends_on=tuple(revision_ids),
        label="生成剧本正文提案",
    ))
    return ScreenplayDraftWorkflow(units=tuple(units))


def _normalize_scene(scene: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(scene, Mapping):
        raise TypeError("screenplay workflow scenes must be mappings")
    scene_id = str(scene.get("id") or "").strip()
    if not scene_id:
        raise ValueError("screenplay workflow scene id is required")
    raw_episode = scene.get("episodeNumber")
    episode_number = (
        raw_episode
        if isinstance(raw_episode, int)
        and not isinstance(raw_episode, bool)
        and raw_episode > 0
        else None
    )
    return {
        "id": scene_id,
        "heading": str(scene.get("heading") or ""),
        "episodeNumber": episode_number,
    }


__all__ = [
    "MAX_SCREENPLAY_CHILD_AGENTS",
    "ScreenplayDraftUnitKind",
    "ScreenplayDraftWorkflow",
    "ScreenplayDraftWorkflowUnit",
    "build_screenplay_draft_workflow",
]
