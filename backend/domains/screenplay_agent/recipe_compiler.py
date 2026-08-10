"""Compile validated screenplay intent into a PurrA execution recipe."""

from __future__ import annotations

from dataclasses import dataclass

from purra.contracts import ExecutionRecipe, ExecutionRecipeStep

from domains.screenplay_agent.contracts import ScreenplayIntent


@dataclass(frozen=True, slots=True)
class CompiledScreenplayTask:
    target_role: str
    recipe: ExecutionRecipe


def compile_screenplay_task(
    *,
    intent: ScreenplayIntent,
    target_role: str,
    episode_numbers: tuple[int, ...] = (),
    base_revision_id: str | None = None,
) -> CompiledScreenplayTask:
    """Build a static, checkpointable DAG without product state in PurrA."""

    common = {
        "instruction": intent.instruction,
        "constraints": list(intent.constraints),
        "preserve": list(intent.preserve),
        "baseRevisionId": base_revision_id,
    }
    if target_role == "screenplayDraft" and episode_numbers:
        generation = tuple(
            ExecutionRecipeStep(
                id=f"draft-episode-{number}",
                kind="generate_episode_draft",
                executor="screenplay",
                depends_on=(
                    (f"draft-episode-{episode_numbers[index - 1]}",)
                    if index
                    else ()
                ),
                plan_step_id="create",
                max_attempts=4,
                metadata={
                    "input": {"episodeNumber": number, **common},
                },
            )
            for index, number in enumerate(episode_numbers)
        )
    else:
        generation = (ExecutionRecipeStep(
            id="generate-deliverable",
            kind="generate_deliverable",
            executor="screenplay",
            plan_step_id="create",
            max_attempts=4,
            metadata={
                "input": {"targetRole": target_role, **common},
            },
        ),)
    publish = ExecutionRecipeStep(
        id="publish-candidate",
        kind="publish_candidate_revision",
        executor="screenplay",
        depends_on=tuple(step.id for step in generation),
        plan_step_id="publish",
        metadata={
            "input": {
                "targetRole": target_role,
                "baseRevisionId": base_revision_id,
            },
        },
    )
    return CompiledScreenplayTask(
        target_role=target_role,
        recipe=ExecutionRecipe(
            kind=f"screenplay.{target_role}",
            steps=(*generation, publish),
            max_parallelism=1,
            metadata={"targetRole": target_role},
        ),
    )


__all__ = ["CompiledScreenplayTask", "compile_screenplay_task"]
