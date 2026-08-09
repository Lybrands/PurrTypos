from __future__ import annotations

from agent_core.contracts import (
    AgentMessage,
    AgentRunRequest,
    ModelRequest,
    PlanningCapabilities,
    PlanningConstraints,
    StepExecutor,
    TaskSpec,
)
from domains.screenplay.contracts import ScreenplayDomainContext
from domains.screenplay.planning import ScreenplayPlanningPolicy


def _request(*, stage: str, intent: str = "chat", draft_scope: str = "planner"):
    return AgentRunRequest(
        messages=(AgentMessage(role="user", content="执行当前请求"),),
        model=ModelRequest(provider="fixture", model="model"),
        domain_context=ScreenplayDomainContext(
            project_id="project-1",
            requested_stage=stage,
            task_intent=intent,
            draft_scope=draft_scope,
        ).to_core_context(),
        mode="agent",
    )


def test_stage_deliverable_maps_to_generic_required_capability_constraint():
    capabilities = PlanningCapabilities(
        available_tool_names=frozenset({
            "generateScreenplayStructure",
            "unrelatedTool",
        }),
        host_planning_facts={
            "requestedCompletionCapability": (
                "generateScreenplayStructure"
            ),
        },
    )

    constraints = ScreenplayPlanningPolicy().planning_constraints(
        _request(stage="structure", intent="stage_deliverable"),
        capabilities,
    )

    assert constraints.required_any_tool_names == frozenset({
        "generateScreenplayStructure",
    })
    assert constraints.allow_model_only_fallback is False


def test_orientation_deliverable_uses_the_domain_resolved_capability():
    capabilities = PlanningCapabilities(
        available_tool_names=frozenset({
            "analyzeSourceMaterial",
            "generateCreativeBrief",
        }),
        host_planning_facts={
            "requestedCompletionCapability": "analyzeSourceMaterial",
        },
    )

    constraints = ScreenplayPlanningPolicy().planning_constraints(
        _request(stage="orientation", intent="stage_deliverable"),
        capabilities,
    )

    assert constraints.required_any_tool_names == frozenset({
        "analyzeSourceMaterial",
    })


def test_explicit_draft_command_leaves_only_host_capability_in_visible_plan():
    capabilities = PlanningCapabilities(
        available_tool_names=frozenset({
            "continueScreenplayDraft",
            "getScreenplayProject",
            "getScreenplayDocument",
            "unrelatedTool",
        }),
        available_agent_roles=frozenset({
            "screenplay_writer",
            "screenplay_reviewer",
            "screenplay_dramaturg",
        }),
        max_parallel_agents=3,
        host_planning_facts={
            "requestedSceneCount": 6,
            "currentScenes": [
                {"id": "s05-01", "episodeNumber": 5},
                {"id": "s05-02", "episodeNumber": 5},
                {"id": "s06-01", "episodeNumber": 6},
                {"id": "s06-02", "episodeNumber": 6},
                {"id": "s07-01", "episodeNumber": 7},
                {"id": "s07-02", "episodeNumber": 7},
            ],
        },
    )

    constraints = ScreenplayPlanningPolicy().planning_constraints(
        _request(stage="draft", draft_scope="next_3_episodes"),
        capabilities,
    )

    assert constraints.required_any_tool_names == frozenset({
        "continueScreenplayDraft",
    })
    assert constraints.planning_excluded_tool_names == frozenset({
        "getScreenplayProject",
        "getScreenplayDocument",
        "unrelatedTool",
    })
    assert constraints.required_any_agent_roles == frozenset()
    assert constraints.planning_excluded_agent_roles == frozenset({
        "screenplay_writer",
        "screenplay_reviewer",
        "screenplay_dramaturg",
    })
    assert constraints.minimum_root_agent_count == 0
    assert constraints.agent_assignment_coverages == ()
    assert constraints.planning_excluded_executors == frozenset({
        StepExecutor.AGENT,
    })
    assert constraints.allow_model_only_fallback is False


def test_natural_language_draft_keeps_core_planner_semantic_freedom():
    capabilities = PlanningCapabilities(available_tool_names=frozenset({
        "continueScreenplayDraft",
    }))

    constraints = ScreenplayPlanningPolicy().planning_constraints(
        _request(stage="draft"),
        capabilities,
    )

    assert not constraints.required_any_tool_names
    assert not constraints.planning_excluded_tool_names
    assert constraints.allow_model_only_fallback is True


def test_single_scene_draft_does_not_advertise_unsupported_child_execution():
    capabilities = PlanningCapabilities(
        available_tool_names=frozenset({"continueScreenplayDraft"}),
        available_agent_roles=frozenset({
            "screenplay_writer",
            "screenplay_reviewer",
        }),
        max_parallel_agents=3,
        host_planning_facts={
            "requestedSceneCount": 1,
            "currentScenes": [{"id": "s05-01", "episodeNumber": 5}],
        },
    )

    constraints = ScreenplayPlanningPolicy().planning_constraints(
        _request(stage="draft", draft_scope="next_scene"),
        capabilities,
    )

    assert constraints.planning_excluded_agent_roles == frozenset({
        "screenplay_writer",
        "screenplay_reviewer",
    })
    assert not constraints.required_any_agent_roles
    assert not constraints.agent_assignment_coverages
    assert not constraints.planning_excluded_executors


def test_artifact_continuation_marks_only_durable_private_progress_satisfied():
    capabilities = PlanningCapabilities(
        available_tool_names=frozenset({"generateSceneList"}),
        host_planning_facts={
            "artifactContinuity": {
                "candidates": [{
                    "artifactId": "artifact-1",
                    "workItemId": "work-1",
                    "kind": "scene_list_batches",
                    "expectedItemCount": 12,
                    "committedItemCount": 7,
                }],
            },
        },
        constraints=PlanningConstraints(
            required_any_tool_names=frozenset({"generateSceneList"}),
        ),
    )
    task_spec = TaskSpec(
        goal="继续生成场景表",
        target={
            "artifactContinuity": {
                "action": "continue",
                "artifactId": "artifact-1",
                "workItemId": "work-1",
            },
        },
    )

    constraints = ScreenplayPlanningPolicy().planning_constraints_for_task(
        _request(stage="scenes", intent="stage_deliverable"),
        capabilities,
        task_spec,
    )

    assert constraints.required_any_tool_names == frozenset({
        "generateSceneList",
    })
    assert constraints.execution_satisfied_tool_names == frozenset({
        "beginSceneListArtifact",
    })
    assert not constraints.satisfied_tool_dependency_edges


def test_complete_artifact_batches_resume_at_private_finalizer_only():
    capabilities = PlanningCapabilities(
        available_tool_names=frozenset({"generateSceneList"}),
        host_planning_facts={
            "artifactContinuity": {
                "candidates": [{
                    "artifactId": "artifact-1",
                    "workItemId": "work-1",
                    "kind": "scene_list_batches",
                    "expectedItemCount": 12,
                    "committedItemCount": 12,
                }],
            },
        },
    )

    constraints = ScreenplayPlanningPolicy().planning_constraints_for_task(
        _request(stage="scenes"),
        capabilities,
        TaskSpec(
            goal="完成场景表",
            target={
                "artifactContinuity": {
                    "action": "continue",
                    "artifactId": "artifact-1",
                    "workItemId": "work-1",
                },
            },
        ),
    )

    assert constraints.execution_satisfied_tool_names == frozenset({
        "beginSceneListArtifact",
        "appendSceneListBatch",
    })
