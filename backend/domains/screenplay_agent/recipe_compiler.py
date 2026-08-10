"""Compile validated screenplay intent into a bounded PurrA recipe."""

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
    original_request: str | None = None,
) -> CompiledScreenplayTask:
    """Build read, generate, validate and publish checkpoints in the domain."""

    common = {
        "instruction": intent.instruction,
        "constraints": list(intent.constraints),
        "preserve": list(intent.preserve),
        "baseRevisionId": base_revision_id,
    }
    steps: list[ExecutionRecipeStep] = []
    validation_ids: list[str] = []
    previous_validation: str | None = None
    targets = (
        tuple((f"episode-{number}", {"episodeNumber": number})
              for number in episode_numbers)
        if target_role == "screenplayDraft" and episode_numbers
        else (("deliverable", {"targetRole": target_role}),)
    )
    for suffix, target_input in targets:
        evidence_id = f"collect-evidence-{suffix}"
        generate_id = f"generate-candidate-{suffix}"
        validate_id = f"validate-candidate-{suffix}"
        target = {**target_input, **common}
        steps.extend((
            ExecutionRecipeStep(
                id=evidence_id,
                kind="collect_evidence",
                executor="screenplay",
                depends_on=(
                    (previous_validation,) if previous_validation else ()
                ),
                plan_step_id="create",
                metadata={
                    "input": target,
                    **_execution_contract("read_only", "output_ref"),
                },
            ),
            ExecutionRecipeStep(
                id=generate_id,
                kind="generate_candidate",
                executor="screenplay",
                depends_on=(evidence_id,),
                plan_step_id="create",
                max_attempts=4,
                metadata={
                    "input": target,
                    **_execution_contract("idempotent_write", "artifact_ref"),
                },
            ),
            ExecutionRecipeStep(
                id=validate_id,
                kind="validate_candidate",
                executor="screenplay",
                depends_on=(generate_id,),
                plan_step_id="create",
                metadata={
                    "input": target,
                    **_execution_contract("read_only", "validation_receipt"),
                },
            ),
        ))
        validation_ids.append(validate_id)
        previous_validation = validate_id
    steps.append(ExecutionRecipeStep(
        id="compose-final-response",
        kind="compose_final_response",
        executor="screenplay",
        depends_on=tuple(validation_ids),
        plan_step_id="publish",
        max_attempts=2,
        metadata={
            "input": {
                "targetRole": target_role,
                "instruction": intent.instruction,
                "userRequest": str(original_request or intent.instruction),
                "constraints": list(intent.constraints),
                "preserve": list(intent.preserve),
            },
            **_execution_contract("read_only", "output_ref"),
        },
    ))
    steps.append(ExecutionRecipeStep(
        id="publish-candidate",
        kind="publish_candidate_revision",
        executor="screenplay",
        depends_on=(*validation_ids, "compose-final-response"),
        plan_step_id="publish",
        metadata={
            "input": {
                "targetRole": target_role,
                "baseRevisionId": base_revision_id,
            },
            **_execution_contract("idempotent_write", "revision_id"),
        },
    ))
    return CompiledScreenplayTask(
        target_role=target_role,
        recipe=ExecutionRecipe(
            kind=f"screenplay.{target_role}",
            steps=tuple(steps),
            max_parallelism=1,
            metadata={"targetRole": target_role, "recipeVersion": 3},
        ),
    )


def _execution_contract(effect_class: str, evidence: str) -> dict[str, str]:
    return {
        "effectClass": effect_class,
        "completionEvidence": evidence,
        "checkpointPolicy": "reuse_completed",
        "retryPolicy": "bounded_attempts",
    }


__all__ = ["CompiledScreenplayTask", "compile_screenplay_task"]
