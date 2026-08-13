from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import pytest
import pytest_asyncio

import application.model_runtime as model_runtime
import application.screenplay_structured_call as screenplay_structured_call
import domains.screenplay_agent.contracts as screenplay_contracts
from purra.contracts import (
    AgentMessage,
    AgentRunRequest,
    ContextBudget,
    DomainContext,
    RunCreateParams,
    RunStatus,
    ModelCompletion,
    ModelFinishReason,
    ModelStream,
    ModelStreamChunk,
    ModelRequest,
    ReasoningMode,
    StepExecutor,
    StepType,
    TaskPlan,
    TaskSpec,
    TaskStep,
)
from purra.api import AgentCore
from purra.events import AgentEvent, CoreEventType
from purra.tools import InMemoryToolCatalog
from purra.errors import ModelGatewayError
from purra.json_values import thaw_json_mapping
from purra.recovery import (
    FailureCategory,
    FailureDisposition,
    FailureScope,
    decide_failure,
)
from application.screenplay_agent_service import (
    PlannedScreenplayIntent,
    ResolvedScreenplayTask,
    ScreenplayAgentService,
    _task_failure,
)
from application.screenplay_agent_profile import ScreenplayAgentProfileExtension
from application.composition_factory import create_agent_composition
from application.model_runtime import model_request_from_runtime
from application.screenplay_agent_stream import ScreenplayCanonicalOutputQuery
from application.screenplay_agent_task_executor import (
    ScreenplayTaskModelCalls,
    ScreenplayTaskUnitExecutor,
    _unit_result,
)
from application.screenplay_candidate_assembler import (
    aggregate_review_validations,
)
from application.screenplay_agent_context import ScreenplayAgentContextQuery
from application.screenplay_part_artifacts import (
    ScreenplayPartArtifactQuery,
    ValidatedPartArtifactRef,
)
from application.screenplay_manifest_compiler import (
    REVIEW_DIMENSIONS,
    compile_screenplay_manifest,
)
from application.screenplay_agent_planner import (
    ModelScreenplayIntentPlanner,
    SqliteScreenplayTaskResolver,
)
from application.screenplay_structured_call import (
    PublicModelResult,
    StructuredModelResult,
)
from application.screenplay_tool_calling import ScreenplayCandidateRunResult
from application.screenplay_v2_service import ScreenplayV2ProjectService
from database.connection import DatabaseConnection
from database.screenplay_agent_schema import init_screenplay_agent_schema
from domains.screenplay_agent import (
    ScreenplayIntent,
    ScreenplayIntentAction,
    ScreenplayIntentCommandMismatchError,
    ScreenplayIntentScope,
    ScreenplayStageCommand,
)
from domains.screenplay_agent.agent_context import (
    SCREENPLAY_AGENT_DOMAIN_NAMESPACE,
    ScreenplayAgentDomainContext,
)
from domains.screenplay_agent.contracts import (
    ScreenplayPlanBinding,
    ScreenplayPlanPhase,
    ScreenplayScopeKind,
)
from domains.screenplay_agent.recovery import classify_screenplay_run_failure
from exceptions import AppError
from infrastructure.persistence.sqlite_screenplay_agent_repository import (
    SqliteScreenplayAgentRepository,
    _unit_view,
)
from infrastructure.persistence.sqlite_screenplay_operation_repository import (
    SqliteScreenplayOperationRepository,
)
from infrastructure.persistence.sqlite_long_task_repository import (
    SqliteLongTaskRepository,
)
from infrastructure.persistence.sqlite_work_item_repository import (
    SqliteWorkItemRepository,
)
from infrastructure.persistence.sqlite_run_repository import SqliteRunRepository
from infrastructure.persistence.sqlite_agent_output_repository import (
    SqliteAgentOutputRepository,
)
from infrastructure.persistence.agent_output_publisher import (
    InProcessAgentOutputPublisher,
)
from infrastructure.persistence.run_execution_store import SqliteExecutionLeaseStore
from domains.screenplay_agent.adapter import (
    ScreenplayExecutionStateFactory,
    ScreenplayHostContextProvider,
    ScreenplayToolLoopPolicy,
)
from schemas.screenplay_agent import SubmitScreenplayAgentTurnRequest
from schemas.screenplay_v2 import CreateScreenplayV2ProjectRequest
from purra.task_admission import ExecutionMode
from purra.long_tasks import DurableTaskDescriptor


pytestmark = pytest.mark.asyncio


def _semantic_steps() -> tuple[TaskStep, ...]:
    return (
        TaskStep(
            id="understand-source",
            title="理解原作范围",
            type=StepType.READ,
            executor=StepExecutor.MODEL,
        ),
        TaskStep(
            id="draft-analysis",
            title="形成素材分析",
            type=StepType.WRITE,
            executor=StepExecutor.MODEL,
            depends_on=("understand-source",),
        ),
        TaskStep(
            id="deliver-candidate",
            title="交付候选文档",
            type=StepType.WRITE,
            executor=StepExecutor.MODEL,
            depends_on=("draft-analysis",),
        ),
    )


def _screenplay_task_spec(
    *,
    operation: str = "create",
    deliverable: str | None = "sourceAnalysis",
    screenplay: dict | None = None,
) -> TaskSpec:
    extension = screenplay or {
        "version": 1,
        "scope": {"kind": "current_stage"},
        "stepBindings": [
            {"stepId": "understand-source", "phase": "evidence"},
            {"stepId": "draft-analysis", "phase": "creation"},
            {"stepId": "deliver-candidate", "phase": "delivery"},
        ],
    }
    return TaskSpec(
        goal="分析原作范围",
        target={"screenplay": extension},
        operation=operation,
        instruction="梳理人物、事件与可改编冲突。",
        constraints=("只使用指定原作范围",),
        preserve=("保留人物关系",),
        deliverable=deliverable,
    )


async def test_screenplay_intent_compiles_versioned_task_spec_and_exact_plan_bindings():
    intent = ScreenplayIntent.from_task_spec(
        _screenplay_task_spec(),
        _semantic_steps(),
    )

    assert intent.action is ScreenplayIntentAction.CREATE
    assert intent.instruction == "梳理人物、事件与可改编冲突。"
    assert intent.requested_deliverable == "sourceAnalysis"
    assert intent.scope.kind is ScreenplayScopeKind.CURRENT_STAGE
    assert [(binding.step_id, binding.phase) for binding in intent.plan_bindings] == [
        ("understand-source", ScreenplayPlanPhase.EVIDENCE),
        ("draft-analysis", ScreenplayPlanPhase.CREATION),
        ("deliver-candidate", ScreenplayPlanPhase.DELIVERY),
    ]
    assert "reply" not in intent.to_mapping()
    assert ScreenplayIntent.from_mapping(intent.to_mapping()) == intent


async def test_answer_task_spec_needs_no_reply_and_legacy_reply_is_not_deserialized():
    task_spec = _screenplay_task_spec(operation="answer", deliverable=None)

    intent = ScreenplayIntent.from_task_spec(task_spec, _semantic_steps())
    restored = ScreenplayIntent.from_mapping({
        "action": "answer",
        "instruction": "说明当前阶段",
        "reply": "旧持久化回复不能完成新 Turn",
    })

    assert intent.action is ScreenplayIntentAction.ANSWER
    assert intent.reply is None
    assert restored.reply is None
    assert "reply" not in restored.to_mapping()


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda raw: raw.update({"version": 2}), "version"),
        (lambda raw: raw.update({"version": 1.0}), "version"),
        (lambda raw: raw.update({"version": True}), "version"),
        (lambda raw: raw.update({"version": "1"}), "version"),
        (lambda raw: raw.update({"unknown": True}), "fields"),
        (lambda raw: raw["scope"].update({"kind": 1}), "scope kind"),
        (lambda raw: raw["scope"].update({"kind": True}), "scope kind"),
        (lambda raw: raw["scope"].update({"count": 2}), "scope"),
        (lambda raw: raw["scope"].update({"unknown": True}), "scope fields"),
        (
            lambda raw: raw.update({
                "scope": {"kind": "next_episodes", "count": "2"}
            }),
            "count",
        ),
        (
            lambda raw: raw.update({
                "scope": {"kind": "next_episodes", "count": True}
            }),
            "count",
        ),
        (
            lambda raw: raw.update({
                "scope": {"kind": "episodes", "episodeNumbers": [1, "3"]}
            }),
            "episode numbers",
        ),
        (
            lambda raw: raw["stepBindings"].append(
                {"stepId": "understand-source", "phase": "review"}
            ),
            "exactly once",
        ),
        (lambda raw: raw["stepBindings"].pop(), "plan steps"),
        (
            lambda raw: raw["stepBindings"].append(
                {"stepId": "publish-internal-revision", "phase": "delivery"}
            ),
            "plan steps",
        ),
        (
            lambda raw: raw["stepBindings"][0].update({"phase": "internal"}),
            "phase",
        ),
        (
            lambda raw: raw["stepBindings"][0].update({"stepId": 1}),
            "step id",
        ),
        (
            lambda raw: raw["stepBindings"][0].update({"stepId": True}),
            "step id",
        ),
        (
            lambda raw: raw["stepBindings"][0].update({"phase": 1}),
            "phase",
        ),
        (
            lambda raw: raw["stepBindings"][0].update({"phase": True}),
            "phase",
        ),
        (
            lambda raw: raw["stepBindings"][0].update({"unknown": True}),
            "binding fields",
        ),
    ],
)
async def test_screenplay_task_spec_fails_closed_for_unknown_or_incompatible_contracts(
    mutate,
    message,
):
    raw = {
        "version": 1,
        "scope": {"kind": "current_stage"},
        "stepBindings": [
            {"stepId": "understand-source", "phase": "evidence"},
            {"stepId": "draft-analysis", "phase": "creation"},
            {"stepId": "deliver-candidate", "phase": "delivery"},
        ],
    }
    mutate(raw)

    with pytest.raises(ValueError, match=message):
        ScreenplayIntent.from_task_spec(
            _screenplay_task_spec(screenplay=raw),
            _semantic_steps(),
        )


@pytest.mark.parametrize("step_id", [1, b"understand-source", True])
async def test_screenplay_plan_binding_rejects_non_string_step_ids(step_id):
    with pytest.raises(ValueError, match="step id"):
        ScreenplayPlanBinding.from_mapping({
            "stepId": step_id,
            "phase": "evidence",
        })


@pytest.mark.parametrize("phase", [1, b"evidence", True])
async def test_screenplay_plan_binding_rejects_non_string_phases(phase):
    with pytest.raises(ValueError, match="phase"):
        ScreenplayPlanBinding.from_mapping({
            "stepId": "understand-source",
            "phase": phase,
        })


@pytest.mark.parametrize("kind", [1, b"current_stage", True])
async def test_screenplay_task_scope_rejects_non_string_kinds(kind):
    with pytest.raises(ValueError, match="scope kind"):
        ScreenplayIntentScope.from_task_spec_mapping({"kind": kind})


@pytest.mark.parametrize(
    "task_spec",
    [
        _screenplay_task_spec(operation="create", deliverable=None),
        _screenplay_task_spec(operation="answer", deliverable="sourceAnalysis"),
        TaskSpec(
            goal="分析原作",
            target={},
            operation="create",
            instruction="分析",
            deliverable="sourceAnalysis",
        ),
    ],
)
async def test_screenplay_task_spec_rejects_missing_formal_or_extension_semantics(
    task_spec,
):
    with pytest.raises(ValueError):
        ScreenplayIntent.from_task_spec(task_spec, _semantic_steps())


async def test_task_spec_intent_still_obeys_typed_stage_command():
    command = ScreenplayStageCommand.from_mapping({
        "kind": "stage_action",
        "action": "create",
        "targetRole": "sourceAnalysis",
        "scope": {"kind": "current_stage"},
    })
    intent = ScreenplayIntent.from_task_spec(
        _screenplay_task_spec(),
        _semantic_steps(),
    )

    command.require_compatible(intent)

    with pytest.raises(ScreenplayIntentCommandMismatchError):
        ScreenplayStageCommand.from_mapping({
            "kind": "stage_action",
            "action": "create",
            "targetRole": "creativeBrief",
            "scope": {"kind": "current_stage"},
        }).require_compatible(intent)


@pytest.mark.parametrize("scope", [
    {"kind": "current_stage"},
    {"kind": "next_episodes", "count": 2},
    {"kind": "episodes", "episodeNumbers": [1, 3]},
    {"kind": "all_remaining"},
])
async def test_task_spec_scope_is_compatible_with_the_same_stage_command(scope):
    screenplay = {
        "version": 1,
        "scope": scope,
        "stepBindings": [
            {"stepId": "understand-source", "phase": "evidence"},
            {"stepId": "draft-analysis", "phase": "creation"},
            {"stepId": "deliver-candidate", "phase": "delivery"},
        ],
    }
    intent = ScreenplayIntent.from_task_spec(
        _screenplay_task_spec(screenplay=screenplay),
        _semantic_steps(),
    )
    command = ScreenplayStageCommand.from_mapping({
        "kind": "stage_action",
        "action": "create",
        "targetRole": "sourceAnalysis",
        "scope": scope,
    })

    command.require_compatible(intent)


async def test_screenplay_domain_context_round_trips_root_and_legacy_child_modes():
    stage_command = ScreenplayStageCommand.from_mapping({
        "kind": "stage_action",
        "action": "create",
        "targetRole": "sourceAnalysis",
        "scope": {"kind": "current_stage"},
    })
    root = ScreenplayAgentDomainContext(
        project_id="project-1",
        turn_id="turn-1",
        stage_command=stage_command,
    )
    restored_root = ScreenplayAgentDomainContext.from_core_context(
        root.to_core_context()
    )
    legacy_child = ScreenplayAgentDomainContext.from_core_context(DomainContext(
        namespace=SCREENPLAY_AGENT_DOMAIN_NAMESPACE,
        payload={
            "projectId": "project-1",
            "taskId": "task-1",
            "unitId": "unit-1",
            "targetRole": "sourceAnalysis",
            "expectedPartType": "document",
            "expectedPartKey": "main",
        },
    ))

    assert restored_root.is_root is True
    assert restored_root.is_child is False
    assert restored_root.turn_id == "turn-1"
    assert restored_root.stage_command == stage_command
    assert legacy_child.is_root is False
    assert legacy_child.is_child is True
    assert legacy_child.task_id == "task-1"


@pytest.mark.parametrize(
    "payload",
    [
        {"projectId": "project-1", "turnId": "turn-1", "taskId": "task-1"},
        {"projectId": "project-1", "turnId": "turn-1", "unitId": "unit-1"},
        {
            "projectId": "project-1",
            "taskId": "task-1",
            "unitId": "unit-1",
            "expectedPartKey": "main",
        },
    ],
)
async def test_screenplay_domain_context_rejects_mixed_or_partial_mode_fields(payload):
    with pytest.raises(ValueError, match="root or child"):
        ScreenplayAgentDomainContext.from_core_context(DomainContext(
            namespace=SCREENPLAY_AGENT_DOMAIN_NAMESPACE,
            payload=payload,
        ))


async def test_screenplay_root_context_rejects_non_object_stage_command():
    with pytest.raises(ValueError, match="stage command"):
        ScreenplayAgentDomainContext.from_core_context(DomainContext(
            namespace=SCREENPLAY_AGENT_DOMAIN_NAMESPACE,
            payload={
                "projectId": "project-1",
                "turnId": "turn-1",
                "stageCommand": "create",
            },
        ))


class _CoreComposition:
    def __init__(self, db, gateway) -> None:
        self._gateway = gateway
        self._runs = SqliteRunRepository(db)
        self._outputs = SqliteAgentOutputRepository(
            db,
            run_repository=self._runs,
        )
        self._publisher = InProcessAgentOutputPublisher()
        self._leases = SqliteExecutionLeaseStore(db)

    def create_core_for_request(self, request, api_key):
        del request, api_key
        return AgentCore(
            model_gateway=self._gateway,
            run_repository=self._runs,
            planning_policy=ScreenplayToolLoopPolicy(),
            context_provider=ScreenplayHostContextProvider(),
            execution_state_factory=ScreenplayExecutionStateFactory(),
            tool_catalog=InMemoryToolCatalog(()),
            output_repository=self._outputs,
            output_publisher=self._publisher,
            execution_lease_store=self._leases,
            execution_owner_id=self._runs.owner_id,
            execution_lease_duration_ms=self._runs.lease_duration_ms,
        )

    def bind_run_profile(self, request, options):
        del request
        return options

    def release_core(self, core) -> None:
        del core


def _core_composition(db, gateway):
    return _CoreComposition(db, gateway)


async def _public_text_events(db, run_id: str | None = None):
    where = "AND run_id = ?" if run_id else ""
    return await db.fetch_all(
        "SELECT * FROM ai_agent_run_events WHERE event_id IS NOT NULL "
        "AND visibility = 'public' AND kind = 'provider.content_delta' "
        f"{where} ORDER BY id",
        [run_id] if run_id else [],
    )

async def test_draft_manifest_has_stable_scene_parts_and_digest():
    arguments = dict(
        intent=ScreenplayIntent(
            action=ScreenplayIntentAction.CREATE,
            instruction="创作第 4 集",
            requested_deliverable="screenplayDraft",
        ),
        target_role="screenplayDraft",
        source_revision_refs=("sprev-scenes", "sprev-brief"),
        episode_scene_ids={4: ("ep04_s01", "ep04_s02")},
        original_request="请创作第 4 集，并保留上一集的结尾伏笔。",
        plan_bindings=(
            ScreenplayPlanBinding("understand", ScreenplayPlanPhase.EVIDENCE),
            ScreenplayPlanBinding("draft", ScreenplayPlanPhase.CREATION),
            ScreenplayPlanBinding("deliver", ScreenplayPlanPhase.DELIVERY),
        ),
    )
    compiled = compile_screenplay_manifest(**arguments)
    repeated = compile_screenplay_manifest(**arguments)

    steps = compiled.recipe.steps
    assert [step.id for step in steps] == [
        "evidence:4",
        "draft:4:ep04_s01",
        "draft:4:ep04_s02",
        "episode:4:metadata",
        "episode:4:validation",
        "compose-final-response",
    ]
    assert [step.kind for step in steps] == [
        "collect_evidence",
        "generate_draft_scene",
        "generate_draft_scene",
        "generate_episode_metadata",
        "validate_manifest_part",
        "compose_final_response",
    ]
    assert steps[0].metadata["effectClass"] == "read_only"
    assert steps[1].metadata["effectClass"] == "idempotent_write"
    assert steps[1].depends_on == (steps[0].id,)
    assert steps[2].depends_on == (steps[1].id,)
    assert steps[3].depends_on == (steps[2].id,)
    assert steps[4].depends_on == (steps[3].id,)
    assert steps[5].depends_on == (steps[4].id,)
    assert steps[5].metadata["input"] == {
        "targetRole": "screenplayDraft",
        "instruction": "创作第 4 集",
        "userRequest": "请创作第 4 集，并保留上一集的结尾伏笔。",
        "constraints": [],
        "preserve": [],
        "baseRevisionId": None,
    }
    assert compiled.recipe.metadata["recipeVersion"] == 4
    assert compiled.manifest.digest == repeated.manifest.digest
    assert [part.id for part in compiled.manifest.parts] == [
        part.id for part in repeated.manifest.parts
    ]


async def test_review_manifest_parallelizes_five_bounded_dimensions_per_episode():
    compiled = compile_screenplay_manifest(
        intent=ScreenplayIntent(
            action=ScreenplayIntentAction.REVIEW,
            instruction="审阅完整剧本",
            requested_deliverable="review",
        ),
        target_role="review",
        source_revision_refs=("sprev-draft",),
        episode_scene_ids={1: ("ep01_s01", "ep01_s02")},
        reviewed_draft_id="sprev-draft",
        plan_bindings=(
            ScreenplayPlanBinding("understand", ScreenplayPlanPhase.EVIDENCE),
            ScreenplayPlanBinding("review", ScreenplayPlanPhase.REVIEW),
            ScreenplayPlanBinding("deliver", ScreenplayPlanPhase.DELIVERY),
        ),
    )

    dimension_parts = [
        part for part in compiled.manifest.parts
        if part.kind.value == "review_dimension"
    ]
    assert [part.metadata["reviewDimension"] for part in dimension_parts] == list(
        REVIEW_DIMENSIONS
    )
    assert all(part.dependencies == ("review-input:1",) for part in dimension_parts)
    validation = next(
        part for part in compiled.manifest.parts
        if part.id == "review:1:validation"
    )
    assert validation.dependencies == tuple(part.id for part in dimension_parts)
    assert compiled.recipe.max_parallelism == 5


async def test_stage_command_accepts_only_the_same_action_role_and_scope():
    command = screenplay_contracts.ScreenplayStageCommand.from_mapping({
        "kind": "stage_action",
        "action": "review",
        "targetRole": "review",
        "scope": {"kind": "current_stage"},
    })
    command.require_compatible(ScreenplayIntent(
        action=ScreenplayIntentAction.REVIEW,
        instruction="审阅当前完整剧本",
        requested_deliverable="review",
    ))

    with pytest.raises(
        screenplay_contracts.ScreenplayIntentCommandMismatchError,
        match="stage command",
    ):
        command.require_compatible(ScreenplayIntent(
            action=ScreenplayIntentAction.ANSWER,
            instruction="说明没有 JSON",
            reply="没有 JSON",
        ))


@pytest.mark.parametrize("intent", [
    ScreenplayIntent(
        action=ScreenplayIntentAction.REVISE,
        instruction="修订三集",
        scope=ScreenplayIntentScope(
            kind=ScreenplayScopeKind.NEXT_EPISODES,
            count=3,
        ),
        requested_deliverable="screenplayDraft",
    ),
    ScreenplayIntent(
        action=ScreenplayIntentAction.CREATE,
        instruction="生成简报",
        scope=ScreenplayIntentScope(
            kind=ScreenplayScopeKind.NEXT_EPISODES,
            count=3,
        ),
        requested_deliverable="creativeBrief",
    ),
    ScreenplayIntent(
        action=ScreenplayIntentAction.CREATE,
        instruction="创作两集",
        scope=ScreenplayIntentScope(
            kind=ScreenplayScopeKind.NEXT_EPISODES,
            count=2,
        ),
        requested_deliverable="screenplayDraft",
    ),
    ScreenplayIntent(
        action=ScreenplayIntentAction.CREATE,
        instruction="创作第 1、3 集",
        scope=ScreenplayIntentScope(
            kind=ScreenplayScopeKind.EPISODES,
            episode_numbers=(1, 3),
        ),
        requested_deliverable="screenplayDraft",
    ),
])
async def test_stage_command_rejects_each_changed_business_boundary(intent):
    command = screenplay_contracts.ScreenplayStageCommand.from_mapping({
        "kind": "stage_action",
        "action": "create",
        "targetRole": "screenplayDraft",
        "scope": {"kind": "next_episodes", "count": 3},
    })

    with pytest.raises(
        screenplay_contracts.ScreenplayIntentCommandMismatchError,
    ):
        command.require_compatible(intent)


@pytest.mark.parametrize("value", [
    {
        "kind": "stage_action",
        "action": "answer",
        "targetRole": "screenplayDraft",
        "scope": {"kind": "current_stage"},
    },
    {
        "kind": "stage_action",
        "action": "review",
        "targetRole": "screenplayDraft",
        "scope": {"kind": "current_stage"},
    },
    {
        "kind": "stage_action",
        "action": "create",
        "targetRole": "review",
        "scope": {"kind": "current_stage"},
    },
    {
        "kind": "stage_action",
        "action": "create",
        "targetRole": "screenplayDraft",
        "scope": {"kind": "current_stage", "count": 2},
    },
    {
        "kind": "stage_action",
        "action": "create",
        "targetRole": "screenplayDraft",
        "scope": {"kind": "episodes", "episodeNumbers": [1, 1]},
    },
])
async def test_stage_command_rejects_invalid_action_role_and_scope(value):
    with pytest.raises(ValueError):
        screenplay_contracts.ScreenplayStageCommand.from_mapping(value)


@pytest_asyncio.fixture
async def temp_db(tmp_path: Path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    try:
        yield db
    finally:
        await db.close()


async def _project_and_session(db):
    projects = ScreenplayV2ProjectService(db)
    workspace = await projects.create_project(
        command_id="create-rewritten-agent-project",
        request=CreateScreenplayV2ProjectRequest.model_validate({
            "title": "Rewritten Agent",
            "format": "series",
            "source": {"type": "original"},
            "brief": {"approach": "人物驱动", "premise": "意外重逢"},
        }),
    )
    session = await projects.ensure_current_session(workspace["project"]["id"])
    return projects, workspace, session


def _admission_plan(
    *,
    operation: str,
    deliverable: str | None,
    phases: tuple[str, ...],
) -> TaskPlan:
    steps = tuple(
        TaskStep(
            id=f"semantic-{index}",
            title=f"语义步骤 {index}",
            type=(StepType.READ if phase == "evidence" else StepType.WRITE),
            executor=StepExecutor.MODEL,
            depends_on=((f"semantic-{index - 1}",) if index > 1 else ()),
        )
        for index, phase in enumerate(phases, start=1)
    )
    return TaskPlan(
        title="完成本轮剧本任务",
        task_spec=TaskSpec(
            goal="根据本轮要求完成剧本任务",
            operation=operation,
            instruction="根据本轮要求完成剧本任务",
            deliverable=deliverable,
            target={"screenplay": {
                "version": 1,
                "scope": {"kind": "current_stage"},
                "stepBindings": [
                    {"stepId": step.id, "phase": phase}
                    for step, phase in zip(steps, phases, strict=True)
                ],
            }},
        ),
        steps=steps,
    )


def _root_request(
    *,
    project_id: str,
    turn_id: str,
    session_id: int,
    content: str,
) -> AgentRunRequest:
    return AgentRunRequest(
        messages=(AgentMessage(role="user", content=content),),
        model=ModelRequest(provider="fixture", model="model"),
        domain_context=ScreenplayAgentDomainContext(
            project_id=project_id,
            turn_id=turn_id,
        ).to_core_context(),
        session_id=session_id,
        mode="agent",
    )


class _AdmissionResolver:
    async def resolve(self, *, workspace, intent):
        del workspace
        if intent.action is ScreenplayIntentAction.REVIEW:
            return ResolvedScreenplayTask(
                target_role="review",
                episode_numbers=(1,),
                episode_scene_ids={1: ("scene-1",)},
                reviewed_draft_id="sprev-draft",
            )
        return ResolvedScreenplayTask(
            target_role=str(intent.requested_deliverable),
            document_sections=("summary",),
        )


async def test_screenplay_profile_admits_answer_inline_without_operation(
    temp_db: DatabaseConnection,
):
    _projects, workspace, session = await _project_and_session(temp_db)
    repository = SqliteScreenplayAgentRepository(
        temp_db,
        owner_id="screenplay-admission-test",
    )
    turn = await repository.begin_turn(
        command_id="answer-inline",
        project_id=workspace["project"]["id"],
        session_id=session["id"],
        content="当前项目进行到哪一步？",
        stage_command=None,
        runtime_profile={},
    )
    profile = ScreenplayAgentProfileExtension(
        temp_db,
        owner_id="screenplay-admission-test",
        resolver=_AdmissionResolver(),
    )
    request = await profile.prepare_request(_root_request(
        project_id=workspace["project"]["id"],
        turn_id=turn["id"],
        session_id=session["id"],
        content="当前项目进行到哪一步？",
    ))

    decision = await profile.task_admission().evaluate(
        request,
        _admission_plan(
            operation="answer",
            deliverable=None,
            phases=("evidence", "delivery"),
        ),
    )

    assert decision.mode is ExecutionMode.INLINE
    assert decision.execution_recipe is None
    assert await temp_db.fetch_one(
        "SELECT COUNT(*) AS count FROM screenplay_agent_operations"
    ) == {"count": 0}


async def test_product_composition_installs_the_screenplay_profile_extension(
    temp_db: DatabaseConnection,
):
    composition = create_agent_composition(temp_db)
    try:
        assert isinstance(
            composition.profile_extension("screenplay"),
            ScreenplayAgentProfileExtension,
        )
    finally:
        await composition.shutdown()


async def test_screenplay_profile_rejects_a_root_request_outside_its_persisted_turn(
    temp_db: DatabaseConnection,
):
    _projects, workspace, session = await _project_and_session(temp_db)
    repository = SqliteScreenplayAgentRepository(
        temp_db,
        owner_id="screenplay-trusted-turn-test",
    )
    turn = await repository.begin_turn(
        command_id="trusted-turn",
        project_id=workspace["project"]["id"],
        session_id=session["id"],
        content="持久化的用户请求",
        stage_command=None,
        runtime_profile={},
    )
    profile = ScreenplayAgentProfileExtension(
        temp_db,
        owner_id="screenplay-trusted-turn-test",
    )

    with pytest.raises(ValueError, match="scope does not match"):
        await profile.prepare_request(_root_request(
            project_id=workspace["project"]["id"],
            turn_id=turn["id"],
            session_id=session["id"],
            content="伪造的另一条用户请求",
        ))


@pytest.mark.parametrize(
    ("operation", "deliverable", "phases"),
    (
        ("create", "sourceAnalysis", ("evidence", "creation", "delivery")),
        ("revise", "sourceAnalysis", ("evidence", "creation", "delivery")),
        ("review", "review", ("evidence", "review", "delivery")),
    ),
)
async def test_screenplay_profile_admits_formal_plan_as_one_operation_and_private_recipe(
    temp_db: DatabaseConnection,
    operation: str,
    deliverable: str,
    phases: tuple[str, ...],
):
    _projects, workspace, session = await _project_and_session(temp_db)
    project_id = workspace["project"]["id"]
    stage_command = {
        "kind": "stage_action",
        "action": operation,
        "targetRole": deliverable,
        "scope": {"kind": "current_stage"},
    }
    repository = SqliteScreenplayAgentRepository(
        temp_db,
        owner_id="screenplay-admission-test",
    )
    turn = await repository.begin_turn(
        command_id=f"formal-{operation}",
        project_id=project_id,
        session_id=session["id"],
        content="完成正式任务",
        stage_command=stage_command,
        runtime_profile={},
    )
    profile = ScreenplayAgentProfileExtension(
        temp_db,
        owner_id="screenplay-admission-test",
        resolver=_AdmissionResolver(),
    )
    request = await profile.prepare_request(_root_request(
        project_id=project_id,
        turn_id=turn["id"],
        session_id=session["id"],
        content="完成正式任务",
    ))
    hydrated = ScreenplayAgentDomainContext.from_core_context(
        request.domain_context
    )
    plan = _admission_plan(
        operation=operation,
        deliverable=deliverable,
        phases=phases,
    )

    first = await profile.task_admission().evaluate(request, plan)
    replay = await profile.task_admission().evaluate(request, plan)

    assert hydrated.stage_command == ScreenplayStageCommand.from_mapping(
        stage_command
    )
    assert first.mode is ExecutionMode.DURABLE
    assert replay.mode is ExecutionMode.DURABLE
    assert first.covered_step_ids == tuple(step.id for step in plan.steps)
    assert first.metadata["operationId"] == replay.metadata["operationId"]
    assert await temp_db.fetch_one(
        "SELECT COUNT(*) AS count FROM screenplay_agent_operations"
    ) == {"count": 1}
    recipe = first.execution_recipe
    assert recipe is not None
    assert {step.plan_step_id for step in recipe.steps} == set(
        first.covered_step_ids
    )
    assert all(step.plan_step_id for step in recipe.steps)
    serialized_metadata = json.dumps(
        thaw_json_mapping(recipe.metadata),
        ensure_ascii=False,
    )
    assert "planBindingDigest" in recipe.metadata
    assert all(step.title not in serialized_metadata for step in plan.steps)


class _AdmissionUnitExecutor:
    async def execute(self, context, signal=None):
        raise AssertionError("dispatch must not execute recipe units")


async def _durable_screenplay_admission(
    db: DatabaseConnection,
    *,
    owner_id: str,
    command_id: str,
):
    _projects, workspace, session = await _project_and_session(db)
    project_id = workspace["project"]["id"]
    repository = SqliteScreenplayAgentRepository(db, owner_id=owner_id)
    turn = await repository.begin_turn(
        command_id=command_id,
        project_id=project_id,
        session_id=session["id"],
        content="生成原作分析",
        stage_command={
            "kind": "stage_action",
            "action": "create",
            "targetRole": "sourceAnalysis",
            "scope": {"kind": "current_stage"},
        },
        runtime_profile={},
    )
    profile = ScreenplayAgentProfileExtension(
        db,
        owner_id=owner_id,
        resolver=_AdmissionResolver(),
    )
    request = await profile.prepare_request(_root_request(
        project_id=project_id,
        turn_id=turn["id"],
        session_id=session["id"],
        content="生成原作分析",
    ))
    plan = _admission_plan(
        operation="create",
        deliverable="sourceAnalysis",
        phases=("evidence", "creation", "delivery"),
    )
    decision = await profile.evaluate(request, plan)
    return profile, request, plan, decision


def _admission_dispatcher(db, profile):
    return profile.create_long_task_dispatcher(
        work_item_repository=SqliteWorkItemRepository(db),
        long_task_repository=SqliteLongTaskRepository(db),
        executor=_AdmissionUnitExecutor(),
    )


async def test_screenplay_dispatch_rolls_back_task_identity_when_operation_attach_fails(
    temp_db: DatabaseConnection,
    monkeypatch: pytest.MonkeyPatch,
):
    profile, request, plan, decision = await _durable_screenplay_admission(
        temp_db,
        owner_id="screenplay-atomic-dispatch-test",
        command_id="atomic-dispatch",
    )
    original_attach = profile._operations.attach_long_task

    async def fail_attach(*_args, **_kwargs):
        raise RuntimeError("injected operation attach failure")

    monkeypatch.setattr(profile._operations, "attach_long_task", fail_attach)
    dispatcher = _admission_dispatcher(temp_db, profile)
    with pytest.raises(RuntimeError, match="injected operation attach failure"):
        await dispatcher.dispatch(
            request,
            plan,
            decision,
            parent_run_id="run-atomic-dispatch",
        )

    assert await temp_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_long_tasks"
    ) == {"count": 0}
    assert await temp_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_work_items"
    ) == {"count": 0}
    assert await temp_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_work_item_runs"
    ) == {"count": 0}
    assert await temp_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_long_task_units"
    ) == {"count": 0}
    operation = await profile._operations.load(decision.metadata["operationId"])
    assert operation is not None
    assert operation.status.value == "queued"
    assert operation.long_task_id is None

    monkeypatch.setattr(profile._operations, "attach_long_task", original_attach)
    first = await dispatcher.dispatch(
        request,
        plan,
        decision,
        parent_run_id="run-atomic-dispatch",
    )
    replay = await dispatcher.dispatch(
        request,
        plan,
        decision,
        parent_run_id="run-atomic-dispatch",
    )
    assert replay.task_id == first.task_id
    assert await temp_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_long_tasks"
    ) == {"count": 1}
    assert await temp_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_work_items"
    ) == {"count": 1}
    assert await temp_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_work_item_runs"
    ) == {"count": 1}
    assert await temp_db.fetch_one(
        "SELECT COUNT(*) AS count FROM screenplay_agent_operation_commands "
        "WHERE command_type = 'attachLongTask'"
    ) == {"count": 1}
    operation = await profile._operations.load(decision.metadata["operationId"])
    assert operation is not None
    assert operation.status.value == "running"
    assert operation.long_task_id == first.task_id


async def test_screenplay_dispatch_links_a_new_parent_and_resumes_a_paused_task(
    temp_db: DatabaseConnection,
):
    profile, request, plan, decision = await _durable_screenplay_admission(
        temp_db,
        owner_id="screenplay-paused-resume-test",
        command_id="paused-resume",
    )
    long_tasks = SqliteLongTaskRepository(temp_db)
    dispatcher = _admission_dispatcher(temp_db, profile)
    first = await dispatcher.dispatch(
        request,
        plan,
        decision,
        parent_run_id="run-paused-origin",
    )
    await long_tasks.pause(first.task_id)

    resumed = await dispatcher.dispatch(
        request,
        plan,
        decision,
        parent_run_id="run-paused-continuation",
    )

    assert resumed.task_id == first.task_id
    assert resumed.metadata["resumed"] is True
    assert resumed.metadata["status"] == "running"
    assert await temp_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_work_item_runs "
        "WHERE run_id = 'run-paused-continuation'"
    ) == {"count": 1}


class _FailedResumeDescriptorResolver:
    async def resolve(self, request, plan, decision):
        del plan
        return DurableTaskDescriptor(
            namespace=request.domain_context.namespace,
            owner_id=str(decision.metadata["projectId"]),
            idempotency_key=str(decision.metadata["commandId"]),
            failed_resume_attempts=1,
        )


async def test_screenplay_dispatch_retries_a_failed_task_for_a_new_parent(
    temp_db: DatabaseConnection,
):
    profile, request, plan, decision = await _durable_screenplay_admission(
        temp_db,
        owner_id="screenplay-failed-resume-test",
        command_id="failed-resume",
    )
    dispatcher = _admission_dispatcher(temp_db, profile)
    dispatcher._descriptors = _FailedResumeDescriptorResolver()
    first = await dispatcher.dispatch(
        request,
        plan,
        decision,
        parent_run_id="run-failed-origin",
    )
    async with temp_db.transaction(cancellation_linearizable=True):
        await temp_db.execute(
            "UPDATE ai_agent_long_tasks SET status = 'failed', failed_units = 1 "
            "WHERE id = ?",
            [first.task_id],
        )
        await temp_db.execute(
            "UPDATE ai_agent_long_task_units SET status = 'failed' "
            "WHERE task_id = ? AND position = 0",
            [first.task_id],
        )

    resumed = await dispatcher.dispatch(
        request,
        plan,
        decision,
        parent_run_id="run-failed-retry",
    )

    assert resumed.task_id == first.task_id
    assert resumed.metadata["resumed"] is True
    assert resumed.metadata["status"] == "running"
    assert await temp_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_work_item_runs "
        "WHERE run_id = 'run-failed-retry'"
    ) == {"count": 1}


async def test_screenplay_dispatch_preserves_create_error_when_cleanup_cancels_work_item(
    temp_db: DatabaseConnection,
    monkeypatch: pytest.MonkeyPatch,
):
    profile, request, plan, decision = await _durable_screenplay_admission(
        temp_db,
        owner_id="screenplay-create-cleanup-test",
        command_id="create-cleanup",
    )
    long_tasks = SqliteLongTaskRepository(temp_db)

    async def fail_create(*_args, **_kwargs):
        raise RuntimeError("injected primary long task create failure")

    monkeypatch.setattr(long_tasks, "create", fail_create)
    dispatcher = profile.create_long_task_dispatcher(
        work_item_repository=SqliteWorkItemRepository(temp_db),
        long_task_repository=long_tasks,
        executor=_AdmissionUnitExecutor(),
    )

    with pytest.raises(
        RuntimeError,
        match="injected primary long task create failure",
    ):
        await dispatcher.dispatch(
            request,
            plan,
            decision,
            parent_run_id="run-create-cleanup",
        )

    assert await temp_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_work_items"
    ) == {"count": 0}


async def test_turn_start_does_not_emit_a_host_authored_plan(
    temp_db: DatabaseConnection,
):
    projects, workspace, session = await _project_and_session(temp_db)
    service = ScreenplayAgentService(
        temp_db,
        owner_id="no-canned-plan-test",
        planner=object(),  # type: ignore[arg-type]
        resolver=object(),  # type: ignore[arg-type]
        unit_executor_factory=lambda _runtime: object(),
        projects=projects,
    )
    request = _request(session["id"], "继续完成第七集。")

    turn = await service.submit_turn(
        command_id="no-canned-plan",
        project_id=workspace["project"]["id"],
        request=request,
    )

    assert await _public_text_events(temp_db) == []


async def test_service_persists_the_validated_stage_command_with_the_turn(
    temp_db: DatabaseConnection,
):
    projects, workspace, session = await _project_and_session(temp_db)
    service = ScreenplayAgentService(
        temp_db,
        owner_id="stage-command-service-test",
        planner=object(),  # type: ignore[arg-type]
        resolver=object(),  # type: ignore[arg-type]
        unit_executor_factory=lambda _runtime: object(),
        projects=projects,
    )
    payload = _request(session["id"], "开始审阅").model_dump(mode="json")
    payload["stageCommand"] = {
        "kind": "stage_action",
        "action": "review",
        "targetRole": "review",
        "scope": {"kind": "current_stage"},
    }
    request = SubmitScreenplayAgentTurnRequest.model_validate(payload)

    turn = await service.submit_turn(
        command_id="stage-command-service",
        project_id=workspace["project"]["id"],
        request=request,
    )

    assert turn["stageCommand"] == payload["stageCommand"]


async def test_planner_structured_fields_never_become_public_text(
    temp_db: DatabaseConnection,
):
    projects, workspace, session = await _project_and_session(temp_db)
    planner_output = json.dumps({
        "debugNote": "确认当前阶段后直接回答，不创建交付物。",
        "action": "answer",
        "instruction": "说明当前阶段",
        "scope": {"kind": "current_stage"},
        "constraints": [],
        "preserve": [],
        "requestedDeliverable": None,
        "reply": "当前处于创作简报阶段。",
    }, ensure_ascii=False)

    class PlannerGateway(_ModelGateway):
        async def stream(self, messages, invocation, signal=None):
            del messages, signal
            self.invocations.append(invocation)
            response = (
                planner_output
                if len(self.invocations) == 1
                else "当前处于创作简报阶段。"
            )

            async def chunks():
                yield ModelStreamChunk(content_delta=response)
                yield ModelStreamChunk(finish_reason=ModelFinishReason.STOP)

            return ModelStream(chunks=chunks(), model=invocation.request.model)

    gateway = PlannerGateway("secret")
    planner = ModelScreenplayIntentPlanner(
        temp_db,
        composition=_core_composition(temp_db, gateway),
    )
    service = ScreenplayAgentService(
        temp_db,
        owner_id="model-summary-ownership-test",
        planner=planner,
        resolver=SqliteScreenplayTaskResolver(temp_db),
        unit_executor_factory=lambda _runtime: object(),
        projects=projects,
    )
    request = _request(session["id"], "现在处于哪个阶段？")
    turn = await service.submit_turn(
        command_id="model-summary-ownership",
        project_id=workspace["project"]["id"],
        request=request,
    )

    await service.execute_turn(turn["id"], request.runtime)

    visible = "".join(
        json.loads(str(row["payload_json"]))["delta"]
        for row in await _public_text_events(temp_db)
    )
    assert visible == "当前处于创作简报阶段。"
    assert "debugNote" not in visible


async def test_planner_without_private_note_adds_no_fallback_copy(
    temp_db: DatabaseConnection,
):
    projects, workspace, session = await _project_and_session(temp_db)
    planner_output = json.dumps({
        "action": "answer",
        "instruction": "说明当前阶段",
        "scope": {"kind": "current_stage"},
        "constraints": [],
        "preserve": [],
        "requestedDeliverable": None,
        "reply": "当前处于创作简报阶段。",
    }, ensure_ascii=False)

    class PlannerGateway(_ModelGateway):
        async def stream(self, messages, invocation, signal=None):
            del messages, signal
            self.invocations.append(invocation)
            response = (
                planner_output
                if len(self.invocations) == 1
                else "当前处于创作简报阶段。"
            )

            async def chunks():
                yield ModelStreamChunk(content_delta=response)
                yield ModelStreamChunk(finish_reason=ModelFinishReason.STOP)

            return ModelStream(chunks=chunks(), model=invocation.request.model)

    gateway = PlannerGateway("secret")
    service = ScreenplayAgentService(
        temp_db,
        owner_id="optional-model-summary-test",
        planner=ModelScreenplayIntentPlanner(
            temp_db,
            composition=_core_composition(temp_db, gateway),
        ),
        resolver=SqliteScreenplayTaskResolver(temp_db),
        unit_executor_factory=lambda _runtime: object(),
        projects=projects,
    )
    request = _request(session["id"], "现在处于哪个阶段？")
    turn = await service.submit_turn(
        command_id="optional-model-summary",
        project_id=workspace["project"]["id"],
        request=request,
    )

    await service.execute_turn(turn["id"], request.runtime)

    snapshot = await service.get_snapshot(
        project_id=workspace["project"]["id"],
        session_id=session["id"],
    )
    visible = "".join(
        json.loads(str(row["payload_json"]))["delta"]
        for row in await _public_text_events(temp_db)
    )
    assert visible == "当前处于创作简报阶段。"
    assert snapshot["turns"][0]["assistantContent"] == "当前处于创作简报阶段。"
    assert len(gateway.invocations) == 2


async def test_final_response_unit_metadata_does_not_duplicate_the_response():
    result = _unit_result(
        ValidatedPartArtifactRef(
            artifact_id="artifact-final-response",
            run_id="screenplay-host:task-1:compose-final-response",
            semantic_key="compose-final-response",
            content_digest="sha256:final-response",
            validation_receipt={"valid": True},
        ),
        {"finalResponse": "任务已经完成。"},
    )

    assert result.metadata == {}
    assert result.output_ref == (
        "screenplay-part-artifact://artifact-final-response"
    )


async def test_host_part_output_is_a_finalized_artifact_without_shadow_json(
    temp_db: DatabaseConnection,
):
    parts = ScreenplayPartArtifactQuery(temp_db)
    ref = await parts.write_host_part(
        project_id="project-artifact-only",
        task_id="task-artifact-only",
        unit_id="evidence:4",
        semantic_key="evidence:4",
        part_kind="evidence",
        output={
            "evidenceDescriptor": {
                "sourceRevisionRefs": ["sprev-brief", "sprev-scenes"],
            },
            "evidenceReceipt": "receipt-4",
        },
    )

    assert ref.output_ref.startswith("screenplay-part-artifact://")
    loaded = await parts.require(ref)
    assert loaded["evidenceDescriptor"]["sourceRevisionRefs"] == [
        "sprev-brief",
        "sprev-scenes",
    ]
    artifact = await temp_db.fetch_one(
        "SELECT status FROM ai_agent_artifacts WHERE id = ?",
        [ref.artifact_id],
    )
    assert artifact == {"status": "finalized"}
    assert await temp_db.fetch_one(
        "SELECT name FROM sqlite_master WHERE type = 'table' "
        "AND name = 'screenplay_agent_task_outputs'"
    ) is None


async def test_final_response_composition_receives_only_public_candidate_facts(
    temp_db: DatabaseConnection,
):
    class CapturingModels:
        def __init__(self) -> None:
            self.calls = []

        async def run_public_text(self, **kwargs):
            self.calls.append(kwargs)
            return PublicModelResult(
                (
                    "第 4 至 5 集候选稿已经完成，并保留了上一集的结尾伏笔。"
                    "可以在候选稿区域查看并继续编辑。"
                ),
                "run-final-response",
            )

    models = CapturingModels()
    executor = ScreenplayTaskModelCalls(
        temp_db,
        composition=object(),
    )
    executor._models = models  # type: ignore[assignment]
    task = {
        "id": "task-final-response-facts",
        "projectId": "project-final-response-facts",
        "sessionId": 7,
        "turnId": "turn-final-response-facts",
        "targetRole": "screenplayDraft",
        "units": [
            {
                "id": "validate-candidate-episode-4",
                "kind": "validate_manifest_part",
                "status": "completed",
                "output": {
                    "executionSummary": "承接上一集选择并完成本集转折。",
                    "episodeDraft": {
                        "episodeNumber": 4,
                        "title": "重逢",
                        "sceneIds": ["scene-4-a", "scene-4-b"],
                        "contentText": "绝不能进入最终回答上下文的第四集正文",
                        "sceneTexts": [{
                            "sceneId": "scene-4-a",
                            "contentText": "绝不能进入最终回答上下文的场景正文",
                        }],
                    },
                    "validationReceipt": "receipt-4",
                    "runId": "run-episode-4",
                },
            },
            {
                "id": "validate-candidate-episode-5",
                "kind": "validate_manifest_part",
                "status": "completed",
                "output": {
                    "executionSummary": "推进新冲突并留下后续问题。",
                    "episodeDraft": {
                        "episodeNumber": 5,
                        "title": "追问",
                        "sceneIds": ["scene-5-a"],
                        "contentText": "绝不能进入最终回答上下文的第五集正文",
                    },
                    "validationReceipt": "receipt-5",
                    "runId": "run-episode-5",
                },
            },
        ],
    }
    unit = {
        "id": "compose-final-response",
        "kind": "compose_final_response",
        "input": {
            "targetRole": "screenplayDraft",
            "instruction": "完成第 4 至 5 集",
            "userRequest": "请把第 4 至 5 集写完。",
            "constraints": ["每集结尾留下问题"],
            "preserve": ["保留上一集结尾伏笔"],
        },
    }

    result = await executor.execute(
        task=task,
        unit=unit,
        runtime=object(),
    )

    assert result == {
        "finalResponse": (
            "第 4 至 5 集候选稿已经完成，并保留了上一集的结尾伏笔。"
            "可以在候选稿区域查看并继续编辑。"
        ),
        "runId": "run-final-response",
    }
    assert len(models.calls) == 1
    payload = models.calls[0]["user_payload"]
    assert payload == {
        "request": "请把第 4 至 5 集写完。",
        "instruction": "完成第 4 至 5 集",
        "target": {"role": "screenplayDraft", "label": "剧本正文"},
        "constraints": ["每集结尾留下问题"],
        "preserve": ["保留上一集结尾伏笔"],
        "candidates": [
                {
                    "episodeNumber": 4,
                    "title": "重逢",
                    "sceneCount": 2,
                },
            {
                    "episodeNumber": 5,
                    "title": "追问",
                    "sceneCount": 1,
                },
        ],
    }
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "contentText" not in serialized
    assert "sceneTexts" not in serialized
    assert "validationReceipt" not in serialized
    assert "run-episode" not in serialized
    assert models.calls[0].get("execution_progress_fields") is None


async def test_planning_context_contains_state_not_artifact_bodies(
    temp_db: DatabaseConnection,
):
    _, workspace, _ = await _project_and_session(temp_db)
    workspace["project"]["source"] = {
        "type": "book",
        "bookId": "book-planning-scope",
        "bookTitle": "只用于规划范围的原作",
        "scope": {"mode": "firstChapters", "count": 10},
    }
    workspace["candidates"] = [{
        "id": "sprev-large-candidate",
        "role": "screenplayDraft",
        "revisionNo": 2,
        "summary": {
            "title": "第 1–2 集剧本",
            "textLength": 8_000,
            "fieldCount": 2,
            "partCount": 3,
            "role": "review",
            "proposalKind": "scene_draft",
            "derivedFromIds": ["sprev-private-derived"],
            "inputRevisionIds": ["sprev-private-input"],
            "receipts": [{"id": "receipt-private"}],
            "partRefs": ["part-private"],
            "digest": "sha256:private",
            "body": "候选正文" * 10_000,
            "content": "CANDIDATE_SUMMARY_CONTENT_MUST_NOT_LEAK",
            "unknownNested": {"id": "nested-private"},
        },
    }, {
        "id": "sprev-invalid-summary-scalars",
        "role": "review",
        "summary": {
            "title": 123,
            "textLength": True,
            "fieldCount": -1,
            "partCount": 1.0,
        },
    }, {
        "id": "sprev-clipped-summary-title",
        "role": "creativeBrief",
        "summary": {
            "title": "长" * 500,
            "textLength": 1,
            "fieldCount": 0,
            "partCount": 0,
        },
    }]

    context = await ScreenplayAgentContextQuery(temp_db).planning_context(workspace)
    serialized = json.dumps(context, ensure_ascii=False)

    assert len(serialized) < 12_000
    assert "contentText" not in serialized
    assert '"content"' not in serialized
    assert context["project"]["title"] == "Rewritten Agent"
    assert context["project"]["source"]["scope"] == {
        "mode": "firstChapters",
        "count": 10,
    }
    assert "creativeBrief" in context["availableDeliverables"]
    assert "revisionId" not in serialized
    assert "parentRevisionId" not in serialized
    assert context["candidateDeliverables"][0] == {
        "role": "screenplayDraft",
        "status": "candidate",
        "summary": {
            "title": "第 1–2 集剧本",
            "textLength": 8_000,
            "fieldCount": 2,
            "partCount": 3,
        },
    }
    for forbidden in (
        "sprev-private-derived",
        "sprev-private-input",
        "receipt-private",
        "part-private",
        "sha256:private",
        "CANDIDATE_SUMMARY_CONTENT_MUST_NOT_LEAK",
        "nested-private",
        "scene_draft",
    ):
        assert forbidden not in serialized
    assert context["candidateDeliverables"][1] == {
        "role": "review",
        "status": "candidate",
        "summary": {},
    }
    assert context["candidateDeliverables"][2] == {
        "role": "creativeBrief",
        "status": "candidate",
        "summary": {
            "title": "长" * 240,
            "textLength": 1,
            "fieldCount": 0,
            "partCount": 0,
        },
    }
    assert "episodeState" in context


async def test_composed_screenplay_root_planning_context_uses_db_facts_without_body_leakage(
    temp_db: DatabaseConnection,
):
    source_marker = "SCREENPLAY_PLANNER_MUST_NOT_SEE_SOURCE_TEXT_8C4D"
    await temp_db.execute(
        "INSERT INTO books (id, title) VALUES (?, ?)",
        ["book-planning-leakage", "规划上下文来源书"],
    )
    await temp_db.execute(
        "INSERT INTO outlines (id, title, type, book_id) VALUES (?, ?, ?, ?)",
        [
            "writing-planning-leakage",
            "写作目录",
            "writing",
            "book-planning-leakage",
        ],
    )
    await temp_db.execute(
        "INSERT INTO outline_chapters (id, outline_id, title, sort) "
        "VALUES (?, ?, ?, ?)",
        [
            "chapter-planning-leakage",
            "writing-planning-leakage",
            "第一章",
            1,
        ],
    )
    await temp_db.execute(
        "INSERT INTO articles (chapter_id, content) VALUES (?, ?)",
        [
            "chapter-planning-leakage",
            json.dumps({
                "root": {
                    "children": [{
                        "type": "paragraph",
                        "children": [{"type": "text", "text": source_marker}],
                    }],
                },
            }, ensure_ascii=False),
        ],
    )
    projects = ScreenplayV2ProjectService(temp_db)
    workspace = await projects.create_project(
        command_id="create-screenplay-planning-leakage-project",
        request=CreateScreenplayV2ProjectRequest.model_validate({
            "title": "规划上下文泄漏测试",
            "format": "series",
            "source": {
                "type": "book",
                "bookId": "book-planning-leakage",
                "scope": {"mode": "firstChapters", "count": 1},
            },
            "brief": {"approach": "忠实改编", "premise": "只读取范围摘要"},
        }),
    )
    project_id = workspace["project"]["id"]
    body_marker = "SCREENPLAY_PLANNER_MUST_NOT_SEE_THIS_BODY_7F2A"
    summary_marker = "SCREENPLAY_SUMMARY_CONTENT_MUST_NOT_LEAK_4A9E"
    head_id = await _install_head(
        temp_db,
        project_id,
        "sourceAnalysis",
        {"documentKind": "source_analysis", "body": body_marker},
    )
    await temp_db.execute(
        "UPDATE screenplay_revisions SET summary_json = ? WHERE id = ?",
        [
            json.dumps({
                "title": "原作素材分析",
                "textLength": 12_000,
                "fieldCount": 4,
                "partCount": 1,
                "role": "screenplayDraft",
                "proposalKind": "internal_analysis_candidate",
                "derivedFromIds": ["sprev-derived-private"],
                "inputRevisionIds": ["sprev-input-private"],
                "receipts": [{"id": "receipt-private"}],
                "partRefs": ["part-private"],
                "digest": "sha256:private-summary",
                "body": summary_marker,
                "content": summary_marker,
                "unknownNested": {"id": "nested-private"},
            }, ensure_ascii=False),
            head_id,
        ],
    )
    await temp_db.execute(
        "UPDATE screenplay_revision_parts SET content_text = ? "
        "WHERE revision_id = ? AND part_type = 'document' AND part_key = 'main'",
        [body_marker, head_id],
    )
    command = ScreenplayStageCommand.from_mapping({
        "kind": "stage_action",
        "action": "create",
        "targetRole": "creativeBrief",
        "scope": {"kind": "current_stage"},
    })
    request = AgentRunRequest(
        messages=(AgentMessage(role="user", content="开始生成创作简报"),),
        model=ModelRequest(provider="fixture", model="model"),
        domain_context=ScreenplayAgentDomainContext(
            project_id=project_id,
            turn_id="turn-planning-facts",
            stage_command=command,
        ).to_core_context(),
        mode="agent",
    )
    composition = create_agent_composition(temp_db)
    try:
        provider = composition._profile_registry.require(
            "screenplay"
        ).adapter.context_provider
        bundle = await provider.build_planning_context(
            request,
            ContextBudget(
                window_tokens=128_000,
                output_reserve_tokens=16_000,
                safety_reserve_tokens=4_000,
                runtime_reserve_tokens=4_000,
            ),
        )
    finally:
        await composition.shutdown()

    facts = bundle.diagnostics["hostPlanningFacts"]
    serialized = json.dumps(thaw_json_mapping(facts), ensure_ascii=False)
    assert facts["project"]["id"] == project_id
    assert facts["stageCommand"] == command.to_mapping()
    assert "sourceAnalysis" in facts["availableDeliverables"]
    assert facts["acceptedDeliverables"][0]["role"] == "sourceAnalysis"
    assert facts["acceptedDeliverables"][0]["summary"] == {
        "title": "原作素材分析",
        "textLength": 12_000,
        "fieldCount": 4,
        "partCount": 1,
    }
    assert body_marker not in serialized
    assert summary_marker not in serialized
    assert source_marker not in serialized
    assert "revisionId" not in serialized
    assert "parentRevisionId" not in serialized
    assert "contentText" not in serialized
    assert '"content"' not in serialized
    for forbidden in (
        "sprev-derived-private",
        "sprev-input-private",
        "receipt-private",
        "part-private",
        "sha256:private-summary",
        "nested-private",
        "internal_analysis_candidate",
    ):
        assert forbidden not in serialized


async def test_create_draft_continues_from_head_instead_of_older_candidate():
    workspace = {
        "workflow": {
            "heads": {"screenplayDraft": {"id": "head-draft-1-to-6"}},
        },
        "candidates": [{
            "id": "candidate-draft-1-to-3",
            "role": "screenplayDraft",
            "applicability": "current",
        }],
    }
    intent = ScreenplayIntent(
        action=ScreenplayIntentAction.CREATE,
        instruction="继续创作全部剩余正文",
        requested_deliverable="screenplayDraft",
    )

    assert SqliteScreenplayTaskResolver._base_revision(
        workspace,
        "screenplayDraft",
        intent,
    ) == "head-draft-1-to-6"


async def test_revise_draft_uses_accepted_head_instead_of_older_candidate():
    workspace = {
        "workflow": {
            "heads": {"screenplayDraft": {"id": "head-draft-1-to-8"}},
        },
        "candidates": [{
            "id": "candidate-draft-1-to-3",
            "role": "screenplayDraft",
            "applicability": "current",
        }],
    }
    intent = ScreenplayIntent(
        action=ScreenplayIntentAction.REVISE,
        instruction="根据审阅报告修订完整剧本",
        requested_deliverable="screenplayDraft",
    )

    assert SqliteScreenplayTaskResolver._base_revision(
        workspace,
        "screenplayDraft",
        intent,
    ) == "head-draft-1-to-8"


async def test_revise_current_stage_covers_every_existing_draft_episode():
    class Context:
        async def available_episode_numbers(self, project_id, **kwargs):
            assert project_id == "project-1"
            assert kwargs["draft_revision_id"] == "head-draft-1-to-8"
            return {
                "sceneList": tuple(range(1, 9)),
                "draft": tuple(range(1, 9)),
                "remaining": (),
            }

    resolver = object.__new__(SqliteScreenplayTaskResolver)
    resolver._context = Context()
    intent = ScreenplayIntent(
        action=ScreenplayIntentAction.REVISE,
        instruction="逐项解决审阅报告中的十个问题并修订完整剧本",
        scope=ScreenplayIntentScope(kind=ScreenplayScopeKind.CURRENT_STAGE),
        requested_deliverable="screenplayDraft",
    )

    assert await resolver._resolve_episodes(
        "project-1",
        intent,
        draft_revision_id="head-draft-1-to-8",
    ) == tuple(range(1, 9))


async def test_review_resolver_binds_every_episode_from_current_draft_head():
    class Context:
        def __init__(self):
            self.requested_draft_revision_ids = []

        async def head_revision_refs(self, project_id):
            assert project_id == "project-review-current"
            return ("draft-current", "review-old")

        async def draft_revision_manifest(self, project_id, draft_revision_id):
            assert project_id == "project-review-current"
            self.requested_draft_revision_ids.append(draft_revision_id)
            return {
                1: ("ep01_s01",),
                2: ("ep02_s01", "ep02_s02"),
            }

    context = Context()
    resolver = object.__new__(SqliteScreenplayTaskResolver)
    resolver._context = context

    resolved = await resolver.resolve(
        workspace={
            "project": {
                "id": "project-review-current",
                "source": {"type": "original"},
            },
            "workflow": {
                "stage": "review",
                "heads": {
                    "screenplayDraft": {"id": "draft-current"},
                    "review": {"id": "review-old"},
                },
            },
            "deliverables": [{"role": "review"}],
            "candidates": [],
        },
        intent=ScreenplayIntent(
            action=ScreenplayIntentAction.REVIEW,
            instruction="重新审阅",
            requested_deliverable="review",
        ),
    )

    assert resolved.reviewed_draft_id == "draft-current"
    assert resolved.episode_scene_ids == {
        1: ("ep01_s01",),
        2: ("ep02_s01", "ep02_s02"),
    }
    assert context.requested_draft_revision_ids == ["draft-current"]


def _request(session_id: int, content: str):
    return SubmitScreenplayAgentTurnRequest.model_validate({
        "sessionId": session_id,
        "content": content,
        "runtime": {
            "apiKey": "secret",
            "apiProvider": "openai",
            "baseURL": "https://provider.example/v1",
            "options": {
                "model": "planner-model",
                "model_profile": "deepseek:deepseek-v4-flash",
                "max_tokens": 32_768,
            },
            "contextWindow": "128k",
        },
    })


async def test_screenplay_structured_calls_enable_supported_provider_json_mode(
    monkeypatch: pytest.MonkeyPatch,
):
    runtime = _request(1, "测试 JSON mode").runtime
    runtime.options.update({
        "model": "deepseek-v4-flash",
        "model_profile": "deepseek:deepseek-v4-flash",
        "response_format": {"type": "text"},
    })
    runtime.baseURL = "https://api.deepseek.com"

    requirements = []
    real_preflight = model_runtime.preflight_capabilities

    def capture_preflight(snapshot, requirement):
        requirements.append(requirement)
        real_preflight(snapshot, requirement)

    monkeypatch.setattr(model_runtime, "preflight_capabilities", capture_preflight)
    structured = model_request_from_runtime(runtime, json_object_output=True)
    ordinary = model_request_from_runtime(runtime)

    assert structured.options["response_format"] == {"type": "json_object"}
    assert structured.protocol_capabilities.json_schema_level == "json_object"
    assert "response_format" not in ordinary.options
    assert [item.structured_output_level for item in requirements] == [
        "json_object",
        "none",
    ]


async def test_screenplay_task_preserves_managed_model_failure_code():
    code, message = _task_failure(ModelGatewayError(
        "model output is incomplete",
        code="model_output_truncated",
        retryable=False,
    ))

    assert code == "model_output_truncated"
    assert "未形成完整候选稿" in message
    assert "不完整结果未被保存" in message


@pytest.mark.parametrize(("code", "retryable", "expected_category"), (
    ("tool_execution_failed", True, FailureCategory.TOOL_EXECUTION),
    ("model_output_truncated", True, FailureCategory.MODEL_OUTPUT_INVALID),
    ("invalid_tool_results", True, FailureCategory.MODEL_OUTPUT_INVALID),
    ("max_model_rounds", True, FailureCategory.MODEL_OUTPUT_INVALID),
    ("invalid_tool_arguments_json", True, FailureCategory.MODEL_OUTPUT_INVALID),
    ("tool_call_truncated", True, FailureCategory.MODEL_OUTPUT_INVALID),
    ("provider_bad_request", False, FailureCategory.PROTOCOL_INCOMPATIBLE),
))
async def test_screenplay_failure_codes_have_typed_durable_dispositions(
    code,
    retryable,
    expected_category,
):
    executor = object.__new__(ScreenplayTaskUnitExecutor)
    error = ModelGatewayError(code, code=code, retryable=retryable)

    failure = executor.classify_failure(error)
    decision = decide_failure(failure, attempts_remaining=0)

    assert failure.category is expected_category
    assert failure.code == code
    assert decision.disposition is FailureDisposition.PAUSE_RECOVERABLE


@pytest.mark.parametrize("code", (
    "provider_bad_request",
    "provider_reasoning_context_invalid",
    "unsupported_model_feature",
    "provider_authentication_failed",
))
async def test_configuration_and_protocol_failures_stop_the_whole_operation(code):
    failure = classify_screenplay_run_failure(
        ModelGatewayError(code, code=code, retryable=False)
    )

    assert failure.scope is FailureScope.SYSTEMIC


async def test_review_aggregate_requires_every_episode_and_rejects_execution_metadata():
    def episode(number: int) -> dict:
        return {
            "title": f"第 {number} 集审阅",
            "contentText": f"第 {number} 集审阅正文",
            "contentJson": {
                "verdict": "ready",
                "issues": [],
                "issueCount": 0,
                "criticalIssueCount": 0,
                "reviewedEpisode": number,
                "reviewedDraftId": "draft-head",
                "reviewedContentDigest": f"digest-{number}",
                "reviewDimensions": list(REVIEW_DIMENSIONS),
                "reviewStatus": "completed",
                "inputContractVersion": 2,
                "partReceipts": [
                    f"receipt-{number}-{dimension}"
                    for dimension in REVIEW_DIMENSIONS
                ],
            },
        }

    with pytest.raises(ValueError, match="required episode validations"):
        aggregate_review_validations(
            [episode(1)],
            required_episode_numbers=(1, 2),
        )

    contaminated = episode(1)
    contaminated["contentJson"]["failedEpisodes"] = [{"episodeNumber": 1}]
    with pytest.raises(ValueError, match="execution metadata"):
        aggregate_review_validations(
            [contaminated],
            required_episode_numbers=(1,),
        )

    _, content, _ = aggregate_review_validations(
        [episode(1), episode(2)],
        required_episode_numbers=(1, 2),
    )
    assert content["reviewedEpisodes"] == [1, 2]
    assert content["inputContractVersion"] == 2
    assert "failedEpisodes" not in content


async def test_review_failure_is_projected_from_the_operation_part():
    projected = _unit_view({
        "unit_id": "review:3:dialogue",
        "position": 7,
        "status": "blocked",
        "metadata_json": json.dumps({
            "unitKind": "generate_review_dimension",
            "input": {
                "episodeNumber": 3,
                "reviewDimension": "dialogue",
            },
        }),
        "error_code": "model_output_truncated",
        "attempt": 1,
    })

    assert projected["error"] == {
        "code": "model_output_truncated",
        "message": "第 3 集审阅失败",
    }


class _Planner:
    def __init__(self, intent: ScreenplayIntent) -> None:
        self.intent = intent
        self.calls = []

    async def plan(self, **kwargs):
        self.calls.append(kwargs)
        return PlannedScreenplayIntent(self.intent, "run-semantic-planner")


class _ModelGateway:
    def __init__(self, api_key: str) -> None:
        assert api_key == "secret"
        self.invocations = []
        self.calls = []

    def describe_invocation(self, messages, invocation):
        return {
            "messageCount": len(messages),
            "model": invocation.request.model,
        }

    async def stream(self, messages, invocation, signal=None):
        del signal
        self.calls.append((tuple(messages), invocation))
        self.invocations.append(invocation)

        async def chunks():
            yield ModelStreamChunk(finish_reason=ModelFinishReason.STOP)

        return ModelStream(chunks=chunks(), model=invocation.request.model)

    async def complete(self, messages, invocation, signal=None):
        del messages, signal
        self.invocations.append(invocation)
        return ModelCompletion(
            message=AgentMessage(role="assistant", content="{}"),
            model=invocation.request.model,
            finish_reason=ModelFinishReason.STOP,
        )


class _ScriptedModelGateway(_ModelGateway):
    def __init__(self, api_key: str, rounds) -> None:
        super().__init__(api_key)
        self.rounds = list(rounds)

    async def stream(self, messages, invocation, signal=None):
        del signal
        self.calls.append((tuple(messages), invocation))
        self.invocations.append(invocation)
        round_chunks = self.rounds.pop(0)

        async def chunks():
            for chunk in round_chunks:
                yield chunk

        return ModelStream(chunks=chunks(), model=invocation.request.model)


def _planner_answer_json(reply: str = "没有待修复 JSON") -> str:
    return json.dumps({
        "action": "answer",
        "instruction": "说明没有待修复 JSON",
        "scope": {"kind": "current_stage"},
        "constraints": [],
        "preserve": [],
        "requestedDeliverable": None,
        "reply": reply,
    }, ensure_ascii=False)


def _planner_review_json() -> str:
    return json.dumps({
        "action": "review",
        "instruction": "审阅当前完整剧本",
        "scope": {"kind": "current_stage"},
        "constraints": [],
        "preserve": [],
        "requestedDeliverable": "review",
        "reply": None,
    }, ensure_ascii=False)


async def test_planner_repairs_schema_valid_intent_that_violates_stage_command(
    temp_db: DatabaseConnection,
):
    _, workspace, session = await _project_and_session(temp_db)
    gateway = _ScriptedModelGateway("secret", [
        [
            ModelStreamChunk(content_delta=_planner_answer_json()),
            ModelStreamChunk(finish_reason=ModelFinishReason.STOP),
        ],
        [
            ModelStreamChunk(content_delta=_planner_review_json()),
            ModelStreamChunk(finish_reason=ModelFinishReason.STOP),
        ],
    ])
    planner = ModelScreenplayIntentPlanner(
        temp_db,
        composition=_core_composition(temp_db, gateway),
    )
    command = ScreenplayStageCommand.from_mapping({
        "kind": "stage_action",
        "action": "review",
        "targetRole": "review",
        "scope": {"kind": "current_stage"},
    })

    planned = await planner.plan(
        workspace=workspace,
        history=(),
        user_content="开始审阅",
        stage_command=command,
        runtime=_request(session["id"], "开始审阅").runtime,
        session_id=session["id"],
        turn_id="turn-review-command",
    )

    assert planned.intent.action is ScreenplayIntentAction.REVIEW
    assert len(gateway.invocations) == 2
    first_payload = json.loads(str(gateway.calls[0][0][1].content))
    assert first_payload["requiredStageCommand"] == command.to_mapping()


async def test_planner_preserves_command_mismatch_after_failed_repair(
    temp_db: DatabaseConnection,
):
    _, workspace, session = await _project_and_session(temp_db)
    gateway = _ScriptedModelGateway("secret", [
        [
            ModelStreamChunk(content_delta=_planner_answer_json()),
            ModelStreamChunk(finish_reason=ModelFinishReason.STOP),
        ],
        [
            ModelStreamChunk(content_delta=_planner_answer_json("仍然是回答")),
            ModelStreamChunk(finish_reason=ModelFinishReason.STOP),
        ],
    ])
    planner = ModelScreenplayIntentPlanner(
        temp_db,
        composition=_core_composition(temp_db, gateway),
    )
    command = ScreenplayStageCommand.from_mapping({
        "kind": "stage_action",
        "action": "review",
        "targetRole": "review",
        "scope": {"kind": "current_stage"},
    })

    with pytest.raises(ScreenplayIntentCommandMismatchError):
        await planner.plan(
            workspace=workspace,
            history=(),
            user_content="开始审阅",
            stage_command=command,
            runtime=_request(session["id"], "开始审阅").runtime,
            session_id=session["id"],
            turn_id="turn-review-command-mismatch",
        )


async def test_planner_omits_required_command_for_free_text(
    temp_db: DatabaseConnection,
):
    _, workspace, session = await _project_and_session(temp_db)
    gateway = _ScriptedModelGateway("secret", [[
        ModelStreamChunk(content_delta=_planner_answer_json("正常回答")),
        ModelStreamChunk(finish_reason=ModelFinishReason.STOP),
    ], [
        ModelStreamChunk(content_delta="正常回答"),
        ModelStreamChunk(finish_reason=ModelFinishReason.STOP),
    ]])
    planner = ModelScreenplayIntentPlanner(
        temp_db,
        composition=_core_composition(temp_db, gateway),
    )

    planned = await planner.plan(
        workspace=workspace,
        history=(),
        user_content="现在到哪一步？",
        stage_command=None,
        runtime=_request(session["id"], "现在到哪一步？").runtime,
        session_id=session["id"],
        turn_id="turn-free-text",
    )

    assert planned.intent.action is ScreenplayIntentAction.ANSWER
    first_payload = json.loads(str(gateway.calls[0][0][1].content))
    assert "requiredStageCommand" not in first_payload


async def test_structured_model_repair_is_persisted_as_one_diagnostic_run(
    temp_db: DatabaseConnection,
):
    _, _, session = await _project_and_session(temp_db)
    malformed = '{"contentText":"角色说"少亲自来"。"}'
    runtime = _request(session["id"], "测试结构化输出").runtime
    runtime.options.update({
        "model": "deepseek-v4-flash",
        "model_profile": "deepseek:deepseek-v4-flash",
        "thinking": {"type": "enabled"},
    })
    gateway = _ScriptedModelGateway("secret", [
        [
            ModelStreamChunk(content_delta=malformed),
            ModelStreamChunk(finish_reason=ModelFinishReason.STOP),
        ],
        [
            ModelStreamChunk(content_delta='```json\n{"answer":"ok"}\n```'),
            ModelStreamChunk(finish_reason=ModelFinishReason.STOP),
        ],
    ])
    result = await screenplay_structured_call.ScreenplayStructuredCallService(
        temp_db,
        composition=_core_composition(temp_db, gateway),
    ).run_json(
        runtime=runtime,
        session_id=session["id"],
        prompt="测试结构化输出",
        system_instruction="只输出 JSON",
        user_payload={"question": "test"},
        binding_namespace="screenplay.agent.test",
        binding_aggregate_id="project-test",
        binding_command_id="repair-test",
        phase="screenplay_test",
        repair_instruction="修复 JSON",
        validate=lambda value: value,
    )

    assert result.value == {"answer": "ok"}
    assert [invocation.reasoning_mode for invocation in gateway.invocations] == [
        ReasoningMode.DEFAULT,
        ReasoningMode.DEFAULT,
    ]
    assert [[message.role.value for message in messages] for messages, _ in gateway.calls] == [
        ["system", "user"],
        ["system", "user", "assistant", "developer"],
    ]
    assert gateway.calls[1][0][-2].content == malformed
    assert "question" not in str(gateway.calls[1][0][-2].content)
    assert await temp_db.fetch_one(
        "SELECT status, binding_namespace, final_response "
        "FROM ai_agent_runs WHERE id = ?",
        [result.run_id],
    ) == {
        "status": "done",
        "binding_namespace": "screenplay.agent.test",
        "final_response": "",
    }
    assert await temp_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_run_events "
        "WHERE run_id = ? AND event_type = 'model.call_recorded'",
        [result.run_id],
    ) == {"count": 2}
    assert await temp_db.fetch_all(
        "SELECT json_extract(payload_json, '$.parameters.messageCount') AS count "
        "FROM ai_agent_run_events WHERE run_id = ? "
        "AND event_type = 'model.call_recorded' ORDER BY id",
        [result.run_id],
    ) == [{"count": 2}, {"count": 4}]


async def test_truncated_structured_output_is_never_repaired_or_replayed(
    temp_db: DatabaseConnection,
):
    _, _, session = await _project_and_session(temp_db)

    class TruncatedGateway(_ModelGateway):
        async def stream(self, messages, invocation, signal=None):
            del messages, signal
            self.invocations.append(invocation)

            async def chunks():
                yield ModelStreamChunk(content_delta='{"answer":"unfinished')
                yield ModelStreamChunk(finish_reason=ModelFinishReason.LENGTH)

            return ModelStream(chunks=chunks(), model=invocation.request.model)

    gateway = TruncatedGateway("secret")
    with pytest.raises(ModelGatewayError) as captured:
        await screenplay_structured_call.ScreenplayStructuredCallService(
            temp_db,
            composition=_core_composition(temp_db, gateway),
        ).run_json(
            runtime=_request(session["id"], "测试截断输出").runtime,
            session_id=session["id"],
            prompt="测试截断输出",
            system_instruction="只输出 JSON",
            user_payload={"question": "test"},
            binding_namespace="screenplay.agent.test",
            binding_aggregate_id="project-test",
            binding_command_id="truncated-output-test",
            phase="screenplay_test",
            repair_instruction="修复 JSON",
            validate=lambda value: value,
        )

    assert captured.value.code == "model_output_truncated"
    assert len(gateway.invocations) == 1
    assert await temp_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_run_events "
        "WHERE event_type = 'model.call_recorded'"
    ) == {"count": 1}


async def test_structured_model_renews_its_core_run_lease_during_slow_generation(
    temp_db: DatabaseConnection,
):
    _, _, session = await _project_and_session(temp_db)

    class SlowGateway(_ScriptedModelGateway):
        async def stream(self, messages, invocation, signal=None):
            await asyncio.sleep(0.65)
            return await super().stream(messages, invocation, signal)

    gateway = SlowGateway("secret", [[
        ModelStreamChunk(content_delta='{"answer":"ok"}'),
        ModelStreamChunk(finish_reason=ModelFinishReason.STOP),
    ]])
    runtime = _request(session["id"], "测试慢速结构化输出").runtime
    result = await screenplay_structured_call.ScreenplayStructuredCallService(
        temp_db,
        composition=_core_composition(temp_db, gateway),
    ).run_json(
        runtime=runtime,
        session_id=session["id"],
        prompt="测试慢速结构化输出",
        system_instruction="只输出 JSON",
        user_payload={"question": "test"},
        binding_namespace="screenplay.agent.test",
        binding_aggregate_id="project-test",
        binding_command_id="slow-lease-test",
        phase="screenplay_test",
        repair_instruction="修复 JSON",
        validate=lambda value: value,
    )

    assert result.value == {"answer": "ok"}
    assert await temp_db.fetch_one(
        "SELECT status, execution_owner_id, lease_expires_at_ms "
        "FROM ai_agent_runs WHERE id = ?",
        [result.run_id],
    ) == {
        "status": "done",
        "execution_owner_id": None,
        "lease_expires_at_ms": None,
    }


async def test_structured_model_preserves_the_frontend_thinking_option(
    temp_db: DatabaseConnection,
):
    _, _, session = await _project_and_session(temp_db)
    reasoning_modes = []

    gateway = _ScriptedModelGateway("secret", [[
        ModelStreamChunk(content_delta='{"answer":"ok"}'),
        ModelStreamChunk(finish_reason=ModelFinishReason.STOP),
    ]])
    runtime = _request(session["id"], "测试思考配置").runtime
    runtime.options.update({
        "model": "deepseek-v4-flash",
        "model_profile": "deepseek:deepseek-v4-flash",
        "thinking": {"type": "enabled"},
    })

    await screenplay_structured_call.ScreenplayStructuredCallService(
        temp_db,
        composition=_core_composition(temp_db, gateway),
    ).run_json(
        runtime=runtime,
        session_id=session["id"],
        prompt="测试思考配置",
        system_instruction="只输出 JSON",
        user_payload={"question": "test"},
        binding_namespace="screenplay.agent.test",
        binding_aggregate_id="project-test",
        binding_command_id="thinking-option-test",
        phase="screenplay_test",
        repair_instruction="修复 JSON",
        validate=lambda value: value,
    )

    reasoning_modes.extend(item.reasoning_mode for item in gateway.invocations)
    assert reasoning_modes == [ReasoningMode.DEFAULT]


async def test_screenplay_uses_the_exact_profile_or_user_output_limit():
    from purra.model_protocol import resolve_invocation_output_limit
    from infrastructure.models.profiles.registry import resolve_model_profile

    snapshot = resolve_model_profile(
        "deepseek:deepseek-v4-flash",
        "deepseek-v4-flash",
        "https://api.deepseek.com",
    ).capability_snapshot(context_window_tokens=1_000_000)

    assert resolve_invocation_output_limit(
        snapshot,
        explicit_user_override=None,
    ).max_tokens == 393_216
    assert resolve_invocation_output_limit(
        snapshot,
        explicit_user_override=256_000,
    ).max_tokens == 256_000


async def test_model_failure_keeps_its_run_id_in_the_shared_diagnostic_stream(
    temp_db: DatabaseConnection,
):
    _, _, session = await _project_and_session(temp_db)

    class FailingGateway(_ModelGateway):
        async def stream(self, *_args, **_kwargs):
            raise RuntimeError("provider disconnected")

    gateway = FailingGateway("secret")
    runtime = _request(session["id"], "测试模型失败诊断").runtime

    with pytest.raises(ModelGatewayError) as captured:
        await screenplay_structured_call.ScreenplayStructuredCallService(
            temp_db,
            composition=_core_composition(temp_db, gateway),
        ).run_json(
            runtime=runtime,
            session_id=session["id"],
            prompt="测试模型失败诊断",
            system_instruction="只输出 JSON",
            user_payload={"question": "test"},
            binding_namespace="screenplay.agent.test",
            binding_aggregate_id="project-test",
            binding_command_id="failure-diagnostic-test",
            phase="screenplay_test",
            repair_instruction="修复 JSON",
            validate=lambda value: value,
        )

    assert captured.value.code == "model_gateway_error"
    assert await _public_text_events(temp_db) == []
    assert await temp_db.fetch_one(
        "SELECT status FROM ai_agent_runs ORDER BY create_time DESC LIMIT 1"
    ) == {"status": "failed"}


async def test_reasoning_only_structured_output_retries_original_not_repair(
    temp_db: DatabaseConnection,
):
    _, _, session = await _project_and_session(temp_db)
    expected = '{"action":"review"}'
    gateway = _ScriptedModelGateway("secret", [
        [
            ModelStreamChunk(reasoning_delta=expected),
            ModelStreamChunk(finish_reason=ModelFinishReason.STOP),
        ],
        [
            ModelStreamChunk(content_delta=expected),
            ModelStreamChunk(finish_reason=ModelFinishReason.STOP),
        ],
    ])

    result = await screenplay_structured_call.ScreenplayStructuredCallService(
        temp_db,
        composition=_core_composition(temp_db, gateway),
    ).run_json(
        runtime=_request(session["id"], "测试 reasoning JSON").runtime,
        session_id=session["id"],
        prompt="测试 reasoning JSON",
        system_instruction="只输出 JSON",
        user_payload={},
        binding_namespace="screenplay.agent.test",
        binding_aggregate_id="project-test",
        binding_command_id="reasoning-json-test",
        phase="screenplay_intent_planning",
        repair_instruction="修复 JSON",
        validate=lambda value: value,
    )

    assert result.value == {"action": "review"}
    assert await temp_db.fetch_all(
        "SELECT json_extract(payload_json, '$.phase') AS phase "
        "FROM ai_agent_run_events WHERE run_id = ? "
        "AND event_type = 'model.call_recorded' ORDER BY id",
        [result.run_id],
    ) == [
        {"phase": "generation"},
        {"phase": "generation"},
    ]
    assert await _public_text_events(temp_db) == []


async def test_empty_structured_output_never_sends_an_empty_repair_candidate(
    temp_db: DatabaseConnection,
):
    _, _, session = await _project_and_session(temp_db)
    empty = [ModelStreamChunk(finish_reason=ModelFinishReason.STOP)]
    gateway = _ScriptedModelGateway("secret", [empty, empty, empty])

    with pytest.raises(ModelGatewayError) as captured:
        await screenplay_structured_call.ScreenplayStructuredCallService(
            temp_db,
            composition=_core_composition(temp_db, gateway),
        ).run_json(
            runtime=_request(session["id"], "测试空 JSON").runtime,
            session_id=session["id"],
            prompt="测试空 JSON",
            system_instruction="只输出 JSON",
            user_payload={},
            binding_namespace="screenplay.agent.test",
            binding_aggregate_id="project-test",
            binding_command_id="empty-json-test",
            phase="screenplay_test",
            repair_instruction="修复 JSON",
            validate=lambda value: value,
        )

    assert captured.value.code == "empty_model_response"
    assert len(gateway.invocations) == 3
    assert await temp_db.fetch_all(
        "SELECT json_extract(payload_json, '$.phase') AS phase "
        "FROM ai_agent_run_events WHERE event_type = 'model.call_recorded' "
        "ORDER BY id"
    ) == [
        {"phase": "generation"},
        {"phase": "generation"},
        {"phase": "generation"},
    ]
    assert await _public_text_events(temp_db) == []


async def test_repair_that_is_still_invalid_fails_as_structured_output_invalid(
    temp_db: DatabaseConnection,
):
    _, _, session = await _project_and_session(temp_db)
    gateway = _ScriptedModelGateway("secret", [
        [
            ModelStreamChunk(content_delta="{invalid"),
            ModelStreamChunk(finish_reason=ModelFinishReason.STOP),
        ],
        [
            ModelStreamChunk(content_delta="still invalid"),
            ModelStreamChunk(finish_reason=ModelFinishReason.STOP),
        ],
    ])

    with pytest.raises(ModelGatewayError) as captured:
        await screenplay_structured_call.ScreenplayStructuredCallService(
            temp_db,
            composition=_core_composition(temp_db, gateway),
        ).run_json(
            runtime=_request(session["id"], "测试无效 JSON").runtime,
            session_id=session["id"],
            prompt="测试无效 JSON",
            system_instruction="只输出 JSON",
            user_payload={},
            binding_namespace="screenplay.agent.test",
            binding_aggregate_id="project-test",
            binding_command_id="invalid-json-test",
            phase="screenplay_test",
            repair_instruction="修复 JSON",
            validate=lambda value: value,
        )

    assert captured.value.code == "structured_output_invalid"
    assert len(gateway.invocations) == 2


async def test_generated_screenplay_body_never_becomes_a_chat_delta(
    temp_db: DatabaseConnection,
):
    _, _, session = await _project_and_session(temp_db)

    gateway = _ScriptedModelGateway("secret", [[
        ModelStreamChunk(content_delta=(
            '{"sceneText":"这里是完整剧本正文"}'
        )),
        ModelStreamChunk(finish_reason=ModelFinishReason.STOP),
    ]])
    result = await screenplay_structured_call.ScreenplayStructuredCallService(
        temp_db,
        composition=_core_composition(temp_db, gateway),
    ).run_json(
        runtime=_request(session["id"], "测试正文隔离").runtime,
        session_id=session["id"],
        prompt="测试正文隔离",
        system_instruction="只输出 JSON",
        user_payload={},
        binding_namespace="screenplay.agent.test",
        binding_aggregate_id="project-test",
        binding_command_id="body-isolation-test",
        phase="screenplay_episode_generation",
        repair_instruction="修复 JSON",
        validate=lambda value: value,
        project_execution=lambda value: (f"sceneText: {value['sceneText']}",),
    )

    assert result.value == {"sceneText": "这里是完整剧本正文"}
    assert await _public_text_events(temp_db, result.run_id) == []


async def test_screenplay_stream_replays_canonical_output_journal(
    temp_db: DatabaseConnection,
):
    _, workspace, session = await _project_and_session(temp_db)
    project_id = workspace["project"]["id"]
    repository = SqliteScreenplayAgentRepository(
        temp_db,
        owner_id="screenplay-stream-test",
    )
    turn = await repository.begin_turn(
        command_id="stream-turn",
        project_id=project_id,
        session_id=session["id"],
        content="创作下一集",
        stage_command=None,
        runtime_profile={"provider": "openai", "model": "test"},
    )
    runs = SqliteRunRepository(temp_db)
    outputs = SqliteAgentOutputRepository(temp_db, run_repository=runs)
    begun, canonical = await outputs.begin_run_lifecycle(
        RunCreateParams(
            session_id=session["id"],
            prompt="创作下一集",
            mode="agent",
            turn_id=turn["id"],
        ),
        AgentEvent(
            type=CoreEventType.RUN_STARTED,
            payload={"status": RunStatus.RUNNING.value},
        ),
    )
    chunks = ScreenplayCanonicalOutputQuery(
        temp_db,
        output_repository=outputs,
    )

    page = await chunks.list_chunks(
        project_id=project_id,
        session_id=session["id"],
    )

    assert page["hasMore"] is False
    assert page["nextCursor"] > 0
    assert page["chunks"] == [{
        "cursor": page["chunks"][0]["cursor"],
        "runId": begun.run_id,
        "turnId": turn["id"],
        "taskId": None,
        "userContent": "创作下一集",
        "model": "test",
        "turnCreatedAt": page["chunks"][0]["turnCreatedAt"],
        "chunk": {
            "eventId": canonical.event_id,
            "outputStreamId": None,
            "runId": begun.run_id,
            "turnId": turn["id"],
            "invocationId": None,
            "sequence": 1,
            "source": "runtime",
            "kind": "run.lifecycle",
            "channel": "lifecycle",
            "visibility": "public",
            "payload": {"status": "running"},
            "occurredAt": canonical.occurred_at.isoformat(),
            "emittedAt": canonical.emitted_at.isoformat(),
        },
        "createdAt": page["chunks"][0]["createdAt"],
    }]


async def test_turn_persists_stage_command_and_rejects_changed_idempotent_replay(
    temp_db: DatabaseConnection,
):
    _, workspace, session = await _project_and_session(temp_db)
    repository = SqliteScreenplayAgentRepository(
        temp_db,
        owner_id="screenplay-stage-command-test",
    )
    review_command = {
        "kind": "stage_action",
        "action": "review",
        "targetRole": "review",
        "scope": {"kind": "current_stage"},
    }
    turn = await repository.begin_turn(
        command_id="persist-stage-command",
        project_id=workspace["project"]["id"],
        session_id=session["id"],
        content="开始审阅",
        stage_command=review_command,
        runtime_profile={"provider": "openai", "model": "test"},
    )

    replay = await repository.begin_turn(
        command_id="persist-stage-command",
        project_id=workspace["project"]["id"],
        session_id=session["id"],
        content="开始审阅",
        stage_command=review_command,
        runtime_profile={"provider": "openai", "model": "test"},
    )
    snapshot = await repository.get_snapshot(
        project_id=workspace["project"]["id"],
        session_id=session["id"],
    )

    assert replay["id"] == turn["id"]
    assert turn["stageCommand"] == review_command
    assert snapshot["turns"][0]["stageCommand"] == review_command

    with pytest.raises(AppError) as captured:
        await repository.begin_turn(
            command_id="persist-stage-command",
            project_id=workspace["project"]["id"],
            session_id=session["id"],
            content="开始审阅",
            stage_command={
                **review_command,
                "action": "revise",
                "targetRole": "screenplayDraft",
            },
            runtime_profile={"provider": "openai", "model": "test"},
        )

    assert captured.value.status_code == 409


async def test_retired_output_tables_are_not_recreated_by_schema_init(
    temp_db: DatabaseConnection,
):
    await init_screenplay_agent_schema(temp_db)

    assert await temp_db.fetch_all(
        "SELECT name FROM sqlite_master WHERE type = 'table' "
        "AND name IN ('screenplay_agent_events', 'screenplay_agent_chunks')"
    ) == []


async def test_restart_exposes_an_abandoned_turn_as_a_terminal_failure(
    temp_db: DatabaseConnection,
):
    _, workspace, session = await _project_and_session(temp_db)
    repository = SqliteScreenplayAgentRepository(
        temp_db,
        owner_id="screenplay-agent-before-turn-restart",
    )
    turn = await repository.begin_turn(
        command_id="queued-before-restart",
        project_id=workspace["project"]["id"],
        session_id=session["id"],
        content="分析一下当前剧本",
        stage_command=None,
        runtime_profile={"provider": "openai", "model": "test"},
    )

    recovered = await repository.recover_after_restart()
    snapshot = await repository.get_snapshot(
        project_id=workspace["project"]["id"],
        session_id=session["id"],
    )

    assert recovered == (turn["id"],)
    assert snapshot["turns"][0]["status"] == "failed"
    assert snapshot["turns"][0]["error"]["code"] == "screenplay_agent_restarted"
    assert snapshot["turns"][0]["assistantContent"] == ""


async def test_restart_finishes_a_durable_cancel_request(
    temp_db: DatabaseConnection,
):
    _, workspace, session = await _project_and_session(temp_db)
    repository = SqliteScreenplayAgentRepository(
        temp_db,
        owner_id="screenplay-agent-cancel-recovery",
    )
    turn = await repository.begin_turn(
        command_id="queued-before-cancel-recovery",
        project_id=workspace["project"]["id"],
        session_id=session["id"],
        content="停止这一轮",
        stage_command=None,
        runtime_profile={"provider": "openai", "model": "test"},
    )
    operations = SqliteScreenplayOperationRepository(temp_db)
    receipt = await operations.request_cancel(
        turn["id"],
        idempotency_key="cancel-before-restart",
    )

    recovered = await repository.recover_after_restart()
    snapshot = await repository.get_snapshot(
        project_id=workspace["project"]["id"],
        session_id=session["id"],
    )

    assert recovered == (turn["id"],)
    assert snapshot["turns"][0]["status"] == "canceled"
    assert snapshot["turns"][0]["assistantContent"] == ""
    assert await temp_db.fetch_one(
        "SELECT cancel_receipt_id FROM screenplay_agent_turns WHERE id = ?",
        [turn["id"]],
    ) == {"cancel_receipt_id": receipt.id}


async def _install_head(db, project_id: str, role: str, content: dict) -> str:
    deliverable = await db.fetch_one(
        "SELECT id FROM screenplay_deliverables WHERE project_id = ? AND role = ?",
        [project_id, role],
    )
    assert deliverable is not None
    revision_id = f"head-{role}"
    encoded = json.dumps(content, ensure_ascii=False, sort_keys=True)
    digest = hashlib.sha256(encoded.encode()).hexdigest()
    await db.execute(
        "INSERT INTO screenplay_revisions "
        "(id, project_id, deliverable_id, revision_no, content_digest, "
        "summary_json, created_by) VALUES (?, ?, ?, 1, ?, '{}', 'test')",
        [revision_id, project_id, deliverable["id"], digest],
    )
    await db.execute(
        "INSERT INTO screenplay_revision_parts "
        "(revision_id, part_type, part_key, position, payload_json, "
        "content_text, content_digest) VALUES (?, 'document', 'main', 0, ?, '', ?)",
        [revision_id, encoded, digest],
    )
    episode_payloads = (
        content.get("episodes", [])
        if role == "structure"
        else [
            {"episodeNumber": number, "scenes": scenes}
            for number, scenes in sorted({
                int(scene["episodeNumber"]): [
                    item for item in content.get("scenes", [])
                    if int(item["episodeNumber"]) == int(scene["episodeNumber"])
                ]
                for scene in content.get("scenes", [])
            }.items())
        ] if role == "sceneList" else []
    )
    for position, episode in enumerate(episode_payloads, start=1):
        number = int(episode.get("episodeNumber") or episode.get("number"))
        payload = json.dumps(episode, ensure_ascii=False, sort_keys=True)
        await db.execute(
            "INSERT INTO screenplay_revision_parts "
            "(revision_id, part_type, part_key, position, payload_json, "
            "content_text, content_digest) VALUES (?, 'episode', ?, ?, ?, '', ?)",
            [
                revision_id,
                str(number),
                position,
                payload,
                hashlib.sha256(payload.encode()).hexdigest(),
            ],
        )
    await db.execute(
        "INSERT INTO screenplay_project_heads "
        "(project_id, deliverable_id, revision_id) VALUES (?, ?, ?)",
        [project_id, deliverable["id"], revision_id],
    )
    return revision_id


async def test_review_episode_context_reads_the_requested_immutable_revision(
    temp_db: DatabaseConnection,
):
    _, workspace, _ = await _project_and_session(temp_db)
    project_id = workspace["project"]["id"]
    scene_list_id = await _install_head(
        temp_db,
        project_id,
        "sceneList",
        {"scenes": [{
            "id": "scene-1",
            "episodeNumber": 1,
            "heading": "审讯室",
        }]},
    )
    deliverable = await temp_db.fetch_one(
        "SELECT id FROM screenplay_deliverables "
        "WHERE project_id = ? AND role = 'screenplayDraft'",
        [project_id],
    )
    assert deliverable is not None
    for revision_no, revision_id, text in (
        (1, "draft-immutable-old", "指定旧版本正文"),
        (2, "draft-current-head", "当前 Head 正文"),
    ):
        payload = {
            "episodeNumber": 1,
            "sceneIds": ["scene-1"],
            "sceneTexts": [{"sceneId": "scene-1", "contentText": text}],
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        await temp_db.execute(
            "INSERT INTO screenplay_revisions "
            "(id, project_id, deliverable_id, revision_no, content_digest, "
            "summary_json, created_by) VALUES (?, ?, ?, ?, ?, '{}', 'test')",
            [revision_id, project_id, deliverable["id"], revision_no, digest],
        )
        await temp_db.execute(
            "INSERT INTO screenplay_revision_parts "
            "(revision_id, part_type, part_key, position, payload_json, "
            "content_text, content_digest) VALUES (?, 'episode', '1', 1, ?, ?, ?)",
            [revision_id, encoded, text, digest],
        )
    await temp_db.execute(
        "INSERT INTO screenplay_project_heads "
        "(project_id, deliverable_id, revision_id) VALUES (?, ?, ?)",
        [project_id, deliverable["id"], "draft-current-head"],
    )

    context = await ScreenplayAgentContextQuery(temp_db).episode_context(
        project_id,
        1,
        draft_revision_id="draft-immutable-old",
        scene_list_revision_id=scene_list_id,
    )

    assert context["currentDraft"]["sceneTexts"][0]["contentText"] == (
        "指定旧版本正文"
    )


class _StructuredDraftModels:
    async def run_json(self, **kwargs):
        payload = kwargs["user_payload"]
        number = int(payload["episodeNumber"])
        if kwargs["phase"] == "screenplay_scene_generation":
            scene = payload["scenePlan"]
            value = kwargs["validate"]({
                "sceneId": payload["sceneId"],
                "processSummary": (
                    f"场景 {payload['sceneId']} 推演：完成目标与转折。"
                ),
                "sceneText": f"{scene['heading']}\n\n第 {number} 集正文",
            })
            return StructuredModelResult(value, f"run-scene-{payload['sceneId']}")
        assert kwargs["phase"] == "screenplay_episode_metadata"
        value = kwargs["validate"]({
            "episodeNumber": number,
            "title": f"第 {number} 集",
            "executionSummary": f"完成第 {number} 集场景推进与连续性校验。",
            "continuitySummary": f"第 {number} 集连续性",
        })
        return StructuredModelResult(value, f"run-metadata-{number}")

    async def run_public_text(self, **kwargs):
        del kwargs
        return PublicModelResult(
            "第 1 至 2 集候选稿已经完成。可以在候选稿区域查看并继续编辑。",
            "run-final-response",
        )


class _CheckpointingToolCalls:
    def __init__(self, *, fail_once_key: str | None = None) -> None:
        self.fail_once_key = fail_once_key
        self.failed = False
        self.calls: list[tuple[str, str, ReasoningMode]] = []
        self.user_payloads: list[dict] = []
        self.system_instructions: list[str] = []

    async def run_candidate(self, **kwargs):
        context = kwargs["domain_context"]
        part_type = context.expected_part_type
        part_key = context.expected_part_key
        self.calls.append((
            part_type,
            part_key,
            kwargs.get("reasoning_mode", ReasoningMode.DEFAULT),
        ))
        self.user_payloads.append(dict(kwargs["user_payload"]))
        self.system_instructions.append(str(kwargs["system_instruction"]))
        if part_key == self.fail_once_key and not self.failed:
            self.failed = True
            raise ModelGatewayError(
                "fragment reached its output limit",
                code="model_output_truncated",
                retryable=True,
            )
        if part_type == "scene":
            template = kwargs.get("host_candidate_template")
            assert isinstance(template, dict)
            raw_scene_text = (
                f'{part_key} 的完整正文，保留英文引号 "台词" 和花括号 {{线索}}'
            )
            hosted = {**template, "sceneText": raw_scene_text}
            candidate = {
                "artifactId": f"artifact-{part_key}",
                "payload": hosted,
                "contentText": raw_scene_text,
            }
        elif part_type == "episode_metadata":
            assert kwargs.get("host_candidate_template") is None
            candidate = {
                "artifactId": f"artifact-metadata-{part_key}",
                "payload": {
                    "episodeNumber": int(part_key),
                    "title": f"第 {part_key} 集",
                    "executionSummary": "按场景顺序完成本集并保留连续性。",
                    "continuitySummary": f"第 {part_key} 集连续性",
                },
                "contentText": "",
            }
        elif part_type == "review_dimension":
            episode_text, dimension = part_key.split(":", 1)
            episode_number = int(episode_text)
            candidate = {
                "artifactId": f"artifact-review-{part_key}",
                "payload": {
                    "episodeNumber": episode_number,
                    "reviewDimension": dimension,
                    "title": f"第 {episode_number} 集 {dimension} 审阅",
                    "executionSummary": f"核对本集 {dimension} 维度。",
                    "contentJson": {
                        "verdict": (
                            "revise"
                            if episode_number == 1 and dimension == "continuity"
                            else "ready"
                        ),
                        "issues": ([{
                            "id": "issue-1",
                            "severity": "major",
                            "description": "场景转折需要更明确。",
                            "sceneIds": [f"scene-{episode_number}"],
                        }] if episode_number == 1 and dimension == "continuity" else []),
                    },
                },
                "contentText": f"第 {episode_number} 集 {dimension} 审阅正文",
            }
        elif part_type == "document_section" and part_key.startswith("episode-"):
            episode_number = int(part_key.removeprefix("episode-"))
            candidate = {
                "artifactId": f"artifact-scene-list-{part_key}",
                "payload": {
                    "sectionKey": part_key,
                    "title": f"第 {part_key} 集场景表",
                    "executionSummary": "按本集结构目标规划场景推进。",
                    "contentJson": {"scenes": [{
                        "id": f"scene-{episode_number}",
                        "episodeNumber": episode_number,
                        "heading": "咖啡馆·夜",
                        "objective": "确认对方来意",
                        "conflict": "双方互不信任",
                        "turn": "旧证物出现",
                        "synopsis": "人物在试探中发现共同线索。",
                    }]},
                },
                "contentText": f"第 {part_key} 集场景表正文",
            }
        elif part_type == "document_section":
            candidate = {
                "artifactId": f"artifact-document-{part_key}",
                "payload": {
                    "sectionKey": part_key,
                    "title": part_key,
                    "executionSummary": f"完成 {part_key} 章节。",
                    "contentJson": {part_key: {"summary": f"{part_key} 内容"}},
                },
                "contentText": f"## {part_key}\n\n{part_key} 内容",
            }
        else:
            raise AssertionError(f"unexpected part type: {part_type}")
        validator = kwargs.get("validate_candidate")
        if validator is not None:
            candidate = dict(validator(candidate))
        return ScreenplayCandidateRunResult(
            run_id=f"run-{part_type}-{part_key}-{len(self.calls)}",
            candidate=candidate,
        )


class _IncrementalEpisodeContext:
    async def episode_manifest(self, project_id, episode_number):
        assert project_id and episode_number == 1
        return {
            "sceneListId": "scene-list-head",
            "sceneIds": ("scene-1", "scene-2"),
        }

    async def episode_writing_context(
        self,
        project_id,
        episode_number,
        *,
        draft_revision_id=None,
    ):
        assert project_id and episode_number == 1
        assert draft_revision_id == "draft-head"
        return {
            "sceneListId": "scene-list-head",
            "scenePlans": {
                "scene-1": {"id": "scene-1", "objective": "建立危机"},
                "scene-2": {"id": "scene-2", "objective": "完成转折"},
            },
            "currentDraftScenes": {
                "scene-1": "scene-1 的旧稿",
                "scene-2": "scene-2 的旧稿",
            },
            "previousEpisodeContinuity": None,
            "reviewRevisionId": "review-head",
            "reviewIssues": [{
                "id": "issue-1",
                "severity": "major",
                "description": "本集需要统一格式并压缩篇幅。",
                "relatedSceneIds": ["scene-1"],
                "crossEpisodeSceneIds": [],
            }],
            "acceptedGuidance": {
                "creativeBrief": {"fields": {"format": "竖屏短剧"}},
                "structureEpisode": {"number": 1, "summary": "危机出现"},
            },
        }


class _EvidenceCheckpointOnlyContext:
    def __getattr__(self, name):
        raise AssertionError(f"generation refetched evidence via {name}")


async def test_formal_generation_consumes_evidence_without_refetching_context(
    temp_db: DatabaseConnection,
):
    _, workspace, session = await _project_and_session(temp_db)
    tool_calls = _CheckpointingToolCalls()
    executor = ScreenplayTaskModelCalls(
        temp_db,
        tool_calling_service=tool_calls,  # type: ignore[arg-type]
    )
    executor._context = _EvidenceCheckpointOnlyContext()
    evidence = {
        "episodeNumber": 1,
        "manifest": {
            "sceneListId": "scene-list-head",
            "sceneIds": ("scene-1", "scene-2"),
        },
        "writingContext": {
            "sceneListId": "scene-list-head",
            "scenePlans": {
                "scene-1": {"id": "scene-1", "objective": "建立危机"},
                "scene-2": {"id": "scene-2", "objective": "完成转折"},
            },
            "currentDraftScenes": {},
            "previousEpisodeContinuity": None,
            "reviewRevisionId": None,
            "reviewIssues": [],
            "acceptedGuidance": {},
        },
        "acceptedDeliverables": [],
    }
    task: dict[str, object] = {
        "id": "task-formal-evidence-checkpoint",
        "projectId": workspace["project"]["id"],
        "sessionId": session["id"],
        "turnId": "turn-formal-evidence-checkpoint",
        "targetRole": "screenplayDraft",
        "units": [
            {
                "id": "evidence:1",
                "kind": "collect_evidence",
                "status": "completed",
                "input": {"episodeNumber": 1},
                "output": {"evidence": evidence, "evidenceReceipt": "receipt-1"},
            },
            {
                "id": "draft:1:scene-1",
                "kind": "generate_draft_scene",
                "status": "pending",
                "dependsOn": ["evidence:1"],
                "input": {
                    "episodeNumber": 1,
                    "sceneId": "scene-1",
                    "sceneIds": ["scene-1", "scene-2"],
                    "instruction": "创作第一集",
                    "baseRevisionId": None,
                },
            },
            {
                "id": "draft:1:scene-2",
                "kind": "generate_draft_scene",
                "status": "pending",
                "dependsOn": ["draft:1:scene-1"],
                "input": {
                    "episodeNumber": 1,
                    "sceneId": "scene-2",
                    "sceneIds": ["scene-1", "scene-2"],
                    "instruction": "创作第一集",
                    "baseRevisionId": None,
                },
            },
            {
                "id": "episode:1:metadata",
                "kind": "generate_episode_metadata",
                "status": "pending",
                "dependsOn": ["draft:1:scene-2"],
                "input": {
                    "episodeNumber": 1,
                    "sceneIds": ["scene-1", "scene-2"],
                },
            },
            {
                "id": "episode:1:validation",
                "kind": "validate_manifest_part",
                "status": "pending",
                "dependsOn": ["episode:1:metadata"],
                "input": {
                    "validationKind": "draft_episode",
                    "episodeNumber": 1,
                    "sceneIds": ["scene-1", "scene-2"],
                },
            },
        ],
    }

    units = task["units"]
    assert isinstance(units, list)
    for index in (1, 2, 3):
        output = await executor.execute(
            task=task,
            unit=units[index],
            runtime=object(),
        )
        units[index].update({"status": "completed", "output": output})
    validated = await executor.execute(
        task=task,
        unit=units[4],
        runtime=object(),
    )

    assert validated["episodeDraft"]["sceneIds"] == ["scene-1", "scene-2"]
    assert len(validated["validationReceipt"]) == 64
    assert [key for _, key, _ in tool_calls.calls] == ["scene-1", "scene-2", "1"]


async def test_scene_part_truncation_does_not_replay_or_advance_other_parts(
    temp_db: DatabaseConnection,
):
    _, workspace, session = await _project_and_session(temp_db)
    tool_calls = _CheckpointingToolCalls(fail_once_key="scene-2")
    executor = ScreenplayTaskModelCalls(
        temp_db,
        tool_calling_service=tool_calls,  # type: ignore[arg-type]
    )
    executor._context = _EvidenceCheckpointOnlyContext()
    evidence = {
        "manifest": {"sceneListId": "scene-list-head"},
        "writingContext": {
            "scenePlans": {
                "scene-1": {"id": "scene-1", "objective": "建立危机"},
                "scene-2": {"id": "scene-2", "objective": "完成转折"},
            },
            "currentDraftScenes": {},
            "reviewIssues": [],
        },
    }
    task = {
        "id": "task-visible-scene-parts",
        "projectId": workspace["project"]["id"],
        "sessionId": session["id"],
        "turnId": "turn-visible-scene-parts",
        "targetRole": "screenplayDraft",
        "units": [{
            "id": "evidence:1",
            "kind": "collect_evidence",
            "status": "completed",
            "input": {"episodeNumber": 1},
            "output": {"evidence": evidence},
        }, {
            "id": "draft:1:scene-1",
            "kind": "generate_draft_scene",
            "status": "completed",
            "input": {
                "episodeNumber": 1,
                "sceneId": "scene-1",
                "sceneIds": ["scene-1", "scene-2"],
            },
            "output": {
                "episodeNumber": 1,
                "sceneId": "scene-1",
                "sceneText": "scene-1 正文",
                "processSummary": "场景 scene-1 推演：建立危机。",
                "sceneListId": "scene-list-head",
            },
        }],
    }
    unit = {
        "id": "draft:1:scene-2",
        "kind": "generate_draft_scene",
        "dependsOn": ["draft:1:scene-1"],
        "input": {
            "episodeNumber": 1,
            "sceneId": "scene-2",
            "sceneIds": ["scene-1", "scene-2"],
            "instruction": "创作第一集",
            "baseRevisionId": "draft-head",
        },
    }
    task["units"].append(unit)

    with pytest.raises(ModelGatewayError, match="output limit"):
        await executor.execute(
            task=task,
            unit=unit,
            runtime=object(),
        )
    assert [key for _, key, _ in tool_calls.calls] == ["scene-2"]
    assert task["units"][1]["output"]["sceneText"] == "scene-1 正文"
    assert unit.get("output") is None


class _IncrementalReviewContext:
    async def available_episode_numbers(self, project_id, **kwargs):
        assert project_id and kwargs["draft_revision_id"] == "draft-head"
        return {"draft": (1, 2), "sceneList": (1, 2), "remaining": ()}

    async def episode_context(self, project_id, episode_number, **kwargs):
        assert project_id and kwargs["draft_revision_id"] == "draft-head"
        return {
            "episode": {
                "episodeNumber": episode_number,
                "scenes": [{
                    "id": f"scene-{episode_number}",
                    "objective": "核对真实正文",
                }],
            },
            "previousEpisode": (
                {"continuitySummary": "上一集连续性"}
                if episode_number > 1 else None
            ),
            "currentDraft": {
                "episodeNumber": episode_number,
                "sceneIds": [f"scene-{episode_number}"],
                "sceneTexts": [{
                    "sceneId": f"scene-{episode_number}",
                    "contentText": f"第 {episode_number} 集真实正文",
                }],
            },
        }


async def test_review_dimension_parts_aggregate_host_side(
    temp_db: DatabaseConnection,
):
    _, workspace, session = await _project_and_session(temp_db)
    tool_calls = _CheckpointingToolCalls()
    executor = ScreenplayTaskModelCalls(
        temp_db,
        tool_calling_service=tool_calls,  # type: ignore[arg-type]
    )
    executor._context = _EvidenceCheckpointOnlyContext()
    review_input = {
        "contractVersion": 2,
        "draftRevisionId": "draft-head",
        "episodeNumber": 1,
        "sceneIds": ["scene-1"],
        "draftContentText": "第 1 集真实正文",
        "scenePlan": {"scenes": [{"id": "scene-1"}]},
        "requiredContext": {"previousEpisode": None},
        "contentDigest": "digest-episode-1",
    }
    task = {
        "id": "task-review-dimensions",
        "projectId": workspace["project"]["id"],
        "sessionId": session["id"],
        "turnId": "turn-review-dimensions",
        "targetRole": "review",
        "units": [{
            "id": "review-input:1",
            "kind": "collect_evidence",
            "status": "completed",
            "input": {"episodeNumber": 1, "evidenceKind": "review_input"},
            "output": {"evidence": {"reviewInput": review_input}},
        }],
    }
    for dimension in REVIEW_DIMENSIONS:
        unit = {
            "id": f"review:1:{dimension}",
            "kind": "generate_review_dimension",
            "status": "pending",
            "dependsOn": ["review-input:1"],
            "input": {
                "episodeNumber": 1,
                "sceneIds": ["scene-1"],
                "reviewDimension": dimension,
                "reviewedDraftId": "draft-head",
                "instruction": "审阅全剧",
            },
        }
        task["units"].append(unit)
        output = await executor.execute(
            task=task,
            unit=unit,
            runtime=object(),
        )
        unit.update({"status": "completed", "output": output})
    validation = {
        "id": "review:1:validation",
        "kind": "validate_manifest_part",
        "status": "pending",
        "dependsOn": [f"review:1:{value}" for value in REVIEW_DIMENSIONS],
        "input": {
            "validationKind": "review_episode",
            "episodeNumber": 1,
        },
    }
    task["units"].append(validation)
    result = await executor.execute(
        task=task,
        unit=validation,
        runtime=object(),
    )

    assert result["contentJson"]["reviewDimensions"] == list(REVIEW_DIMENSIONS)
    assert result["contentJson"]["verdict"] == "revise"
    assert result["contentJson"]["issues"][0]["id"] == (
        "episode-1:continuity:issue-1"
    )
    assert len(result["sourceRunIds"]) == 5
    review_input = tool_calls.user_payloads[0]["reviewInput"]
    assert review_input["contractVersion"] == 2
    assert review_input["draftRevisionId"] == "draft-head"
    assert review_input["episodeNumber"] == 1
    assert review_input["sceneIds"] == ["scene-1"]
    assert "第 1 集真实正文" in review_input["draftContentText"]
    assert review_input["scenePlan"]["scenes"][0]["id"] == "scene-1"
    assert review_input["contentDigest"] == "digest-episode-1"
    assert len(tool_calls.user_payloads) == len(REVIEW_DIMENSIONS)
    assert {
        payload["reviewInput"]["contentDigest"]
        for payload in tool_calls.user_payloads
    } == {"digest-episode-1"}
    assert all(
        payload["reviewedDraftId"] == "draft-head"
        and payload["reviewInput"]["draftRevisionId"] == "draft-head"
        and "previousReview" not in payload
        and "acceptedReview" not in payload
        and "reviewReport" not in payload
        for payload in tool_calls.user_payloads
    )
    assert "按需调用工具读取" not in tool_calls.system_instructions[0]


async def test_review_dimension_failure_stays_a_failed_part_not_a_finding(
    temp_db: DatabaseConnection,
):
    _, workspace, session = await _project_and_session(temp_db)
    tool_calls = _CheckpointingToolCalls(fail_once_key="1:dialogue")
    executor = ScreenplayTaskModelCalls(
        temp_db,
        tool_calling_service=tool_calls,  # type: ignore[arg-type]
    )
    executor._context = _EvidenceCheckpointOnlyContext()
    task = {
        "id": "task-failed-review-dimension",
        "projectId": workspace["project"]["id"],
        "sessionId": session["id"],
        "turnId": "turn-failed-review-dimension",
        "targetRole": "review",
        "units": [{
            "id": "review-input:1",
            "kind": "collect_evidence",
            "status": "completed",
            "input": {"episodeNumber": 1},
            "output": {"evidence": {"reviewInput": {
                "contractVersion": 2,
                "draftRevisionId": "draft-head",
                "episodeNumber": 1,
                "sceneIds": ["scene-1"],
                "draftContentText": "真实正文",
                "scenePlan": {"scenes": [{"id": "scene-1"}]},
                "contentDigest": "digest-1",
            }}},
        }],
    }
    unit = {
        "id": "review:1:dialogue",
        "kind": "generate_review_dimension",
        "dependsOn": ["review-input:1"],
        "input": {
            "episodeNumber": 1,
            "sceneIds": ["scene-1"],
            "reviewDimension": "dialogue",
            "reviewedDraftId": "draft-head",
            "instruction": "审阅对白",
        },
    }
    task["units"].append(unit)

    with pytest.raises(ModelGatewayError, match="output limit"):
        await executor.execute(
            task=task,
            unit=unit,
            runtime=object(),
        )

    assert [key for _, key, _ in tool_calls.calls] == ["1:dialogue"]
    assert unit.get("output") is None


async def test_scene_list_uses_visible_episode_sections_and_host_validation(
    temp_db: DatabaseConnection,
):
    _, workspace, session = await _project_and_session(temp_db)
    tool_calls = _CheckpointingToolCalls()
    executor = ScreenplayTaskModelCalls(
        temp_db,
        tool_calling_service=tool_calls,  # type: ignore[arg-type]
    )
    task = {
        "id": "task-scene-list-sections",
        "projectId": workspace["project"]["id"],
        "sessionId": session["id"],
        "turnId": "turn-scene-list-sections",
        "targetRole": "sceneList",
        "units": [{
            "id": "document:evidence",
            "kind": "collect_evidence",
            "status": "completed",
            "input": {"targetRole": "sceneList"},
            "output": {"evidence": {"acceptedDeliverables": [{
                "role": "structure",
                "revisionId": "structure-head",
                "content": {"episodes": [{"number": 1}, {"number": 2}]},
            }]}},
        }],
    }
    for number in (1, 2):
        unit = {
            "id": f"section:sceneList:episode-{number}",
            "kind": "generate_document_section",
            "status": "pending",
            "dependsOn": ["document:evidence"],
            "input": {
                "targetRole": "sceneList",
                "sectionKey": f"episode-{number}",
                "instruction": "生成场景表",
            },
        }
        task["units"].append(unit)
        output = await executor.execute(
            task=task,
            unit=unit,
            runtime=object(),
        )
        unit.update({"status": "completed", "output": output})
    validation = {
        "id": "document:validation",
        "kind": "validate_manifest_part",
        "status": "pending",
        "dependsOn": [
            "section:sceneList:episode-1",
            "section:sceneList:episode-2",
        ],
        "input": {"validationKind": "document", "targetRole": "sceneList"},
    }
    task["units"].append(validation)
    result = await executor.execute(
        task=task,
        unit=validation,
        runtime=object(),
    )

    assert result["contentJson"]["structureId"] == "structure-head"
    assert [scene["episodeNumber"] for scene in result["contentJson"]["scenes"]] == [
        1,
        2,
    ]
    assert len(result["sourceRunIds"]) == 2


async def test_production_resolver_and_executor_publish_one_native_candidate(
    temp_db: DatabaseConnection,
):
    projects, workspace, session = await _project_and_session(temp_db)
    project_id = workspace["project"]["id"]
    await _install_head(temp_db, project_id, "creativeBrief", {
        "documentKind": "creative_brief",
        "fields": {"approach": "人物驱动", "premise": "意外重逢"},
    })
    await _install_head(temp_db, project_id, "structure", {
        "documentKind": "episode_outline",
        "episodes": [
            {"number": 1, "id": "episode-1", "title": "重逢"},
            {"number": 2, "id": "episode-2", "title": "追问"},
            {"number": 3, "id": "episode-3", "title": "选择"},
        ],
    })
    scene_list_id = await _install_head(temp_db, project_id, "sceneList", {
        "documentKind": "scene_list",
        "scenes": [
            {"id": "scene-1", "episodeNumber": 1, "heading": "咖啡馆·夜"},
            {"id": "scene-2", "episodeNumber": 2, "heading": "车站·晨"},
            {"id": "scene-3", "episodeNumber": 3, "heading": "码头·黄昏"},
        ],
    })
    intent = ScreenplayIntent(
        action=ScreenplayIntentAction.CREATE,
        instruction="把接下来两集写完，每集结尾留下新的问题",
        scope=ScreenplayIntentScope(
            kind=ScreenplayScopeKind.NEXT_EPISODES,
            count=2,
        ),
    )
    executor = ScreenplayTaskModelCalls(
        temp_db,
        composition=object(),
    )
    executor._models = _StructuredDraftModels()

    def unit_executor_factory(runtime):
        unit = ScreenplayTaskUnitExecutor(
            temp_db,
            runtime=runtime,
            composition=object(),
        )
        unit._delegate = executor
        return unit

    service = ScreenplayAgentService(
        temp_db,
        owner_id="production-screenplay-task-test",
        planner=_Planner(intent),
        resolver=SqliteScreenplayTaskResolver(temp_db),
        unit_executor_factory=unit_executor_factory,
        projects=projects,
    )
    request = _request(session["id"], "把接下来两集写完，每集结尾留下新的问题")
    turn = await service.submit_turn(
        command_id="production-next-two",
        project_id=project_id,
        request=request,
    )

    await service.execute_turn(turn["id"], request.runtime)

    snapshot = await service.get_snapshot(
        project_id=project_id,
        session_id=session["id"],
    )
    task = snapshot["tasks"][0]
    assert task["status"] == "completed", json.dumps(
        task,
        ensure_ascii=False,
        default=str,
    )
    result_revision = task["resultRevision"]
    assert result_revision["id"] == task["resultRevisionId"]
    assert result_revision["role"] == "screenplayDraft"
    assert result_revision["revisionNo"] == 1
    assert result_revision["summary"]["proposalKind"] == "scene_draft"
    assert result_revision["summary"]["title"] == "第 1–2 集剧本"
    assert result_revision["summary"]["partCount"] == 3
    assert result_revision["agentTaskId"] == task["id"]
    assert result_revision["status"] == "candidate"
    assert "parts" not in result_revision
    assert "contentText" not in result_revision
    assert "contentJson" not in result_revision
    revision = await projects.get_revision(task["resultRevisionId"], view="full")
    assert revision["createdBy"] == "agent"
    assert revision["parts"][0]["payload"]["sceneListId"] == scene_list_id
    assert [part["key"] for part in revision["parts"]] == ["main", "1", "2"]
    assert await temp_db.fetch_one(
        "SELECT agent_task_id FROM screenplay_revisions WHERE id = ?",
        [revision["id"]],
    ) == {"agent_task_id": task["id"]}
    unit_rows = await temp_db.fetch_all(
        "SELECT unit_id, output_ref, artifact_digest, validation_receipt_json "
        "FROM ai_agent_long_task_units WHERE task_id = ? ORDER BY position",
        [task["id"]],
    )
    assert all(
        str(row["output_ref"]).startswith("screenplay-part-artifact://")
        and str(row["artifact_digest"]).strip()
        and json.loads(str(row["validation_receipt_json"]))["valid"] is True
        for row in unit_rows
    )
    evidence = await ScreenplayPartArtifactQuery(temp_db).require(
        str(unit_rows[0]["output_ref"])
    )
    assert set(evidence["evidenceDescriptor"]) >= {
        "sourceRevisionRefs",
        "sceneListRevisionId",
        "episodeNumber",
    }
    assert "acceptedDeliverables" not in evidence["evidenceDescriptor"]
    assert "writingContext" not in evidence["evidenceDescriptor"]
    assert await temp_db.fetch_one(
        "SELECT name FROM sqlite_master WHERE type = 'table' "
        "AND name = 'screenplay_agent_task_outputs'"
    ) is None
    continuation = await SqliteScreenplayTaskResolver(temp_db).resolve(
        workspace=await projects.get_workspace(project_id),
        intent=ScreenplayIntent(
            action=ScreenplayIntentAction.CREATE,
            instruction="继续创作下一集",
            scope=ScreenplayIntentScope(
                kind=ScreenplayScopeKind.NEXT_EPISODES,
                count=1,
            ),
        ),
    )
    assert continuation.base_revision_id == revision["id"]
    assert continuation.episode_numbers == (3,)

    continuation_service = ScreenplayAgentService(
        temp_db,
        owner_id="production-screenplay-continuation-test",
        planner=_Planner(ScreenplayIntent(
            action=ScreenplayIntentAction.CREATE,
            instruction="继续创作下一集",
            scope=ScreenplayIntentScope(
                kind=ScreenplayScopeKind.NEXT_EPISODES,
                count=1,
            ),
        )),
        resolver=SqliteScreenplayTaskResolver(temp_db),
        unit_executor_factory=unit_executor_factory,
        projects=projects,
    )
    continuation_turn = await continuation_service.submit_turn(
        command_id="production-next-one-after-candidate",
        project_id=project_id,
        request=_request(session["id"], "继续创作下一集"),
    )
    await continuation_service.execute_turn(
        continuation_turn["id"],
        _request(session["id"], "继续创作下一集").runtime,
    )
    continued_snapshot = await continuation_service.get_snapshot(
        project_id=project_id,
        session_id=session["id"],
    )
    assert [
        item["resultRevision"]["id"]
        for item in continued_snapshot["tasks"]
    ] == [
        revision["id"],
        continued_snapshot["tasks"][1]["resultRevisionId"],
    ]
    assert all(
        "parts" not in item["resultRevision"]
        for item in continued_snapshot["tasks"]
    )
    continued_revision = await projects.get_revision(
        continued_snapshot["tasks"][1]["resultRevisionId"],
        view="full",
    )
    assert continued_revision["parentRevisionId"] == revision["id"]
    assert [part["key"] for part in continued_revision["parts"]] == [
        "main",
        "1",
        "2",
        "3",
    ]

    await service.truncate_from_turn(turn["id"])
    assert await temp_db.fetch_one(
        "SELECT id FROM screenplay_revisions WHERE id = ?",
        [revision["id"]],
    ) is None
