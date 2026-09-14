from __future__ import annotations

import pytest

from agents.screenplay.contracts import (
    SCREENPLAY_PART_REGISTRY,
    ScreenplayPartCompletion,
    ScreenplayPartKind,
    ScreenplayPartOperationScope,
    ScreenplayStageCommand,
)
from agents.screenplay.recipe import (
    ScreenplayHostRecipeSpec,
    ScreenplayRecipePart,
    compile_screenplay_replacement_recipe,
)
from agents.screenplay.access_contract import (
    WRITE_SCREENPLAY_CANDIDATE_PART,
    screenplay_part_tool_names,
)
from agents.screenplay.output_contract import (
    ScreenplayPartOutputError,
    ScreenplayPartOutputEvidence,
    validate_screenplay_part_output,
)


def test_stage_command_round_trips_the_existing_public_wire_shape() -> None:
    wire = {
        "kind": "stage_action",
        "action": "revise",
        "targetRole": "screenplayDraft",
        "scope": {"kind": "episodes", "episodeNumbers": [2, 4]},
    }

    command = ScreenplayStageCommand.from_mapping(wire)

    assert command.to_mapping() == wire
    assert command.scope.episode_numbers == (2, 4)


@pytest.mark.parametrize(
    ("wire", "message"),
    (
        (
            {
                "kind": "stage_action",
                "action": "answer",
                "targetRole": "structure",
                "scope": {"kind": "current_stage"},
            },
            "cannot be answer",
        ),
        (
            {
                "kind": "stage_action",
                "action": "review",
                "targetRole": "structure",
                "scope": {"kind": "current_stage"},
            },
            "review stage command requires review target",
        ),
        (
            {
                "kind": "stage_action",
                "action": "create",
                "targetRole": "sceneList",
                "scope": {
                    "kind": "next_episodes",
                    "count": 2,
                    "episodeNumbers": [1],
                },
            },
            "next_episodes stage command cannot list episodes",
        ),
    ),
)
def test_stage_command_preserves_host_validation_semantics(
    wire: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        ScreenplayStageCommand.from_mapping(wire)


def test_part_registry_is_total_and_has_one_definition_per_kind() -> None:
    assert set(SCREENPLAY_PART_REGISTRY) == set(ScreenplayPartKind)
    assert {item.kind for item in SCREENPLAY_PART_REGISTRY.values()} == set(
        ScreenplayPartKind
    )
    assert (
        SCREENPLAY_PART_REGISTRY[ScreenplayPartKind.EPISODE_METADATA].completion
        is ScreenplayPartCompletion.HOST_CAPTURE
    )


def test_operation_scope_uses_attempt_identity_and_explicit_revision_scope() -> None:
    scope = ScreenplayPartOperationScope(
        project_id="project-1",
        task_id="task-1",
        unit_id="episode-1-metadata",
        attempt=2,
        part_kind=ScreenplayPartKind.EPISODE_METADATA,
        part_key="episode:1:metadata",
        target_role="screenplayDraft",
        source_revision_refs=("source-r1",),
        deliverable_revision_scope={"structure": "revision-7"},
        episode_number=1,
    )

    assert scope.operation_scope_id == "task-1:episode-1-metadata:2"
    assert scope.to_mapping()["deliverableRevisionScope"] == {
        "structure": "revision-7"
    }


def test_structure_part_still_declares_an_explicit_empty_revision_scope() -> None:
    scope = ScreenplayPartOperationScope(
        project_id="project-1",
        task_id="task-1",
        unit_id="series-arc",
        attempt=1,
        part_kind="expansion",
        part_key="structure:series-arc",
        target_role="structure",
        source_revision_refs=(),
        deliverable_revision_scope={},
    )

    assert scope.to_mapping()["deliverableRevisionScope"] == {}


def test_draft_scene_requires_one_exact_episode_scene_identity() -> None:
    with pytest.raises(ValueError, match="draft scene identity"):
        ScreenplayPartOperationScope(
            project_id="project-1",
            task_id="task-1",
            unit_id="scene-unit",
            attempt=1,
            part_kind="draft_scene",
            part_key="scene-1",
            target_role="screenplayDraft",
            source_revision_refs=(),
            deliverable_revision_scope={"sceneList": "revision-1"},
        )

    scope = ScreenplayPartOperationScope(
        project_id="project-1",
        task_id="task-1",
        unit_id="scene-unit",
        attempt=1,
        part_kind="draft_scene",
        part_key="scene-1",
        target_role="screenplayDraft",
        source_revision_refs=(),
        deliverable_revision_scope={"sceneList": "revision-1"},
        episode_number=2,
        scene_id="scene-1",
    )

    assert scope.to_mapping()["episodeNumber"] == 2
    assert scope.to_mapping()["sceneId"] == "scene-1"


def test_operation_scope_rejects_duplicate_source_revisions() -> None:
    with pytest.raises(ValueError, match="unique non-empty"):
        ScreenplayPartOperationScope(
            project_id="project-1",
            task_id="task-1",
            unit_id="part-1",
            attempt=1,
            part_kind="evidence",
            part_key="evidence:1",
            target_role="sourceAnalysis",
            source_revision_refs=("source-r1", "source-r1"),
            deliverable_revision_scope={},
        )


def test_recipe_is_generated_from_registry_contracts() -> None:
    recipe = compile_screenplay_replacement_recipe(
        project_id="project-1",
        target_role="screenplayDraft",
        max_parallelism=4,
        parts=(
            ScreenplayRecipePart(
                id="evidence:1",
                kind="evidence",
                semantic_key="episode:1:evidence",
                deliverable_revision_scope={"structure": "revision-7"},
            ),
            ScreenplayRecipePart(
                id="episode:1:metadata",
                kind="episode_metadata",
                semantic_key="episode:1:metadata",
                depends_on=("evidence:1",),
                deliverable_revision_scope={"structure": "revision-7"},
                episode_number=1,
            ),
        ),
    )

    assert recipe.max_parallelism == 2
    assert recipe.steps[0].max_attempts == SCREENPLAY_PART_REGISTRY[
        ScreenplayPartKind.EVIDENCE
    ].max_attempts
    assert recipe.steps[1].metadata["completion"] == "host_capture"
    assert recipe.steps[1].metadata["toolProfile"] == "episode_metadata_read"
    assert recipe.steps[1].metadata["dependencyPartKeys"] == [
        "episode:1:evidence"
    ]
    assert recipe.steps[0].metadata["toolNames"] == [
        "inspectScreenplayProjectV1",
        "listScreenplaySourceItemsV1",
        "readScreenplaySourceItemV1",
        WRITE_SCREENPLAY_CANDIDATE_PART,
    ]
    assert WRITE_SCREENPLAY_CANDIDATE_PART not in recipe.steps[1].metadata[
        "toolNames"
    ]
    assert recipe.metadata["recipeVersion"] == 1
    assert str(recipe.metadata["recipeDigest"]).startswith("sha256:")


def test_recipe_rejects_missing_or_forward_revision_contracts() -> None:
    with pytest.raises(ValueError, match="declare revision scope"):
        ScreenplayRecipePart(
            id="part-1",
            kind="expansion",
            semantic_key="structure:series-arc",
        )


def test_host_recipe_is_closed_and_semantic_part_keys_are_unique() -> None:
    with pytest.raises(ValueError, match="host recipe shape"):
        ScreenplayHostRecipeSpec.from_mapping({
            "recipeVersion": 1,
            "operation": "create",
            "targetRole": "structure",
            "maxParallelism": 1,
            "parts": [],
            "legacyManifest": {},
        })

    duplicate = (
        ScreenplayRecipePart(
            id="part-1",
            kind="document_section",
            semantic_key="section:same",
            deliverable_revision_scope={},
        ),
        ScreenplayRecipePart(
            id="part-2",
            kind="expansion",
            semantic_key="section:same",
            depends_on=("part-1",),
            deliverable_revision_scope={},
        ),
    )
    with pytest.raises(ValueError, match="semantic keys must be unique"):
        compile_screenplay_replacement_recipe(
            project_id="project-1",
            target_role="structure",
            max_parallelism=1,
            parts=duplicate,
        )


def test_tool_manifest_follows_completion_contract() -> None:
    assert WRITE_SCREENPLAY_CANDIDATE_PART in screenplay_part_tool_names(
        "evidence"
    )
    assert WRITE_SCREENPLAY_CANDIDATE_PART not in screenplay_part_tool_names(
        "draft_scene"
    )
    assert screenplay_part_tool_names("validation") == ()


def test_candidate_missing_is_retryable_model_output_not_business_failure() -> None:
    with pytest.raises(ScreenplayPartOutputError) as raised:
        validate_screenplay_part_output(
            "document_section",
            ScreenplayPartOutputEvidence(),
        )

    assert raised.value.code == "screenplay_candidate_missing"
    assert raised.value.retryable is True


def test_host_capture_and_host_only_have_distinct_completion_evidence() -> None:
    captured = validate_screenplay_part_output(
        "episode_metadata",
        ScreenplayPartOutputEvidence(host_capture={"episodeNumber": 1}),
    )
    projected = validate_screenplay_part_output(
        "validation",
        ScreenplayPartOutputEvidence(host_result={"valid": True}),
    )

    assert captured["completion"] == "host_capture"
    assert projected["completion"] == "host_only"


def test_invalid_host_capture_is_retryable_but_mixed_evidence_is_not() -> None:
    with pytest.raises(ScreenplayPartOutputError) as missing:
        validate_screenplay_part_output(
            "draft_scene",
            ScreenplayPartOutputEvidence(),
        )
    assert missing.value.retryable is True

    with pytest.raises(ScreenplayPartOutputError) as conflict:
        validate_screenplay_part_output(
            "draft_scene",
            ScreenplayPartOutputEvidence(
                candidate_artifact_id="artifact-1",
                host_capture={"content": "scene"},
            ),
        )
    assert conflict.value.retryable is False

    with pytest.raises(ValueError, match="point backward"):
        compile_screenplay_replacement_recipe(
            project_id="project-1",
            target_role="structure",
            max_parallelism=1,
            parts=(ScreenplayRecipePart(
                id="part-1",
                kind="expansion",
                semantic_key="structure:series-arc",
                depends_on=("future-part",),
                deliverable_revision_scope={},
            ),),
        )
