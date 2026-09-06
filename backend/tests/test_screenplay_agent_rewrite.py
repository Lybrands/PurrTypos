from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio

import application.model_runtime as model_runtime
import application.screenplay_tool_calling as screenplay_tool_calling
import domains.screenplay_agent.contracts as screenplay_contracts
from purra.contracts import (
    AgentMessage,
    AgentRunResult,
    AgentRunRequest,
    ContextBudget,
    DomainContext,
    RunCreateParams,
    RunStatus,
    ModelRequest,
    ReasoningMode,
    StepExecutor,
    StepStatus,
    StepType,
    ExecutionPlan,
    TaskSpec,
    TaskStep,
    ToolRiskLevel,
)
from purra.artifacts import ArtifactStatus
from purra.events import AgentEvent, CoreEventType
from purra.errors import ContractViolationError, ModelGatewayError
from purra.ports import RunCommit
from purra.cancellation import OperationCanceled
from purra.json_values import thaw_json_mapping
from purra.long_tasks import (
    LongTaskCreateCommand,
    LongTaskUnitResult,
    LongTaskUnitSpec,
)
from purra.recovery import (
    FailureCategory,
    FailureDisposition,
    FailureScope,
    decide_failure,
)
from application.screenplay_agent_service import (
    ScreenplayAgentService,
    _ScreenplayTurnRunLifecycle,
    _execution_recipe_from_metadata,
    _task_failure,
)
from application.screenplay_task_resolver import ResolvedScreenplayTask
from application.screenplay_agent_profile import (
    ScreenplayAgentProfile,
    _ScreenplayCheckpointObserver,
    _checkpoint_input,
    _ready_checkpoint_keys,
)
from application.composition_factory import create_agent_composition
from application.model_runtime import model_request_from_runtime
from application.screenplay_agent_stream import (
    ScreenplayCanonicalOutputQuery,
    _with_screenplay_tool_display_names,
)
from application.screenplay_agent_task_executor import (
    ScreenplayTaskModelCalls,
    ScreenplayTaskUnitExecutor,
    _document_section_tool_instruction,
    _validate_deliverable,
    _validate_document_parts,
    _requires_run,
    _unit_result,
    normalize_screenplay_candidate,
    StructurePartSplit,
)
from application.screenplay_candidate_assembler import (
    aggregate_review_validations,
)
from application.screenplay_agent_context import ScreenplayAgentContextQuery
from infrastructure.screenplay.agent_root_completion_projector import (
    ScreenplayAgentRootCompletionProjector,
)
from application.screenplay_part_artifacts import (
    ScreenplayPartArtifactQuery,
    ValidatedPartArtifactRef,
)
from application.screenplay_manifest_compiler import (
    REVIEW_DIMENSIONS,
    compile_screenplay_manifest,
)
from application.screenplay_part_contracts import (
    PART_CONTRACTS,
    resolve_screenplay_part_contract,
    screenplay_max_generated_units,
    screenplay_task_budget_limits,
)
from application.screenplay_checkpoint_planning import (
    CHECKPOINT_PLAN_PROTOCOL,
    ScreenplayCheckpointInput,
    ScreenplayCheckpointOutcome,
    ScreenplayCheckpointPlanner,
    ScreenplayCheckpointStateError,
    SqliteScreenplayCheckpointRepository,
    _canonical_root_plan,
    _planning_payload,
    _plan_mapping,
    parse_persisted_plan,
    plan_digest,
)
from application.screenplay_task_resolver import SqliteScreenplayTaskResolver
from application.screenplay_structured_call import (
    PublicModelResult,
    StructuredModelResult,
)
from application.screenplay_tool_calling import (
    ScreenplayCandidateRunResult,
    ScreenplayToolCallingService,
)
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
from infrastructure.persistence.sqlite_run_repository import SqliteRunRepository
from infrastructure.persistence.sqlite_agent_output_repository import (
    SqliteAgentOutputRepository,
)
from domains.screenplay_agent.adapter import (
    ScreenplayExecutionStateFactory,
    ScreenplayToolLoopPolicy,
)
from schemas.screenplay_agent import SubmitScreenplayAgentTurnRequest
from schemas.screenplay_v2 import CreateScreenplayV2ProjectRequest
from purra.task_admission import (
    ExecutionMode,
    LongTaskExecutionResult,
    LongTaskExecutionStatus,
)
from purra.testing import assert_task_orchestration_conforms


pytestmark = pytest.mark.asyncio


async def test_screenplay_part_contracts_do_not_define_provider_token_budgets():
    assert PART_CONTRACTS
    assert all(
        not hasattr(contract, "output_token_cap")
        for contract in PART_CONTRACTS.values()
    )
    assert all(
        contract.tool_profile != "all"
        for contract in PART_CONTRACTS.values()
    )
    assert all(
        not hasattr(contract, "reasoning_mode")
        for contract in PART_CONTRACTS.values()
    )


@pytest.mark.parametrize(
    ("role", "kind", "unit_input", "expected_key"),
    [
        ("screenplayDraft", "generate_draft_scene", {}, "draft_scene"),
        (
            "screenplayDraft",
            "generate_episode_metadata",
            {},
            "episode_metadata",
        ),
        ("review", "generate_review_dimension", {}, "review_dimension"),
        (
            "sourceAnalysis",
            "generate_document_section",
            {"sectionKey": "characters"},
            "source_analysis.characters",
        ),
        (
            "sourceAnalysis",
            "generate_document_section",
            {
                "sectionKey": "source_digest:chapter:chapter-1",
                "sourceChapterDigest": True,
                "chapterId": "chapter-1",
            },
            "source_analysis.chapter_digest",
        ),
        (
            "sourceAnalysis",
            "generate_document_section",
            {
                "sectionKey": "source_digest:reduce:1:1",
                "sourceDigestReduction": True,
            },
            "source_analysis.digest_reduction",
        ),
        (
            "creativeBrief",
            "generate_document_section",
            {"sectionKey": "premise"},
            "creative_brief.premise",
        ),
        (
            "structure",
            "generate_document_section",
            {"sectionKey": "series_arc:index", "seriesArcIndex": True},
            "structure.series_arc_index",
        ),
        (
            "structure",
            "generate_document_section",
            {
                "sectionKey": "series_arc:phase:setup",
                "documentSectionKey": "series_arc",
                "phaseKey": "setup",
            },
            "structure.series_arc_phase",
        ),
        (
            "structure",
            "generate_document_section",
            {"sectionKey": "episode_plan:index", "episodePlanIndex": True},
            "structure.episode_plan_index",
        ),
        (
            "structure",
            "generate_document_section",
            {
                "sectionKey": "episode_plan:episode-7",
                "documentSectionKey": "episode_plan",
                "episodeNumber": 7,
            },
            "structure.episode_plan_fragment",
        ),
        (
            "structure",
            "generate_document_section",
            {
                "sectionKey": "character_arcs:index",
                "characterArcsIndex": True,
            },
            "structure.character_arcs_index",
        ),
        (
            "structure",
            "generate_document_section",
            {
                "sectionKey": "character_arcs:character:linyue",
                "documentSectionKey": "character_arcs",
                "characterKey": "linyue",
            },
            "structure.character_arc_fragment",
        ),
        (
            "sceneList",
            "generate_document_section",
            {"sectionKey": "episode-7"},
            "scene_list_episode",
        ),
        ("creativeBrief", "compose_final_response", {}, "final_response"),
    ],
)
async def test_screenplay_part_contract_resolution_is_unique(
    role,
    kind,
    unit_input,
    expected_key,
):
    assert resolve_screenplay_part_contract(
        role,
        kind,
        unit_input,
    ).key == expected_key


async def test_screenplay_part_contract_resolution_fails_closed():
    with pytest.raises(ValueError, match="screenplay_part_contract_unknown"):
        resolve_screenplay_part_contract(
            "creativeBrief",
            "generate_document_section",
            {"sectionKey": "invented"},
        )


async def test_creative_brief_section_instructions_match_document_validator_fields():
    positioning = _document_section_tool_instruction(
        "creativeBrief",
        "positioning",
    )
    premise = _document_section_tool_instruction("creativeBrief", "premise")

    assert '"contentJson":{"fields":{"approach":' in positioning
    assert '"contentJson":{"fields":{"premise":' in premise

    with pytest.raises(ValueError, match="screenplay_part_contract_unknown"):
        _document_section_tool_instruction("creativeBrief", "invented")


def _creative_brief_candidate(section_key: str, content_json: dict):
    return {
        "payload": {
            "sectionKey": section_key,
            "title": f"{section_key} 标题",
            "contentJson": content_json,
        },
        "contentText": f"## {section_key}",
    }


@pytest.mark.parametrize(
    ("section_key", "content_json"),
    [
        ("positioning", {"fields": {
            "approach": "人物驱动",
            "format": "竖屏短剧",
            "audience": "年轻观众",
            "tone": "悬疑而温暖",
        }}),
        ("premise", {"fields": {
            "premise": "旧友在停电夜重逢并寻找真相。",
            "centralConflict": "合作需求与旧日背叛冲突。",
            "dramaticQuestion": "他们能否在来电前重建信任？",
        }}),
        ("characters", {"coreCharacters": [{
            "key": "lead",
            "name": "林月",
            "function": "推动调查",
            "desire": "查明真相",
            "obstacle": "无法信任旧友",
            "changeDirection": "从独行转向合作",
        }]}),
        ("world", {
            "worldRules": ["停电期间所有电子记录都会失真"],
            "visualIdentity": "冷蓝夜景与暖色手电光对照",
        }),
        ("adaptation_rules", {"adaptationRules": [{
            "rule": "关键线索必须通过人物行动呈现",
            "reason": "避免解释性对白",
        }]}),
    ],
)
async def test_creative_brief_section_candidates_have_strict_shapes(
    section_key,
    content_json,
):
    normalized = normalize_screenplay_candidate(
        {
            "protocol": "purrtypos.screenplay.candidate-validation/v1",
            "kind": "creative_brief_section",
            "sectionKey": section_key,
        },
        _creative_brief_candidate(section_key, content_json),
    )

    assert normalized["payload"]["contentJson"] == content_json


async def test_creative_brief_section_rejects_extra_fields_and_character_overflow():
    contract = {
        "protocol": "purrtypos.screenplay.candidate-validation/v1",
        "kind": "creative_brief_section",
        "sectionKey": "characters",
    }
    character = {
        "key": "lead",
        "name": "林月",
        "function": "推动调查",
        "desire": "查明真相",
        "obstacle": "不信任旧友",
        "changeDirection": "转向合作",
    }
    with pytest.raises(ValueError, match="character fields"):
        normalize_screenplay_candidate(
            contract,
            _creative_brief_candidate(
                "characters",
                {"coreCharacters": [character], "episodes": []},
            ),
        )
    with pytest.raises(ValueError, match="count"):
        normalize_screenplay_candidate(
            contract,
            _creative_brief_candidate(
                "characters",
                {"coreCharacters": [
                    {**character, "key": f"lead-{index}"}
                    for index in range(13)
                ]},
            ),
        )


async def test_creative_brief_assembled_document_revalidates_the_complete_contract():
    content = {
        "fields": {
            "approach": "人物驱动",
            "format": "竖屏短剧",
            "audience": "年轻观众",
            "tone": "悬疑而温暖",
            "premise": "旧友在停电夜重逢并寻找真相。",
            "centralConflict": "合作需求与旧日背叛冲突。",
            "dramaticQuestion": "他们能否在来电前重建信任？",
        },
        "coreCharacters": [{
            "key": "lead",
            "name": "林月",
            "function": "推动调查",
            "desire": "查明真相",
            "obstacle": "无法信任旧友",
            "changeDirection": "从独行转向合作",
        }],
        "worldRules": ["停电期间所有电子记录都会失真"],
        "visualIdentity": "冷蓝夜景与暖色手电光对照",
        "adaptationRules": [{
            "rule": "关键线索必须通过人物行动呈现",
            "reason": "避免解释性对白",
        }],
    }
    validated = _validate_deliverable(
        "creativeBrief",
        {
            "title": "创作简报",
            "contentText": "完整简报",
            "contentJson": content,
        },
        structure_id=None,
        structure_episode_numbers=(),
        reviewed_draft_id=None,
    )

    assert validated["contentJson"]["documentKind"] == "creative_brief"
    with pytest.raises(ValueError, match="document fields"):
        _validate_deliverable(
            "creativeBrief",
            {
                "title": "创作简报",
                "contentText": "完整简报",
                "contentJson": {**content, "episodes": []},
            },
            structure_id=None,
            structure_episode_numbers=(),
            reviewed_draft_id=None,
        )


def _scene_list_candidate(
    episode_number: int,
    *,
    scene_id: str = "scene-1",
) -> dict:
    return {
        "payload": {
            "sectionKey": f"episode-{episode_number}",
            "title": f"第 {episode_number} 集场景表",
            "contentJson": {"scenes": [{
                "id": scene_id,
                "episodeNumber": episode_number,
                "heading": "咖啡馆·夜",
                "objective": "确认来意",
                "conflict": "双方互不信任",
                "turn": "旧证物出现",
                "synopsis": "试探中发现共同线索。",
            }]},
        },
        "contentText": f"第 {episode_number} 集场景表",
    }


async def test_late_stage_candidate_contracts_reject_extra_fields_and_review_overflow():
    protocol = "purrtypos.screenplay.candidate-validation/v1"
    scene_contract = {
        "protocol": protocol,
        "kind": "scene_list_fragment",
        "episodeNumber": 1,
    }
    with pytest.raises(ValueError, match="fragment fields"):
        candidate = _scene_list_candidate(1)
        candidate["payload"]["contentJson"]["episodes"] = []
        normalize_screenplay_candidate(scene_contract, candidate)

    metadata_contract = {
        "protocol": protocol,
        "kind": "episode_metadata",
        "episodeNumber": 1,
    }
    with pytest.raises(ValueError, match="metadata number"):
        normalize_screenplay_candidate(metadata_contract, {
            "payload": {
                "episodeNumber": 1,
                "title": "第一集",
                "continuitySummary": "人物取得证物。",
                "executionSummary": "不属于业务合同",
            },
            "contentText": "",
        })

    review_contract = {
        "protocol": protocol,
        "kind": "review_dimension",
        "episodeNumber": 1,
        "dimension": "continuity",
        "allowedSceneIds": ["scene-1"],
        "reviewedDraftId": "draft-1",
        "reviewedContentDigest": "a" * 64,
    }
    with pytest.raises(ValueError, match="exceeds scene count"):
        normalize_screenplay_candidate(review_contract, {
            "payload": {
                "episodeNumber": 1,
                "reviewDimension": "continuity",
                "title": "第一集连续性审阅",
                "contentJson": {
                    "verdict": "revise",
                    "issues": [{
                        "id": f"issue-{number}",
                        "severity": "major",
                        "description": f"问题 {number}",
                        "sceneIds": ["scene-1"],
                    } for number in (1, 2)],
                },
            },
            "contentText": "需要修订。",
        })


async def test_creative_brief_run_binds_exact_evidence_revision_scope(
    temp_db: DatabaseConnection,
):
    _, workspace, session = await _project_and_session(temp_db)

    class ToolCalls:
        context = None

        async def run_candidate(self, **kwargs):
            self.context = kwargs["domain_context"]
            candidate = normalize_screenplay_candidate(
                kwargs["candidate_validation_contract"],
                _creative_brief_candidate("premise", {"fields": {
                    "premise": "旧友在停电夜重逢并寻找真相。",
                    "centralConflict": "合作需求与旧日背叛冲突。",
                    "dramaticQuestion": "他们能否在来电前重建信任？",
                }}),
            )
            return ScreenplayCandidateRunResult("run-brief-premise", candidate)

    tool_calls = ToolCalls()
    executor = ScreenplayTaskModelCalls(
        temp_db,
        tool_calling_service=tool_calls,  # type: ignore[arg-type]
    )
    descriptor = {
        "projectId": workspace["project"]["id"],
        "targetRole": "creativeBrief",
        "sourceRevisionRefs": ["analysis-accepted", "brief-baseline"],
        "baseRevisionId": "brief-baseline",
        "acceptedRevisionIds": {
            "sourceAnalysis": "analysis-accepted",
            "creativeBrief": "brief-baseline",
        },
        "structureEpisodeNumbers": [],
    }
    unit = {
        "id": "section:creativeBrief:premise",
        "kind": "generate_document_section",
        "dependsOn": ["document:evidence"],
        "input": {
            "sectionKey": "premise",
            "instruction": "修订故事前提",
            "baseRevisionId": "brief-baseline",
        },
    }
    task = {
        "id": "task-brief-revision-scope",
        "projectId": workspace["project"]["id"],
        "sessionId": session["id"],
        "turnId": "turn-brief-revision-scope",
        "rootRunId": "root-brief-revision-scope",
        "targetRole": "creativeBrief",
        "units": [{
            "id": "document:evidence",
            "kind": "collect_evidence",
            "status": "completed",
            "input": {"targetRole": "creativeBrief"},
            "output": {"evidenceDescriptor": descriptor},
        }, unit],
    }

    await executor.execute(task=task, unit=unit, runtime=object())

    assert tool_calls.context.deliverable_revision_scope == {
        "sourceAnalysis": "analysis-accepted",
        "creativeBrief": "brief-baseline",
    }


async def test_structure_episode_index_and_fragment_candidates_are_bounded():
    protocol = "purrtypos.screenplay.candidate-validation/v1"
    index = normalize_screenplay_candidate(
        {"protocol": protocol, "kind": "structure_episode_plan_index"},
        {
            "payload": {
                "sectionKey": "episode_plan:index",
                "title": "分集索引",
                "contentJson": {"episodes": [
                    {
                        "number": 1,
                        "id": "ep01",
                        "title": "误入犬域",
                        "summary": "建立主角并进入异空间。",
                        "sourceChapterIds": ["chapter-1"],
                    },
                    {
                        "number": 2,
                        "id": "ep02",
                        "title": "绝境觉醒",
                        "summary": "完成首次能力爆发。",
                    },
                ]},
            },
            "contentText": "- 第 1 集\n- 第 2 集",
        },
    )
    assert [
        episode["number"]
        for episode in index["payload"]["contentJson"]["episodes"]
    ] == [1, 2]

    fragment = normalize_screenplay_candidate(
        {
            "protocol": protocol,
            "kind": "structure_episode_plan_fragment",
            "episodeNumber": 2,
            "episodeId": "ep02",
            "episodeTitle": "绝境觉醒",
        },
        {
            "payload": {
                "sectionKey": "episode_plan:episode-2",
                "title": "第 2 集：绝境觉醒",
                "contentJson": {"episodes": [{
                    "number": 2,
                    "id": "ep02",
                    "title": "绝境觉醒",
                    "summary": "林月在追杀中唤醒金鼓。",
                    "objective": "救出被围困的同伴。",
                    "conflict": "能力失控会伤害同伴。",
                    "turn": "林月主动接受金鼓。",
                    "hook": "苏文从梦中惊醒。",
                }]},
            },
            "contentText": "## 第 2 集：绝境觉醒",
        },
    )
    assert fragment["payload"]["contentJson"]["episodes"][0]["id"] == "ep02"

    with pytest.raises(ValueError, match="ordered and unique"):
        normalize_screenplay_candidate(
            {"protocol": protocol, "kind": "structure_episode_plan_index"},
            {
                "payload": {
                    "sectionKey": "episode_plan:index",
                    "title": "错误索引",
                    "contentJson": {"episodes": [{
                        "number": 2,
                        "id": "ep02",
                        "title": "跳号",
                        "summary": "不允许跳号。",
                    }]},
                },
                "contentText": "错误",
            },
        )


async def test_structure_series_and_character_candidates_are_bounded():
    protocol = "purrtypos.screenplay.candidate-validation/v1"
    series_index = normalize_screenplay_candidate(
        {"protocol": protocol, "kind": "structure_series_arc_index"},
        {
            "payload": {
                "sectionKey": "series_arc:index",
                "title": "全剧阶段索引",
                "contentJson": {"phases": [{
                    "key": "setup",
                    "title": "误入犬域",
                    "objective": "建立主角目标与异世界规则。",
                }]},
            },
            "contentText": "- 误入犬域",
        },
    )
    assert series_index["payload"]["contentJson"]["phases"][0]["key"] == (
        "setup"
    )

    phase = normalize_screenplay_candidate(
        {
            "protocol": protocol,
            "kind": "structure_series_arc_phase",
            "phaseKey": "setup",
            "phaseTitle": "误入犬域",
            "phaseObjective": "建立主角目标与异世界规则。",
        },
        {
            "payload": {
                "sectionKey": "series_arc:phase:setup",
                "title": "误入犬域",
                "contentJson": {"seriesArc": {"phases": [{
                    "key": "setup",
                    "title": "误入犬域",
                    "objective": "建立主角目标与异世界规则。",
                    "centralConflict": "回家与救人不可兼得。",
                    "turningPoint": "主角主动留下。",
                    "exitState": "团队正式结盟。",
                }]}},
            },
            "contentText": "## 误入犬域",
        },
    )
    assert phase["payload"]["contentJson"]["seriesArc"]["phases"][0][
        "centralConflict"
    ]

    character_index = normalize_screenplay_candidate(
        {"protocol": protocol, "kind": "structure_character_arcs_index"},
        {
            "payload": {
                "sectionKey": "character_arcs:index",
                "title": "核心人物索引",
                "contentJson": {"characters": [{
                    "key": "linyue",
                    "name": "林月",
                }]},
            },
            "contentText": "- 林月",
        },
    )
    assert character_index["payload"]["contentJson"]["characters"] == [{
        "key": "linyue",
        "name": "林月",
    }]

    character = normalize_screenplay_candidate(
        {
            "protocol": protocol,
            "kind": "structure_character_arc_fragment",
            "characterKey": "linyue",
            "characterName": "林月",
        },
        {
            "payload": {
                "sectionKey": "character_arcs:character:linyue",
                "title": "林月人物弧",
                "contentJson": {"characterArcs": [{
                    "key": "linyue",
                    "startState": "只想独自回家。",
                    "desire": "找到回归现实的方法。",
                    "turningEpisodes": ["ep01"],
                    "endState": "选择守护同伴。",
                }]},
            },
            "contentText": "## 林月",
        },
    )
    assert character["payload"]["contentJson"]["characterArcs"][0][
        "turningEpisodes"
    ] == ["ep01"]

    with pytest.raises(ValueError, match="cannot contain episode or scene detail"):
        normalize_screenplay_candidate(
            {
                "protocol": protocol,
                "kind": "structure_series_arc_phase",
                "phaseKey": "setup",
                "phaseTitle": "误入犬域",
                "phaseObjective": "建立主角目标与异世界规则。",
            },
            {
                "payload": {
                    "sectionKey": "series_arc:phase:setup",
                    "title": "错误阶段",
                    "contentJson": {"seriesArc": {"phases": [{
                        "key": "setup",
                        "title": "误入犬域",
                        "objective": "建立主角目标与异世界规则。",
                        "centralConflict": "冲突",
                        "turningPoint": "转折",
                        "exitState": "状态",
                        "episodes": [{"id": "ep01"}],
                    }]}},
                },
                "contentText": "错误",
            },
        )


def _checkpoint_unit(
    position: int,
    *,
    kind: str,
    status: str = "completed",
    episode_number: int | None = None,
):
    unit_input = {"validationKind": kind}
    if episode_number is not None:
        unit_input["episodeNumber"] = episode_number
    return SimpleNamespace(
        position=position,
        status=SimpleNamespace(value=status),
        metadata={
            "unitKind": "validate_manifest_part",
            "input": unit_input,
        },
    )


def _root_revision_payload(
    plan: ExecutionPlan,
    *,
    identity: str,
    digest: str | None = None,
) -> dict[str, object]:
    return {
        "title": plan.title,
        "goal": plan.goal,
        "status": "running",
        "taskSpec": plan.task_spec.to_mapping() if plan.task_spec else None,
        "steps": [{
            "id": step.id,
            "title": step.title,
            "type": step.type.value,
            "executor": step.executor.value,
            "status": step.status.value,
            "risk_level": step.risk_level.value if step.risk_level else None,
            "suggested_tools": list(step.suggested_tools),
            "depends_on": list(step.depends_on),
            "description": step.description,
            "result_summary": step.result_summary,
            "error": step.error,
        } for step in plan.steps],
        "planRevision": {
            "identity": identity,
            "digest": digest or plan_digest(plan),
        },
    }


async def test_checkpoint_boundaries_are_business_milestones_not_every_part():
    ordinary_part = SimpleNamespace(
        position=1,
        status=SimpleNamespace(value="completed"),
        metadata={"unitKind": "generate_draft_scene", "input": {}},
    )
    assert _ready_checkpoint_keys((ordinary_part,)) == ()
    assert _ready_checkpoint_keys((
        ordinary_part,
        _checkpoint_unit(2, kind="draft_episode", episode_number=4),
    )) == ("episode:4",)
    assert _ready_checkpoint_keys((
        _checkpoint_unit(3, kind="document"),
    )) == ("document:sections",)
    review_one = _checkpoint_unit(
        4,
        kind="review_episode",
        episode_number=1,
    )
    review_two_pending = _checkpoint_unit(
        5,
        kind="review_episode",
        status="pending",
        episode_number=2,
    )
    assert _ready_checkpoint_keys((review_one, review_two_pending)) == ()
    review_two_done = _checkpoint_unit(
        5,
        kind="review_episode",
        episode_number=2,
    )
    assert _ready_checkpoint_keys((review_one, review_two_done)) == (
        "review:aggregate",
    )


async def test_checkpoint_planner_receipts_expose_only_public_artifact_facts():
    plan = ExecutionPlan(
        title="创作",
        task_spec=_screenplay_task_spec(deliverable="screenplayDraft"),
        steps=_bound_steps("evidence", "create", "deliver"),
    )
    unit = SimpleNamespace(
        id="PRIVATE-UNIT-SENTINEL",
        status=SimpleNamespace(value="completed"),
        artifact_digest="sha256:" + "a" * 64,
        error_code=None,
        failure=None,
        metadata={
            "unitKind": "generate_document_section",
            "input": {
                "sectionKey": "characters",
                "instruction": "PROMPT-SENTINEL-DO-NOT-LEAK",
            },
        },
    )
    checkpoint = _checkpoint_input(
        SimpleNamespace(
            id="PRIVATE-TASK-SENTINEL",
            created_by_run_id="root-public-plan",
        ),
        (unit,),
        {
            "turnId": "turn-public",
            "projectId": "project-public",
            "sessionId": 1,
            "targetRole": "creative_brief",
            "screenplayScope": {},
        },
        "document:sections",
        plan,
        plan,
    )

    payload = _planning_payload(checkpoint)
    encoded = json.dumps(payload, ensure_ascii=False)
    assert payload["artifactReceipts"] == [{
        "partKind": "documentSection",
        "digest": "sha256:" + "a" * 64,
        "sectionKey": "characters",
        "status": "completed",
    }]
    assert "unitKind" not in encoded
    assert "PRIVATE-UNIT-SENTINEL" not in encoded
    assert "PRIVATE-TASK-SENTINEL" not in encoded
    assert "PROMPT-SENTINEL-DO-NOT-LEAK" not in encoded


async def test_checkpoint_treats_expanded_structure_episodes_as_completed_scope():
    plan = ExecutionPlan(
        title="设计分集结构",
        task_spec=_screenplay_task_spec(deliverable="structure"),
        steps=_bound_steps("evidence", "create", "deliver"),
    )
    units = (
        SimpleNamespace(
            status=SimpleNamespace(value="completed"),
            required=True,
            artifact_digest="sha256:" + "a" * 64,
            error_code=None,
            failure=None,
            metadata={
                "unitKind": "generate_document_section",
                "input": {
                    "sectionKey": "episode_plan:index",
                    "documentSectionKey": "episode_plan",
                    "episodePlanIndex": True,
                },
            },
        ),
        SimpleNamespace(
            status=SimpleNamespace(value="expanded"),
            required=False,
            artifact_digest=None,
            error_code="structure_episode_plan_split_required",
            failure={"category": "model_output_invalid"},
            metadata={
                "unitKind": "expand_structure_episode_plan",
                "input": {
                    "sectionKey": "episode_plan",
                    "splitStrategy": "structure_episode_plan",
                },
            },
        ),
        SimpleNamespace(
            status=SimpleNamespace(value="completed"),
            required=True,
            artifact_digest="sha256:" + "b" * 64,
            error_code=None,
            failure=None,
            metadata={
                "unitKind": "generate_document_section",
                "input": {
                    "sectionKey": "episode_plan:episode-1",
                    "documentSectionKey": "episode_plan",
                    "episodeNumber": 1,
                },
            },
        ),
    )

    checkpoint = _checkpoint_input(
        SimpleNamespace(id="task-structure", created_by_run_id="root-structure"),
        units,
        {
            "turnId": "turn-structure",
            "projectId": "project-structure",
            "sessionId": 1,
            "targetRole": "structure",
            "screenplayScope": {},
        },
        "document:sections",
        plan,
        plan,
    )

    assert checkpoint.remaining_scope["episodeNumbers"] == []
    assert checkpoint.remaining_scope["sectionKeys"] == []
    assert checkpoint.typed_failures == ()


@pytest.mark.parametrize(("kind", "expected"), (
    ("collect_evidence", False),
    ("validate_manifest_part", False),
    ("project_structure_hooks", False),
    ("generate_draft_scene", True),
    ("generate_episode_metadata", True),
    ("generate_document_section", True),
    ("generate_review_dimension", True),
    ("compose_final_response", True),
))
async def test_screenplay_part_run_requirement_classification(kind, expected):
    assert _requires_run(kind) is expected


async def test_checkpoint_planner_accepts_only_future_copy_and_dependencies():
    original = ExecutionPlan(
        title="创作两集",
        goal="完成两集候选稿",
        task_spec=_screenplay_task_spec(deliverable="screenplayDraft"),
        steps=_bound_steps("evidence", "create", "deliver"),
    )
    current = replace(original, steps=(
        replace(original.steps[0], status=StepStatus.DONE),
        replace(original.steps[1], status=StepStatus.RUNNING),
        original.steps[2],
    ))
    revised = replace(current, steps=(
        current.steps[0],
        replace(current.steps[1], title="完成剩余集", description="依据检查点继续"),
        replace(current.steps[2], depends_on=("create",)),
    ))

    class Models:
        async def run_json(self, **kwargs):
            self.payload = kwargs["user_payload"]
            value = {
                "protocol": CHECKPOINT_PLAN_PROTOCOL,
                "outcome": "revised",
                "plan": _plan_mapping(revised),
            }
            return StructuredModelResult(kwargs["validate"](value), "child-plan")

    models = Models()
    decision = await ScreenplayCheckpointPlanner(models, runtime=object()).revise(
        ScreenplayCheckpointInput(
            checkpoint_key="episode:4",
            root_run_id="root-1",
            task_id="task-1",
            turn_id="turn-1",
            project_id="project-1",
            session_id=1,
            target_role="screenplayDraft",
            original_plan=original,
            current_plan=current,
            completed_summaries=({"stepId": "evidence", "summary": "完成"},),
            artifact_receipts=({"part": "episode:4", "digest": "sha256:" + "a" * 64},),
            remaining_scope={"episodeNumbers": [5]},
            base_revision_id="sprev-private-root-id",
        )
    )

    assert decision.outcome is ScreenplayCheckpointOutcome.REVISED
    assert decision.plan == revised
    serialized = json.dumps(models.payload, ensure_ascii=False)
    assert "task-1" not in serialized
    assert "root-1" not in serialized
    assert "sprev-private-root-id" not in serialized
    assert "正文" not in serialized


@pytest.mark.parametrize("condition", ["complete", "pending", "unknown", "episode", "failure", "constraint", "missing_receipts"])
async def test_checkpoint_skips_model_only_for_verified_complete_business_scope(condition):
    plan = ExecutionPlan(
        title="创作", task_spec=_screenplay_task_spec(deliverable="screenplayDraft"),
        steps=_bound_steps("evidence", "create", "deliver"),
    )
    remaining = {"pendingBusinessUnits": 0, "episodeNumbers": [], "sectionKeys": []}
    if condition == "pending":
        remaining["pendingBusinessUnits"] = 1
    elif condition == "unknown":
        del remaining["pendingBusinessUnits"]
    elif condition == "episode":
        remaining["episodeNumbers"] = [5]

    class Models:
        calls = 0

        async def run_json(self, **kwargs):
            self.calls += 1
            value = {"protocol": CHECKPOINT_PLAN_PROTOCOL, "outcome": "unchanged"}
            return StructuredModelResult(kwargs["validate"](value), "root")

    models = Models()
    decision = await ScreenplayCheckpointPlanner(models, runtime=object()).revise(
        ScreenplayCheckpointInput(
            checkpoint_key="episode:4", root_run_id="root", task_id="task", turn_id="turn",
            project_id="project", session_id=1, target_role="screenplayDraft",
            original_plan=plan, current_plan=plan, completed_summaries=(),
            artifact_receipts=() if condition == "missing_receipts" else ({"digest": "sha256:" + "a" * 64},),
            typed_failures=({"code": "validation_failed"},) if condition == "failure" else (),
            constraint_changes=({"code": "changed_scope"},) if condition == "constraint" else (),
            remaining_scope=remaining,
        )
    )
    assert decision.outcome is ScreenplayCheckpointOutcome.UNCHANGED
    assert models.calls == (0 if condition == "complete" else 1)


async def test_checkpoint_loads_current_plan_from_authoritative_root_state(
    temp_db: DatabaseConnection,
):
    original = ExecutionPlan(
        title="创作两集",
        goal="完成两集候选稿",
        task_spec=_screenplay_task_spec(deliverable="screenplayDraft"),
        steps=_bound_steps("evidence", "create", "deliver"),
    )
    await temp_db.execute(
        "INSERT INTO ai_agent_runs (id, status, prompt) VALUES (?, 'running', ?)",
        ["root-checkpoint-plan", "创作两集"],
    )
    for position, step in enumerate(original.steps):
        await temp_db.execute(
            "INSERT INTO ai_agent_run_todos "
            "(run_id, step_id, title, status, executor, step_type, "
            "risk_level, description, expected_tools, "
            "assignment_json, depends_on_json, result_summary, error, sort) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                "root-checkpoint-plan",
                step.id,
                step.title,
                "done" if position == 0 else "running" if position == 1 else "pending",
                step.executor.value,
                step.type.value,
                step.risk_level.value if step.risk_level else None,
                step.description,
                json.dumps(list(step.suggested_tools)),
                "{}",
                json.dumps(list(step.depends_on)),
                "已读取权威材料" if position == 0 else None,
                None,
                position,
            ],
        )
    await temp_db.execute(
        "INSERT INTO ai_agent_run_events (run_id, event_type, payload_json) "
        "VALUES (?, 'run.todos_updated', ?)",
        [
            "root-checkpoint-plan",
            json.dumps({
                "title": original.title,
                "goal": original.goal,
                "taskSpec": original.task_spec.to_mapping(),
                "steps": _plan_mapping(original)["steps"],
            }, ensure_ascii=False),
        ],
    )

    current = await SqliteScreenplayCheckpointRepository(
        temp_db
    ).load_root_plan("root-checkpoint-plan")

    assert current.title == original.title
    assert current.goal == original.goal
    assert current.task_spec == original.task_spec
    assert current.steps[0].status is StepStatus.DONE
    assert current.steps[0].result_summary == "已读取权威材料"
    assert current.steps[1].status is StepStatus.RUNNING
    assert current.steps[1].depends_on == original.steps[1].depends_on


async def test_checkpoint_root_plan_fails_closed_without_one_authoritative_plan_event(
    temp_db: DatabaseConnection,
):
    await temp_db.execute(
        "INSERT INTO ai_agent_runs (id, status, prompt) VALUES (?, 'running', ?)",
        ["root-missing-plan", "创作"],
    )

    with pytest.raises(
        ScreenplayCheckpointStateError,
        match="authoritative Root plan",
    ):
        await SqliteScreenplayCheckpointRepository(temp_db).load_root_plan(
            "root-missing-plan"
        )


async def test_stale_ready_checkpoint_is_persistently_failed_not_reemitted(
    temp_db: DatabaseConnection,
):
    repository = SqliteScreenplayCheckpointRepository(temp_db)
    plan = ExecutionPlan(
        title="创作",
        task_spec=_screenplay_task_spec(deliverable="screenplayDraft"),
        steps=_bound_steps("evidence", "create", "deliver"),
    )
    reservation = await repository.reserve(
        operation_id="operation-stale",
        task_id="task-stale",
        checkpoint_key="episode:4",
        root_run_id="root-stale",
        input_digest="sha256:" + "1" * 64,
    )
    await repository.ready(
        operation_id="operation-stale",
        checkpoint_key="episode:4",
        plan=plan,
        outcome=ScreenplayCheckpointOutcome.REVISED,
        reservation_owner=str(reservation["reservation_owner"]),
        reservation_epoch=int(reservation["reservation_epoch"]),
    )

    row = await repository.fail_ready_conflict(
        operation_id="operation-stale",
        checkpoint_key="episode:4",
        code="screenplay_checkpoint_ready_root_plan_conflict",
        expected_plan_digest=str((await repository.load(
            "operation-stale", "episode:4"
        ))["plan_digest"]),
    )

    assert row["status"] == "failed"
    assert row["outcome"] == ScreenplayCheckpointOutcome.FAILED.value
    assert row["error_code"] == (
        "screenplay_checkpoint_ready_root_plan_conflict"
    )
    assert await repository.root_revision_digest(
        "root-stale",
        "episode:4",
    ) is None


async def test_checkpoint_reservation_is_single_winner_and_expiry_recoverable(
    temp_db: DatabaseConnection,
):
    repository = SqliteScreenplayCheckpointRepository(temp_db)

    async def reserve(token: str):
        return await repository.reserve(
            operation_id="operation-cas",
            task_id="task-cas",
            checkpoint_key="episode:4",
            root_run_id="root-cas",
            input_digest="sha256:" + "2" * 64,
            reservation_token=token,
        )

    first, second = await asyncio.gather(reserve("owner-a"), reserve("owner-b"))
    assert sum(bool(row["_acquired"]) for row in (first, second)) == 1

    await temp_db.execute(
        "UPDATE screenplay_checkpoint_plans "
        "SET reservation_expires_at_ms = 0 WHERE operation_id = ?",
        ["operation-cas"],
    )
    recovered = await reserve("owner-after-restart")
    assert recovered["_acquired"] is True
    assert recovered["reservation_owner"] == "owner-after-restart"


async def test_checkpoint_planning_failure_is_terminal_and_owner_fenced(
    temp_db: DatabaseConnection,
):
    repository = SqliteScreenplayCheckpointRepository(temp_db)
    stale = await repository.reserve(
        operation_id="operation-planning-failure",
        task_id="task-planning-failure",
        checkpoint_key="episode:4",
        root_run_id="root-planning-failure",
        input_digest="sha256:" + "d" * 64,
        reservation_token="owner-stale",
    )
    await temp_db.execute(
        "UPDATE screenplay_checkpoint_plans SET reservation_expires_at_ms = 0 "
        "WHERE operation_id = ?",
        ["operation-planning-failure"],
    )
    recovered = await repository.reserve(
        operation_id="operation-planning-failure",
        task_id="task-planning-failure",
        checkpoint_key="episode:4",
        root_run_id="root-planning-failure",
        input_digest="sha256:" + "d" * 64,
        reservation_token="owner-recovered",
    )

    with pytest.raises(RuntimeError, match="reservation_lost"):
        await repository.fail_planning(
            operation_id="operation-planning-failure",
            checkpoint_key="episode:4",
            code="model_output_truncated",
            reservation_owner="owner-stale",
            reservation_epoch=int(stale["reservation_epoch"]),
        )
    failed = await repository.fail_planning(
        operation_id="operation-planning-failure",
        checkpoint_key="episode:4",
        code="model_output_truncated",
        reservation_owner="owner-recovered",
        reservation_epoch=int(recovered["reservation_epoch"]),
    )

    assert failed["status"] == "failed"
    assert failed["outcome"] == ScreenplayCheckpointOutcome.FAILED.value
    assert failed["error_code"] == "model_output_truncated"
    assert failed["reservation_owner"] is None
    assert failed["reservation_expires_at_ms"] is None


async def test_review_checkpoint_gate_waits_for_all_episode_validations(
    temp_db: DatabaseConnection,
):
    from infrastructure.screenplay.long_task_claim_guard import ScreenplayCheckpointClaimGuard

    repository = SqliteLongTaskRepository(
        temp_db, claim_guard=ScreenplayCheckpointClaimGuard(temp_db),
    )
    task = await repository.create(
        "task-review-checkpoint-gate",
        LongTaskCreateCommand(
            namespace="purrtypos.screenplay",
            kind="screenplay.review",
            owner_id="project-review-checkpoint-gate",
            created_by_run_id="root-review-checkpoint-gate",
            units=(
                LongTaskUnitSpec(
                    id="review:1:validation", position=0,
                    metadata={"unitKind": "validate_manifest_part", "input": {
                        "validationKind": "review_episode", "episodeNumber": 1,
                    }},
                ),
                LongTaskUnitSpec(
                    id="review:2:generate", position=1,
                    dependencies=("review:1:validation",),
                    metadata={"unitKind": "generate_manifest_part"},
                ),
                LongTaskUnitSpec(
                    id="review:2:validation", position=2,
                    dependencies=("review:2:generate",),
                    metadata={"unitKind": "validate_manifest_part", "input": {
                        "validationKind": "review_episode", "episodeNumber": 2,
                    }},
                ),
                LongTaskUnitSpec(
                    id="compose-final-response", position=3,
                    dependencies=("review:2:validation",),
                    metadata={"unitKind": "compose_final_response"},
                ),
            ),
            metadata={
                "operationId": "operation-review-checkpoint-gate",
                "checkpointPlanningEnabled": True,
            },
        ),
    )
    task = await repository.start(task.id, expected_revision=task.revision)
    for unit_id in (
        "review:1:validation", "review:2:generate", "review:2:validation",
    ):
        unit = await repository.claim_ready_unit(
            task.id, worker_id="review-worker", lease_duration_ms=30_000,
        )
        assert unit is not None, f"review stalled before {unit_id}"
        assert unit.id == unit_id
        await repository.complete_unit(
            task.id, unit.id, worker_id="review-worker",
            lease_epoch=unit.lease_epoch,
            result=LongTaskUnitResult(output_ref=f"artifact://{unit_id}"),
        )
    assert await repository.claim_ready_unit(
        task.id, worker_id="review-worker", lease_duration_ms=30_000,
    ) is None


@pytest.mark.parametrize("commit_ready_first", (False, True))
async def test_checkpoint_ready_write_failure_preserves_error_and_terminates_receipt(
    temp_db: DatabaseConnection,
    monkeypatch,
    commit_ready_first: bool,
):
    repository = SqliteScreenplayCheckpointRepository(temp_db)
    plan = ExecutionPlan(
        title="创作",
        task_spec=_screenplay_task_spec(deliverable="screenplayDraft"),
        steps=_bound_steps("evidence", "create", "deliver"),
    )
    original_ready = repository.ready
    original_error = RuntimeError("checkpoint_ready_write_failed")

    async def load_plan(*_args, **_kwargs):
        return plan

    async def roots(_root_run_id):
        return None, "root-ready-failure"

    async def revise(_value, _signal):
        return SimpleNamespace(
            outcome=ScreenplayCheckpointOutcome.UNCHANGED,
            plan=None,
        )

    async def fail_ready(**kwargs):
        if commit_ready_first:
            await original_ready(**kwargs)
        raise original_error

    async def downstream(_update):
        pytest.fail("failed ready transition must not publish a plan revision")

    monkeypatch.setattr(repository, "load_root_plan", load_plan)
    monkeypatch.setattr(repository, "continuation_plan_roots", roots)
    monkeypatch.setattr(repository, "ready", fail_ready)
    observer = _ScreenplayCheckpointObserver(
        dispatcher=SimpleNamespace(_checkpoints=repository),
        planner=SimpleNamespace(revise=revise),
        downstream=downstream,
        task_id="task-ready-failure",
        root_run_id="root-ready-failure",
        signal=None,
    )
    async with asyncio.timeout(2):
        with pytest.raises(RuntimeError) as captured:
            await observer._handle_checkpoint(
                SimpleNamespace(id="task-ready-failure"),
                (),
                {
                    "turnId": "turn-ready-failure",
                    "projectId": "project-ready-failure",
                    "sessionId": 1,
                    "targetRole": "screenplayDraft",
                },
                "operation-ready-failure",
                "episode:4",
                SimpleNamespace(),
            )

    assert captured.value is original_error
    failed = await repository.load("operation-ready-failure", "episode:4")
    assert failed["status"] == "failed"
    assert failed["error_code"] == "checkpoint_ready_write_failed"
    assert failed["reservation_owner"] is None
    assert failed["reservation_expires_at_ms"] is None
    assert failed["reservation_epoch"] == 1
    assert bool(failed["plan_digest"]) is commit_ready_first


@pytest.mark.parametrize("failure_target", ("downstream", "applied"))
async def test_checkpoint_applying_exception_fails_owned_receipt_with_original_error(
    temp_db: DatabaseConnection,
    monkeypatch,
    failure_target: str,
):
    repository = SqliteScreenplayCheckpointRepository(temp_db)
    plan = ExecutionPlan(
        title="创作",
        task_spec=_screenplay_task_spec(deliverable="screenplayDraft"),
        steps=_bound_steps("evidence", "create", "deliver"),
    )
    reservation = await repository.reserve(
        operation_id="operation-apply-failure",
        task_id="task-apply-failure",
        checkpoint_key="episode:4",
        root_run_id="root-apply-failure",
        input_digest="sha256:" + "c" * 64,
        reservation_token="planner",
    )
    ready = await repository.ready(
        operation_id="operation-apply-failure",
        checkpoint_key="episode:4",
        plan=plan,
        outcome=ScreenplayCheckpointOutcome.UNCHANGED,
        reservation_owner="planner",
        reservation_epoch=int(reservation["reservation_epoch"]),
    )
    original_error = RuntimeError(f"checkpoint_{failure_target}_failed")
    downstream_calls = 0

    async def downstream(_update):
        nonlocal downstream_calls
        downstream_calls += 1
        raise original_error

    async def acknowledged(*_args, **_kwargs):
        return ready["plan_digest"]

    async def fail_applied(**_kwargs):
        raise original_error

    if failure_target == "applied":
        monkeypatch.setattr(repository, "root_revision_digest", acknowledged)
        monkeypatch.setattr(repository, "applied", fail_applied)
    observer = _ScreenplayCheckpointObserver(
        dispatcher=SimpleNamespace(_checkpoints=repository),
        planner=SimpleNamespace(),
        downstream=downstream,
        task_id="task-apply-failure",
        root_run_id="root-apply-failure",
        signal=None,
    )
    async with asyncio.timeout(2):
        with pytest.raises(RuntimeError) as captured:
            await observer._emit_ready(
                ready,
                SimpleNamespace(event=AgentEvent(
                    type=CoreEventType.LONG_TASK_PROGRESS,
                    payload={"taskId": "task-apply-failure"},
                )),
            )

    assert captured.value is original_error
    failed = await repository.load("operation-apply-failure", "episode:4")
    assert failed["status"] == "failed"
    assert failed["error_code"] == str(original_error)
    assert failed["reservation_owner"] is None
    assert failed["reservation_expires_at_ms"] is None
    assert failed["reservation_epoch"] == 2
    assert downstream_calls == (1 if failure_target == "downstream" else 0)


@pytest.mark.parametrize("failure_stage", ("reserved", "ready", "applying"))
async def test_checkpoint_failure_cas_cannot_overwrite_a_newer_lease(
    temp_db: DatabaseConnection,
    failure_stage: str,
):
    repository = SqliteScreenplayCheckpointRepository(temp_db)
    plan = ExecutionPlan(
        title="创作",
        task_spec=_screenplay_task_spec(deliverable="screenplayDraft"),
        steps=_bound_steps("evidence", "create", "deliver"),
    )
    reserved = await repository.reserve(
        operation_id="operation-failure-fence",
        task_id="task-failure-fence",
        checkpoint_key="episode:4",
        root_run_id="root-failure-fence",
        input_digest="sha256:" + "b" * 64,
        reservation_token="planner",
    )
    ready = await repository.ready(
        operation_id="operation-failure-fence",
        checkpoint_key="episode:4",
        plan=plan,
        outcome=ScreenplayCheckpointOutcome.UNCHANGED,
        reservation_owner="planner",
        reservation_epoch=int(reserved["reservation_epoch"]),
    )
    applying = await repository.acquire_applying(
        operation_id="operation-failure-fence",
        checkpoint_key="episode:4",
        digest=str(ready["plan_digest"]),
        reservation_token="old-applier",
    )
    await temp_db.execute(
        "UPDATE screenplay_checkpoint_plans SET reservation_expires_at_ms = 0 "
        "WHERE operation_id = ?",
        ["operation-failure-fence"],
    )
    recovered = await repository.acquire_applying(
        operation_id="operation-failure-fence",
        checkpoint_key="episode:4",
        digest=str(ready["plan_digest"]),
        reservation_token="new-applier",
    )
    stale = {"reserved": reserved, "ready": ready, "applying": applying}[failure_stage]
    with pytest.raises(RuntimeError, match="reservation_lost"):
        await repository.fail_execution(stale, code="stale_worker_failed")
    current = await repository.load("operation-failure-fence", "episode:4")
    assert current["status"] == "applying"
    assert current["reservation_owner"] == "new-applier"
    assert current["reservation_epoch"] == recovered["reservation_epoch"]
    assert current["error_code"] is None


async def test_checkpoint_failure_settlement_keeps_both_errors(
    temp_db: DatabaseConnection,
    monkeypatch,
):
    repository = SqliteScreenplayCheckpointRepository(temp_db)
    receipt = await repository.reserve(
        operation_id="operation-double-failure",
        task_id="task-double-failure",
        checkpoint_key="episode:4",
        root_run_id="root-double-failure",
        input_digest="sha256:" + "a" * 64,
        reservation_token="planner",
    )
    original_error = ModelGatewayError(
        "provider response was incomplete", code="model_output_truncated",
    )
    settlement_error = RuntimeError("checkpoint_failure_write_failed")

    async def fail_settlement(*_args, **_kwargs):
        raise settlement_error

    monkeypatch.setattr(repository, "fail_execution", fail_settlement)
    observer = _ScreenplayCheckpointObserver(
        dispatcher=SimpleNamespace(_checkpoints=repository),
        planner=SimpleNamespace(),
        downstream=SimpleNamespace(),
        task_id="task-double-failure",
        root_run_id="root-double-failure",
        signal=None,
    )
    with pytest.raises(ModelGatewayError) as captured:
        await observer._fail_execution(receipt, original_error)
    assert captured.value is original_error
    assert captured.value.code == "model_output_truncated"
    assert captured.value.__cause__ is settlement_error


async def test_failed_checkpoint_rejects_downstream_claim_without_idle_loop(
    temp_db: DatabaseConnection,
):
    from infrastructure.screenplay.long_task_claim_guard import ScreenplayCheckpointClaimGuard

    repository = SqliteLongTaskRepository(
        temp_db, claim_guard=ScreenplayCheckpointClaimGuard(temp_db),
    )
    task = await repository.create(
        "task-failed-checkpoint-gate",
        LongTaskCreateCommand(
            namespace="purrtypos.screenplay",
            kind="screenplay.draft",
            owner_id="project-failed-checkpoint-gate",
            created_by_run_id="root-failed-checkpoint-gate",
            units=(
                LongTaskUnitSpec(
                    id="episode:4:validation",
                    position=0,
                    metadata={
                        "unitKind": "validate_manifest_part",
                        "input": {
                            "validationKind": "draft_episode",
                            "episodeNumber": 4,
                        },
                    },
                ),
                LongTaskUnitSpec(
                    id="compose-final-response",
                    position=1,
                    dependencies=("episode:4:validation",),
                    metadata={"unitKind": "compose_final_response"},
                ),
            ),
            metadata={
                "operationId": "operation-failed-checkpoint-gate",
                "checkpointPlanningEnabled": True,
            },
        ),
    )
    task = await repository.start(task.id, expected_revision=task.revision)
    validation = await repository.claim_ready_unit(
        task.id,
        worker_id="worker-checkpoint-gate",
        lease_duration_ms=30_000,
    )
    assert validation is not None
    await repository.complete_unit(
        task.id,
        validation.id,
        worker_id="worker-checkpoint-gate",
        lease_epoch=validation.lease_epoch,
        result=LongTaskUnitResult(output_ref="artifact://episode-4-validation"),
    )
    checkpoints = SqliteScreenplayCheckpointRepository(temp_db)
    reservation = await checkpoints.reserve(
        operation_id="operation-failed-checkpoint-gate",
        task_id=task.id,
        checkpoint_key="episode:4",
        root_run_id="root-failed-checkpoint-gate",
        input_digest="sha256:" + "f" * 64,
        reservation_token="checkpoint-owner",
    )
    await checkpoints.fail_planning(
        operation_id="operation-failed-checkpoint-gate",
        checkpoint_key="episode:4",
        code="model_output_truncated",
        reservation_owner="checkpoint-owner",
        reservation_epoch=int(reservation["reservation_epoch"]),
    )

    with pytest.raises(ContractViolationError) as failure:
        await repository.claim_ready_unit(
            task.id,
            worker_id="worker-checkpoint-gate",
            lease_duration_ms=30_000,
        )
    assert failure.value.code == "model_output_truncated"


async def test_ready_checkpoint_rebinds_to_continuation_root_after_crash(
    temp_db: DatabaseConnection,
):
    repository = SqliteScreenplayCheckpointRepository(temp_db)
    plan = ExecutionPlan(
        title="创作",
        task_spec=_screenplay_task_spec(deliverable="screenplayDraft"),
        steps=_bound_steps("evidence", "create", "deliver"),
    )
    reservation = await repository.reserve(
        operation_id="operation-continuation",
        task_id="task-continuation",
        checkpoint_key="episode:4",
        root_run_id="root-old",
        input_digest="sha256:" + "3" * 64,
    )
    ready = await repository.ready(
        operation_id="operation-continuation",
        checkpoint_key="episode:4",
        plan=plan,
        outcome=ScreenplayCheckpointOutcome.REVISED,
        reservation_owner=str(reservation["reservation_owner"]),
        reservation_epoch=int(reservation["reservation_epoch"]),
    )

    async with temp_db.transaction(cancellation_linearizable=True):
        await repository.rebind_for_continuation(
            operation_id="operation-continuation",
            source_root_run_id="root-old",
            continuation_root_run_id="root-new",
        )

    rebound = await repository.load("operation-continuation", "episode:4")
    assert rebound["status"] == "ready"
    assert rebound["root_run_id"] == "root-new"
    assert rebound["plan_digest"] == ready["plan_digest"]
    assert rebound["input_digest"] == ready["input_digest"]


async def test_applied_checkpoint_on_source_root_remains_immutable_audit(
    temp_db: DatabaseConnection,
):
    repository = SqliteScreenplayCheckpointRepository(temp_db)
    plan = ExecutionPlan(
        title="创作",
        task_spec=_screenplay_task_spec(deliverable="screenplayDraft"),
        steps=_bound_steps("evidence", "create", "deliver"),
    )
    reservation = await repository.reserve(
        operation_id="operation-applied-audit",
        task_id="task-applied-audit",
        checkpoint_key="episode:4",
        root_run_id="root-old",
        input_digest="sha256:" + "7" * 64,
    )
    ready = await repository.ready(
        operation_id="operation-applied-audit",
        checkpoint_key="episode:4",
        plan=plan,
        outcome=ScreenplayCheckpointOutcome.REVISED,
        reservation_owner=str(reservation["reservation_owner"]),
        reservation_epoch=int(reservation["reservation_epoch"]),
    )
    await temp_db.execute(
        "INSERT INTO ai_agent_run_events "
        "(run_id, event_type, payload_json) "
        "VALUES ('root-old', 'run.todos_updated', ?)",
        [json.dumps(_root_revision_payload(
            _canonical_root_plan(plan),
            identity="episode:4",
            digest=str(ready["plan_digest"]),
        ))],
    )

    async with temp_db.transaction(cancellation_linearizable=True):
        await repository.rebind_for_continuation(
            operation_id="operation-applied-audit",
            source_root_run_id="root-old",
            continuation_root_run_id="root-new",
        )

    applied = await repository.load("operation-applied-audit", "episode:4")
    assert applied["status"] == "applied"
    assert applied["root_run_id"] == "root-old"
    assert applied["plan_digest"] == ready["plan_digest"]


async def test_paused_checkpoint_retries_only_after_explicit_continuation_rebind(
    temp_db: DatabaseConnection,
):
    repository = SqliteScreenplayCheckpointRepository(temp_db)
    reservation = await repository.reserve(
        operation_id="operation-paused-retry",
        task_id="task-paused-retry",
        checkpoint_key="episode:4",
        root_run_id="root-old",
        input_digest="sha256:" + "8" * 64,
        reservation_token="planner-old",
    )
    paused = await repository.pause(
        operation_id="operation-paused-retry",
        checkpoint_key="episode:4",
        outcome=ScreenplayCheckpointOutcome.REQUIRES_RERESOLUTION,
        code="scope_changed",
        reservation_owner="planner-old",
        reservation_epoch=int(reservation["reservation_epoch"]),
    )

    unchanged = await repository.reserve(
        operation_id="operation-paused-retry",
        task_id="task-paused-retry",
        checkpoint_key="episode:4",
        root_run_id="root-old",
        input_digest=str(paused["input_digest"]),
        reservation_token="implicit-retry",
    )
    assert unchanged["status"] == "paused"
    assert unchanged["_acquired"] is False

    async with temp_db.transaction(cancellation_linearizable=True):
        await repository.rebind_for_continuation(
            operation_id="operation-paused-retry",
            source_root_run_id="root-old",
            continuation_root_run_id="root-new",
        )
    retried = await repository.acquire_planning(
        operation_id="operation-paused-retry",
        task_id="task-paused-retry",
        checkpoint_key="episode:4",
        root_run_id="root-new",
        input_digest="sha256:" + "9" * 64,
        reservation_token="explicit-retry",
    )

    assert retried["status"] == "reserved"
    assert retried["_acquired"] is True
    assert retried["root_run_id"] == "root-new"
    assert retried["input_digest"] == "sha256:" + "9" * 64
    assert retried["reservation_owner"] == "explicit-retry"


async def test_null_continuation_binding_is_not_a_phantom_root(
    temp_db: DatabaseConnection,
):
    await temp_db.execute(
        "INSERT INTO ai_agent_runs "
        "(id, status, prompt, binding_attributes_json) "
        "VALUES ('root-original', 'running', '', ?)",
        [json.dumps({"continuationOf": None})],
    )

    assert await SqliteScreenplayCheckpointRepository(
        temp_db
    ).continuation_plan_roots("root-original") == (None, "root-original")


async def test_expired_checkpoint_owner_is_fenced_during_continuation_rebind(
    temp_db: DatabaseConnection,
):
    repository = SqliteScreenplayCheckpointRepository(temp_db)
    plan = ExecutionPlan(
        title="创作",
        task_spec=_screenplay_task_spec(deliverable="screenplayDraft"),
        steps=_bound_steps("evidence", "create", "deliver"),
    )
    reservation = await repository.reserve(
        operation_id="operation-expired-applying",
        task_id="task-expired-applying",
        checkpoint_key="episode:4",
        root_run_id="root-old",
        input_digest="sha256:" + "4" * 64,
    )
    ready = await repository.ready(
        operation_id="operation-expired-applying",
        checkpoint_key="episode:4",
        plan=plan,
        outcome=ScreenplayCheckpointOutcome.REVISED,
        reservation_owner=str(reservation["reservation_owner"]),
        reservation_epoch=int(reservation["reservation_epoch"]),
    )
    applying = await repository.acquire_applying(
        operation_id="operation-expired-applying",
        checkpoint_key="episode:4",
        digest=str(ready["plan_digest"]),
        reservation_token="dead-owner",
    )
    await temp_db.execute(
        "UPDATE screenplay_checkpoint_plans SET reservation_expires_at_ms = 0 "
        "WHERE operation_id = ?",
        ["operation-expired-applying"],
    )
    reserved = await repository.reserve(
        operation_id="operation-expired-reserved",
        task_id="task-expired-reserved",
        checkpoint_key="episode:5",
        root_run_id="root-old",
        input_digest="sha256:" + "5" * 64,
        reservation_token="dead-planner",
    )
    await temp_db.execute(
        "UPDATE screenplay_checkpoint_plans SET reservation_expires_at_ms = 0 "
        "WHERE operation_id = ?",
        ["operation-expired-reserved"],
    )

    async with temp_db.transaction(cancellation_linearizable=True):
        await repository.rebind_for_continuation(
            operation_id="operation-expired-applying",
            source_root_run_id="root-old",
            continuation_root_run_id="root-new",
        )
        await repository.rebind_for_continuation(
            operation_id="operation-expired-reserved",
            source_root_run_id="root-old",
            continuation_root_run_id="root-new",
        )

    rebound = await repository.load(
        "operation-expired-applying", "episode:4"
    )
    assert rebound["status"] == "ready"
    assert rebound["root_run_id"] == "root-new"
    assert rebound["reservation_owner"] is None
    assert int(rebound["reservation_epoch"]) > int(
        applying["reservation_epoch"]
    )
    abandoned = await repository.load(
        "operation-expired-reserved", "episode:5"
    )
    assert abandoned["status"] == "continuation_retry"
    assert abandoned["root_run_id"] == "root-new"
    assert abandoned["error_code"] == "screenplay_checkpoint_owner_expired"
    assert abandoned["reservation_owner"] is None
    assert int(abandoned["reservation_epoch"]) > int(
        reserved["reservation_epoch"]
    )


async def test_active_checkpoint_owner_blocks_continuation_rebind(
    temp_db: DatabaseConnection,
):
    repository = SqliteScreenplayCheckpointRepository(temp_db)
    active = await repository.reserve(
        operation_id="operation-active-owner",
        task_id="task-active-owner",
        checkpoint_key="episode:4",
        root_run_id="root-old",
        input_digest="sha256:" + "a" * 64,
        reservation_token="live-planner",
    )

    with pytest.raises(
        ScreenplayCheckpointStateError,
        match="reservation is active",
    ):
        async with temp_db.transaction(cancellation_linearizable=True):
            await repository.rebind_for_continuation(
                operation_id="operation-active-owner",
                source_root_run_id="root-old",
                continuation_root_run_id="root-new",
            )

    unchanged = await repository.load("operation-active-owner", "episode:4")
    assert unchanged["status"] == "reserved"
    assert unchanged["root_run_id"] == "root-old"
    assert unchanged["reservation_owner"] == "live-planner"
    assert unchanged["reservation_epoch"] == active["reservation_epoch"]


async def test_continuation_ready_rebase_only_synchronizes_root_step_status(
    temp_db: DatabaseConnection,
):
    repository = SqliteScreenplayCheckpointRepository(temp_db)
    baseline = ExecutionPlan(
        title="创作",
        task_spec=_screenplay_task_spec(deliverable="screenplayDraft"),
        steps=_bound_steps("evidence", "create", "deliver"),
    )
    revised = replace(
        baseline,
        steps=(
            baseline.steps[0],
            replace(baseline.steps[1], title="调整后的生成步骤"),
            baseline.steps[2],
        ),
    )
    reservation = await repository.reserve(
        operation_id="operation-rebase",
        task_id="task-rebase",
        checkpoint_key="episode:4",
        root_run_id="root-new",
        input_digest="sha256:" + "6" * 64,
    )
    ready = await repository.ready(
        operation_id="operation-rebase",
        checkpoint_key="episode:4",
        plan=revised,
        outcome=ScreenplayCheckpointOutcome.REVISED,
        reservation_owner=str(reservation["reservation_owner"]),
        reservation_epoch=int(reservation["reservation_epoch"]),
    )
    current = replace(
        baseline,
        steps=(
            replace(
                baseline.steps[0],
                status=StepStatus.DONE,
                result_summary="证据已读取",
            ),
            baseline.steps[1],
            baseline.steps[2],
        ),
    )

    rebound = await repository.rebase_ready_for_continuation(
        operation_id="operation-rebase",
        checkpoint_key="episode:4",
        expected_plan_digest=str(ready["plan_digest"]),
        current_plan=current,
        input_digest="sha256:" + "7" * 64,
    )

    plan = parse_persisted_plan(str(rebound["plan_json"]))
    assert plan.steps[0].status is StepStatus.DONE
    assert plan.steps[0].result_summary == "证据已读取"
    assert plan.steps[1].title == "调整后的生成步骤"
    assert rebound["input_digest"] == "sha256:" + "7" * 64
    assert rebound["plan_digest"] != ready["plan_digest"]


async def test_checkpoint_non_owner_waits_at_barrier_until_owner_is_ready(
    temp_db: DatabaseConnection,
):
    repository = SqliteScreenplayCheckpointRepository(
        temp_db,
        lease_ms=1_000,
        poll_interval_seconds=0.005,
    )
    first = await repository.acquire_planning(
        operation_id="operation-barrier",
        task_id="task-barrier",
        checkpoint_key="episode:4",
        root_run_id="root-barrier",
        input_digest="sha256:" + "a" * 64,
        reservation_token="owner-a",
    )
    waiter = asyncio.create_task(repository.acquire_planning(
        operation_id="operation-barrier",
        task_id="task-barrier",
        checkpoint_key="episode:4",
        root_run_id="root-barrier",
        input_digest="sha256:" + "a" * 64,
        reservation_token="owner-b",
    ))
    await asyncio.sleep(0.02)
    assert waiter.done() is False

    plan = ExecutionPlan(
        title="创作",
        task_spec=_screenplay_task_spec(deliverable="screenplayDraft"),
        steps=_bound_steps("evidence", "create", "deliver"),
    )
    await repository.ready(
        operation_id="operation-barrier",
        checkpoint_key="episode:4",
        plan=plan,
        outcome=ScreenplayCheckpointOutcome.REVISED,
        reservation_owner="owner-a",
        reservation_epoch=int(first["reservation_epoch"]),
    )

    observed = await asyncio.wait_for(waiter, timeout=0.2)
    assert observed["status"] == "ready"
    assert observed["_acquired"] is False


async def test_checkpoint_expiry_takeover_fences_stale_planner_write(
    temp_db: DatabaseConnection,
):
    repository = SqliteScreenplayCheckpointRepository(
        temp_db,
        lease_ms=20,
        poll_interval_seconds=0.002,
    )
    stale = await repository.acquire_planning(
        operation_id="operation-fence",
        task_id="task-fence",
        checkpoint_key="episode:4",
        root_run_id="root-fence",
        input_digest="sha256:" + "b" * 64,
        reservation_token="owner-stale",
    )
    recovered = await repository.acquire_planning(
        operation_id="operation-fence",
        task_id="task-fence",
        checkpoint_key="episode:4",
        root_run_id="root-fence",
        input_digest="sha256:" + "b" * 64,
        reservation_token="owner-recovered",
    )
    assert recovered["_acquired"] is True
    assert int(recovered["reservation_epoch"]) > int(stale["reservation_epoch"])

    plan = ExecutionPlan(
        title="创作",
        task_spec=_screenplay_task_spec(deliverable="screenplayDraft"),
        steps=_bound_steps("evidence", "create", "deliver"),
    )
    with pytest.raises(RuntimeError, match="reservation_lost"):
        await repository.ready(
            operation_id="operation-fence",
            checkpoint_key="episode:4",
            plan=plan,
            outcome=ScreenplayCheckpointOutcome.REVISED,
            reservation_owner="owner-stale",
            reservation_epoch=int(stale["reservation_epoch"]),
        )
    await repository.ready(
        operation_id="operation-fence",
        checkpoint_key="episode:4",
        plan=plan,
        outcome=ScreenplayCheckpointOutcome.REVISED,
        reservation_owner="owner-recovered",
        reservation_epoch=int(recovered["reservation_epoch"]),
    )


async def test_checkpoint_planner_heartbeat_prevents_duplicate_model_call(
    temp_db: DatabaseConnection,
):
    repository = SqliteScreenplayCheckpointRepository(
        temp_db,
        lease_ms=300,
        poll_interval_seconds=0.002,
    )
    observer = _ScreenplayCheckpointObserver(
        dispatcher=SimpleNamespace(_checkpoints=repository),
        planner=SimpleNamespace(),
        downstream=SimpleNamespace(),
        task_id="task-heartbeat",
        root_run_id="root-heartbeat",
        signal=None,
    )
    plan = ExecutionPlan(
        title="创作",
        task_spec=_screenplay_task_spec(deliverable="screenplayDraft"),
        steps=_bound_steps("evidence", "create", "deliver"),
    )
    model_calls = 0

    async def worker(owner: str):
        nonlocal model_calls
        receipt = await repository.acquire_planning(
            operation_id="operation-heartbeat",
            task_id="task-heartbeat",
            checkpoint_key="episode:4",
            root_run_id="root-heartbeat",
            input_digest="sha256:" + "e" * 64,
            reservation_token=owner,
        )
        if not receipt.get("_acquired"):
            return receipt
        model_calls += 1
        await observer._with_heartbeat(receipt, "reserved", asyncio.sleep(0.6))
        return await repository.ready(
            operation_id="operation-heartbeat",
            checkpoint_key="episode:4",
            plan=plan,
            outcome=ScreenplayCheckpointOutcome.REVISED,
            reservation_owner=owner,
            reservation_epoch=int(receipt["reservation_epoch"]),
        )

    first, second = await asyncio.gather(worker("owner-a"), worker("owner-b"))

    assert model_calls == 1
    assert {first["status"], second["status"]} == {"ready"}


async def test_checkpoint_heartbeat_caller_cancel_cleans_worker_and_keeper(
    temp_db: DatabaseConnection,
):
    repository = SqliteScreenplayCheckpointRepository(
        temp_db,
        lease_ms=30,
        poll_interval_seconds=0.002,
    )
    receipt = await repository.acquire_planning(
        operation_id="operation-heartbeat-cancel",
        task_id="task-heartbeat-cancel",
        checkpoint_key="episode:4",
        root_run_id="root-heartbeat-cancel",
        input_digest="sha256:" + "f" * 64,
        reservation_token="owner-a",
    )
    observer = _ScreenplayCheckpointObserver(
        dispatcher=SimpleNamespace(_checkpoints=repository),
        planner=SimpleNamespace(),
        downstream=SimpleNamespace(),
        task_id="task-heartbeat-cancel",
        root_run_id="root-heartbeat-cancel",
        signal=None,
    )
    before = set(asyncio.all_tasks())
    running = asyncio.create_task(observer._with_heartbeat(
        receipt,
        "reserved",
        asyncio.Event().wait(),
    ))
    await asyncio.sleep(0.02)
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    await asyncio.sleep(0)
    assert set(asyncio.all_tasks()) - before == set()


async def test_checkpoint_waiter_cancellation_leaves_no_poll_task(
    temp_db: DatabaseConnection,
):
    repository = SqliteScreenplayCheckpointRepository(
        temp_db,
        lease_ms=1_000,
        poll_interval_seconds=0.005,
    )
    await repository.acquire_planning(
        operation_id="operation-cancel-wait",
        task_id="task-cancel-wait",
        checkpoint_key="episode:4",
        root_run_id="root-cancel-wait",
        input_digest="sha256:" + "c" * 64,
        reservation_token="owner-a",
    )
    before = set(asyncio.all_tasks())
    signal = asyncio.Event()
    waiter = asyncio.create_task(repository.acquire_planning(
        operation_id="operation-cancel-wait",
        task_id="task-cancel-wait",
        checkpoint_key="episode:4",
        root_run_id="root-cancel-wait",
        input_digest="sha256:" + "c" * 64,
        reservation_token="owner-b",
        signal=signal,
    ))
    await asyncio.sleep(0.02)
    signal.set()
    with pytest.raises(OperationCanceled):
        await waiter
    await asyncio.sleep(0)
    assert set(asyncio.all_tasks()) - before == set()


@pytest.mark.parametrize("event_already_persisted", (False, True))
async def test_ready_checkpoint_reconciles_root_event_without_replanning(
    temp_db: DatabaseConnection,
    event_already_persisted: bool,
):
    repository = SqliteScreenplayCheckpointRepository(temp_db)
    plan = ExecutionPlan(
        title="创作",
        task_spec=_screenplay_task_spec(deliverable="screenplayDraft"),
        steps=_bound_steps("evidence", "create", "deliver"),
    )
    reservation = await repository.reserve(
        operation_id="operation-ready",
        task_id="task-ready",
        checkpoint_key="episode:4",
        root_run_id="root-ready",
        input_digest="sha256:" + "1" * 64,
    )
    receipt = await repository.ready(
        operation_id="operation-ready",
        checkpoint_key="episode:4",
        plan=plan,
        outcome=ScreenplayCheckpointOutcome.REVISED,
        reservation_owner=str(reservation["reservation_owner"]),
        reservation_epoch=int(reservation["reservation_epoch"]),
    )
    downstream_calls = 0

    async def persist_revision(update):
        nonlocal downstream_calls
        downstream_calls += 1
        await temp_db.execute(
            "INSERT INTO ai_agent_run_events "
            "(run_id, event_type, payload_json) "
            "VALUES (?, 'run.todos_updated', ?)",
            [
                "root-ready",
                json.dumps(_root_revision_payload(
                    _canonical_root_plan(update.plan_revision),
                    identity=str(update.plan_revision_metadata["identity"]),
                    digest=str(update.plan_revision_metadata["digest"]),
                )),
            ],
        )

    if event_already_persisted:
        await temp_db.execute(
            "INSERT INTO ai_agent_run_events "
            "(run_id, event_type, payload_json) "
            "VALUES (?, 'run.todos_updated', ?)",
            [
                "root-ready",
                json.dumps(_root_revision_payload(
                    _canonical_root_plan(
                        parse_persisted_plan(str(receipt["plan_json"]))
                    ),
                    identity="episode:4",
                    digest=str(receipt["plan_digest"]),
                )),
            ],
        )
    observer = _ScreenplayCheckpointObserver(
        dispatcher=SimpleNamespace(_checkpoints=repository),
        planner=SimpleNamespace(),
        downstream=persist_revision,
        task_id="task-ready",
        root_run_id="root-ready",
        signal=None,
    )
    progress = SimpleNamespace(
        event=AgentEvent(
            type=CoreEventType.LONG_TASK_PROGRESS,
            payload={"taskId": "task-ready"},
        )
    )

    await observer._emit_ready(receipt, progress)

    assert downstream_calls == (0 if event_already_persisted else 1)
    assert (await repository.load("operation-ready", "episode:4"))["status"] == (
        "applied"
    )


@pytest.mark.parametrize(
    "mutation",
    (
        "metadata_only",
        "wrong_step",
        "wrong_event_digest",
        "duplicate_exact",
        "duplicate_conflict",
    ),
)
async def test_checkpoint_root_reconcile_requires_complete_matching_plan(
    temp_db: DatabaseConnection,
    mutation: str,
):
    repository = SqliteScreenplayCheckpointRepository(temp_db)
    plan = ExecutionPlan(
        title="创作",
        task_spec=_screenplay_task_spec(deliverable="screenplayDraft"),
        steps=_bound_steps("evidence", "create", "deliver"),
    )
    digest = plan_digest(plan)
    if mutation == "metadata_only":
        payload = {"planRevision": {
            "identity": "episode:4",
            "digest": digest,
        }}
    else:
        payload = _root_revision_payload(
            plan,
            identity="episode:4",
            digest=("sha256:" + "f" * 64 if mutation == "wrong_event_digest" else digest),
        )
        if mutation == "wrong_step":
            payload["steps"][1]["title"] = "并非receipt中的计划"
    await temp_db.execute(
        "INSERT INTO ai_agent_run_events "
        "(run_id, event_type, payload_json) "
        "VALUES (?, 'run.todos_updated', ?)",
        ["root-strict-reconcile", json.dumps(payload)],
    )
    if mutation in {"duplicate_exact", "duplicate_conflict"}:
        conflicting = (
            payload
            if mutation == "duplicate_exact"
            else _root_revision_payload(
                replace(plan, title="冲突计划"),
                identity="episode:4",
            )
        )
        await temp_db.execute(
            "INSERT INTO ai_agent_run_events "
            "(run_id, event_type, payload_json) "
            "VALUES (?, 'run.todos_updated', ?)",
            ["root-strict-reconcile", json.dumps(conflicting)],
        )

    with pytest.raises(
        ScreenplayCheckpointStateError,
        match="Root revision",
    ):
        await repository.root_revision_digest(
            "root-strict-reconcile",
            "episode:4",
            expected_digest=digest,
        )


async def test_checkpoint_root_digest_hydrates_hidden_tool_authority(
    temp_db: DatabaseConnection,
):
    repository = SqliteScreenplayCheckpointRepository(temp_db)
    plan = _canonical_root_plan(ExecutionPlan(
        title="创作",
        task_spec=TaskSpec(goal="创作", operation="create"),
        steps=(TaskStep(
            id="create",
            title="创建候选稿",
            type=StepType.WRITE,
            executor=StepExecutor.TOOL,
            risk_level=ToolRiskLevel.WRITE,
            suggested_tools=("delegateToAgents",),
        ),),
    ))
    digest = plan_digest(plan)
    await temp_db.execute(
        "INSERT INTO ai_agent_runs (id, status, prompt) "
        "VALUES (?, 'running', ?)",
        ["root-hidden-authority", "创作"],
    )
    await temp_db.execute(
        "INSERT INTO ai_agent_run_todos "
        "(run_id, step_id, title, status, executor, step_type, risk_level, "
        "expected_tools, depends_on_json, sort) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, '[]', 0)",
        [
            "root-hidden-authority",
            "create",
            "创建候选稿",
            "running",
            "tool",
            "write",
            "write",
            '["delegateToAgents"]',
        ],
    )
    payload = _root_revision_payload(
        plan,
        identity="document:sections",
        digest=digest,
    )
    payload["steps"][0].pop("suggested_tools")
    await temp_db.execute(
        "INSERT INTO ai_agent_run_events "
        "(run_id, event_type, payload_json) "
        "VALUES (?, 'run.todos_updated', ?)",
        ["root-hidden-authority", json.dumps(payload)],
    )

    assert await repository.root_revision_digest(
        "root-hidden-authority",
        "document:sections",
        expected_digest=digest,
    ) == digest


async def test_duplicate_root_revision_event_fails_ready_receipt_without_reemission(
    temp_db: DatabaseConnection,
):
    repository = SqliteScreenplayCheckpointRepository(temp_db)
    plan = ExecutionPlan(
        title="创作",
        task_spec=_screenplay_task_spec(deliverable="screenplayDraft"),
        steps=_bound_steps("evidence", "create", "deliver"),
    )
    reservation = await repository.reserve(
        operation_id="operation-duplicate-event",
        task_id="task-duplicate-event",
        checkpoint_key="episode:4",
        root_run_id="root-duplicate-event",
        input_digest="sha256:" + "9" * 64,
        reservation_token="planner",
    )
    receipt = await repository.ready(
        operation_id="operation-duplicate-event",
        checkpoint_key="episode:4",
        plan=plan,
        outcome=ScreenplayCheckpointOutcome.REVISED,
        reservation_owner="planner",
        reservation_epoch=int(reservation["reservation_epoch"]),
    )
    payload = _root_revision_payload(
        _canonical_root_plan(parse_persisted_plan(str(receipt["plan_json"]))),
        identity="episode:4",
        digest=str(receipt["plan_digest"]),
    )
    for _ in range(2):
        await temp_db.execute(
            "INSERT INTO ai_agent_run_events "
            "(run_id, event_type, payload_json) "
            "VALUES (?, 'run.todos_updated', ?)",
            ["root-duplicate-event", json.dumps(payload)],
        )
    downstream_calls = 0

    async def downstream(_update):
        nonlocal downstream_calls
        downstream_calls += 1

    observer = _ScreenplayCheckpointObserver(
        dispatcher=SimpleNamespace(_checkpoints=repository),
        planner=SimpleNamespace(),
        downstream=downstream,
        task_id="task-duplicate-event",
        root_run_id="root-duplicate-event",
        signal=None,
    )
    with pytest.raises(ScreenplayCheckpointStateError, match="duplicated"):
        await observer._emit_ready(
            receipt,
            SimpleNamespace(event=AgentEvent(
                type=CoreEventType.LONG_TASK_PROGRESS,
                payload={"taskId": "task-duplicate-event"},
            )),
        )

    assert downstream_calls == 0
    assert (await repository.load(
        "operation-duplicate-event",
        "episode:4",
    ))["status"] == "failed"
    assert await temp_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_run_events WHERE run_id = ? "
        "AND event_type = 'run.todos_updated'",
        ["root-duplicate-event"],
    ) == {"count": 2}


async def test_ready_checkpoint_has_single_apply_owner_and_single_revision_event(
    temp_db: DatabaseConnection,
):
    repository = SqliteScreenplayCheckpointRepository(
        temp_db,
        lease_ms=20,
        poll_interval_seconds=0.002,
    )
    plan = ExecutionPlan(
        title="创作",
        task_spec=_screenplay_task_spec(deliverable="screenplayDraft"),
        steps=_bound_steps("evidence", "create", "deliver"),
    )
    planning = await repository.reserve(
        operation_id="operation-single-apply",
        task_id="task-single-apply",
        checkpoint_key="episode:4",
        root_run_id="root-single-apply",
        input_digest="sha256:" + "d" * 64,
        reservation_token="planner",
    )
    receipt = await repository.ready(
        operation_id="operation-single-apply",
        checkpoint_key="episode:4",
        plan=plan,
        outcome=ScreenplayCheckpointOutcome.REVISED,
        reservation_owner="planner",
        reservation_epoch=int(planning["reservation_epoch"]),
    )
    downstream_calls = 0
    authoritative_readback_started = asyncio.Event()
    original_root_revision_digest = repository.root_revision_digest

    async def delayed_root_revision_digest(*args, **kwargs):
        result = await original_root_revision_digest(*args, **kwargs)
        if result is not None and not authoritative_readback_started.is_set():
            authoritative_readback_started.set()
            await asyncio.sleep(0.08)
        return result

    repository.root_revision_digest = delayed_root_revision_digest

    async def persist_revision(update):
        nonlocal downstream_calls
        downstream_calls += 1
        await temp_db.execute(
            "INSERT INTO ai_agent_run_events "
            "(run_id, event_type, payload_json) "
            "VALUES (?, 'run.todos_updated', ?)",
            [
                "root-single-apply",
                json.dumps(_root_revision_payload(
                    _canonical_root_plan(update.plan_revision),
                    identity=str(update.plan_revision_metadata["identity"]),
                    digest=str(update.plan_revision_metadata["digest"]),
                )),
            ],
        )

    def observer():
        return _ScreenplayCheckpointObserver(
            dispatcher=SimpleNamespace(_checkpoints=repository),
            planner=SimpleNamespace(),
            downstream=persist_revision,
            task_id="task-single-apply",
            root_run_id="root-single-apply",
            signal=None,
        )

    progress = SimpleNamespace(event=AgentEvent(
        type=CoreEventType.LONG_TASK_PROGRESS,
        payload={"taskId": "task-single-apply"},
    ))
    first = asyncio.create_task(observer()._emit_ready(receipt, progress))
    await asyncio.wait_for(authoritative_readback_started.wait(), timeout=0.2)
    second = asyncio.create_task(observer()._emit_ready(receipt, progress))
    await asyncio.gather(first, second)

    assert downstream_calls == 1
    assert (await repository.load(
        "operation-single-apply",
        "episode:4",
    ))["status"] == "applied"
    event_rows = await temp_db.fetch_all(
        "SELECT id FROM ai_agent_run_events WHERE run_id = ? "
        "AND event_type = 'run.todos_updated'",
        ["root-single-apply"],
    )
    assert len(event_rows) == 1


@pytest.mark.parametrize("mutation", ("completed", "scope", "step_id"))
async def test_checkpoint_planner_only_pauses_for_explicit_reresolution(mutation):
    original = ExecutionPlan(
        title="创作",
        task_spec=_screenplay_task_spec(deliverable="screenplayDraft"),
        steps=_bound_steps("evidence", "create", "deliver"),
    )
    current = replace(original, steps=(
        replace(original.steps[0], status=StepStatus.DONE),
        original.steps[1],
        original.steps[2],
    ))
    if mutation == "completed":
        proposed = replace(current, steps=(
            replace(current.steps[0], title="篡改完成步骤"),
            *current.steps[1:],
        ))
    elif mutation == "step_id":
        proposed = replace(current, steps=(
            current.steps[0],
            replace(current.steps[1], id="replacement"),
            replace(current.steps[2], depends_on=("replacement",)),
        ), work_step_ids=("evidence", "replacement", "deliver"))
    else:
        changed_spec = replace(
            original.task_spec,
            target={"screenplay": {
                "version": 1,
                "scope": {"kind": "next_episodes", "count": 9},
            }},
        )
        proposed = replace(current, task_spec=changed_spec)

    class Models:
        async def run_json(self, **kwargs):
            value = {
                "protocol": CHECKPOINT_PLAN_PROTOCOL,
                "outcome": "revised",
                "plan": _plan_mapping(proposed),
            }
            return StructuredModelResult(kwargs["validate"](value), "child-plan")

    checkpoint = ScreenplayCheckpointInput(
        checkpoint_key="episode:4",
        root_run_id="root-1",
        task_id="task-1",
        turn_id="turn-1",
        project_id="project-1",
        session_id=1,
        target_role="screenplayDraft",
        original_plan=original,
        current_plan=current,
        completed_summaries=(),
        artifact_receipts=(),
        remaining_scope={},
    )
    planner = ScreenplayCheckpointPlanner(Models(), runtime=object())
    if mutation == "scope":
        decision = await planner.revise(checkpoint)
        assert decision.outcome is ScreenplayCheckpointOutcome.REQUIRES_RERESOLUTION
        return
    with pytest.raises(ValueError, match="checkpoint"):
        await planner.revise(checkpoint)


async def test_checkpoint_planner_typed_model_failure_propagates():
    original = ExecutionPlan(
        title="创作",
        task_spec=_screenplay_task_spec(deliverable="screenplayDraft"),
        steps=_bound_steps("evidence", "create", "deliver"),
    )

    class Models:
        async def run_json(self, **_kwargs):
            class TypedFailure(RuntimeError):
                code = "checkpoint_provider_unavailable"

            raise TypedFailure("checkpoint provider unavailable")

    with pytest.raises(RuntimeError, match="provider unavailable") as captured:
        await ScreenplayCheckpointPlanner(
            Models(),
            runtime=object(),
        ).revise(ScreenplayCheckpointInput(
            checkpoint_key="episode:4",
            root_run_id="root-1",
            task_id="task-1",
            turn_id="turn-1",
            project_id="project-1",
            session_id=1,
            target_role="screenplayDraft",
            original_plan=original,
            current_plan=original,
            completed_summaries=(),
            artifact_receipts=(),
        ))
    assert captured.value.code == "checkpoint_provider_unavailable"


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


def _bound_steps(*step_ids: str) -> tuple[TaskStep, ...]:
    return tuple(
        TaskStep(
            id=step_id,
            title=step_id,
            type=StepType.WRITE,
            executor=StepExecutor.MODEL,
            depends_on=((step_ids[index - 1],) if index else ()),
        )
        for index, step_id in enumerate(step_ids)
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


async def test_screenplay_intent_compiles_versioned_task_spec_without_plan_shaping():
    intent = ScreenplayIntent.from_task_spec(
        _screenplay_task_spec(),
        _semantic_steps(),
    )

    assert intent.action is ScreenplayIntentAction.CREATE
    assert intent.instruction == "梳理人物、事件与可改编冲突。"
    assert intent.requested_deliverable == "sourceAnalysis"
    assert intent.scope.kind is ScreenplayScopeKind.CURRENT_STAGE
    assert not hasattr(intent, "plan_bindings")
    assert "reply" not in intent.to_mapping()
    assert ScreenplayIntent.from_mapping(intent.to_mapping()) == intent


async def test_answer_task_spec_needs_no_reply_and_rejects_legacy_fields():
    task_spec = _screenplay_task_spec(operation="answer", deliverable=None)

    intent = ScreenplayIntent.from_task_spec(task_spec, _semantic_steps())

    assert intent.action is ScreenplayIntentAction.ANSWER
    assert not hasattr(intent, "reply")
    with pytest.raises(ValueError, match="intent fields"):
        ScreenplayIntent.from_mapping({
            "action": "answer",
            "instruction": "说明当前阶段",
            "reply": "旧持久化回复不能完成新 Turn",
        })


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
    ],
)
async def test_screenplay_task_spec_fails_closed_for_unknown_or_incompatible_contracts(
    mutate,
    message,
):
    raw = {
        "version": 1,
        "scope": {"kind": "current_stage"},
    }
    mutate(raw)

    with pytest.raises(ValueError, match=message):
        ScreenplayIntent.from_task_spec(
            _screenplay_task_spec(screenplay=raw),
            _semantic_steps(),
        )


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
            "dependencyPartKeys": ["section:premise", "section:characters"],
        },
    ))

    assert restored_root.is_root is True
    assert restored_root.is_child is False
    assert restored_root.turn_id == "turn-1"
    assert restored_root.stage_command == stage_command
    assert legacy_child.is_root is False
    assert legacy_child.is_child is True
    assert legacy_child.task_id == "task-1"
    assert legacy_child.dependency_part_keys == (
        "section:premise",
        "section:characters",
    )
    assert legacy_child.to_core_context().payload["dependencyPartKeys"] == [
        "section:premise",
        "section:characters",
    ]

    with pytest.raises(ValueError, match="Root context cannot carry dependencies"):
        ScreenplayAgentDomainContext(
            project_id="project-1",
            turn_id="turn-1",
            dependency_part_keys=("section:premise",),
        )


async def test_screenplay_child_context_round_trips_revision_and_episode_scope():
    child = ScreenplayAgentDomainContext(
        project_id="project-1",
        task_id="task-1",
        unit_id="draft:2:scene-1",
        target_role="screenplayDraft",
        expected_part_type="scene",
        expected_part_key="scene-1",
        deliverable_revision_scope={
            "sceneList": "scene-list-1",
            "screenplayDraft": "draft-1",
        },
        episode_number=2,
        tool_access="draft_scene",
    )
    payload = child.to_core_context().payload
    restored = ScreenplayAgentDomainContext.from_core_context(
        child.to_core_context()
    )

    assert payload["boundEpisodeNumber"] == 2
    assert restored.episode_number == 2
    assert restored.deliverable_revision_scope == {
        "sceneList": "scene-list-1",
        "screenplayDraft": "draft-1",
    }


@pytest.mark.parametrize("reasoning_effort", (None, "high"))
async def test_tool_calling_service_preserves_bound_revision_and_episode_scope(
    temp_db: DatabaseConnection,
    monkeypatch: pytest.MonkeyPatch,
    reasoning_effort,
):
    captured = {}

    async def fake_child(**kwargs):
        assert kwargs["request"].model.max_generation_tokens == 32_768
        assert kwargs["request"].model.options.get("reasoning_effort") == (
            reasoning_effort
        )
        assert kwargs["request"].metadata["responseAudience"] == "internal"
        assert kwargs["request"].metadata[
            "screenplaySceneIdsByEpisode"
        ] == {
            "1": ["ep01-scene-1"],
            "2": ["ep02-scene-1", "ep02-scene-2"],
        }
        assert "ep01-scene-1" not in kwargs["request"].latest_user_text()
        captured["payload"] = thaw_json_mapping(
            kwargs["request"].domain_context.payload
        )
        captured["binding"] = dict(
            kwargs["options"].binding.attributes[
                "candidateCompletionProjection"
            ]["scope"]
        )
        return AgentRunResult(
            run_id="run-bound-scope",
            status=RunStatus.DONE,
            final_response="",
            model="fixture-model",
        )

    class Candidates:
        async def load_run(self, run_id):
            assert run_id == "run-bound-scope"
            return {
                "_artifact": SimpleNamespace(status=ArtifactStatus.FINALIZED),
                "payload": {"sceneId": "scene-1", "sceneText": "正文"},
                "contentText": "正文",
            }

    monkeypatch.setattr(screenplay_tool_calling, "run_screenplay_child", fake_child)
    service = object.__new__(ScreenplayToolCallingService)
    service._db = temp_db
    service._runs = object()
    service._candidates = Candidates()
    context = ScreenplayAgentDomainContext(
        project_id="project-1",
        task_id="task-1",
        unit_id="draft:2:scene-1",
        target_role="screenplayDraft",
        expected_part_type="scene",
        expected_part_key="scene-1",
        deliverable_revision_scope={
            "sceneList": "scene-list-1",
            "screenplayDraft": "draft-1",
        },
        episode_number=2,
        tool_access="draft_scene",
    )

    runtime = _request(1, "测试绑定").runtime
    runtime.baseURL = "https://api.deepseek.com"
    runtime.options.update({
        "model": "deepseek-v4-flash",
        "thinking": {"type": "enabled"},
    })
    if reasoning_effort is not None:
        runtime.options["reasoning_effort"] = reasoning_effort
    await service.run_candidate(
        runtime=runtime,
        session_id=1,
        system_instruction="只写当前场景",
        user_payload={"episodeNumber": 2},
        domain_context=context,
        conversation_turn_id="turn-1",
        host_candidate_template={"sceneId": "scene-1"},
        candidate_validation_contract={
            "protocol": "purrtypos.screenplay.candidate-validation/v1",
            "kind": "scene",
            "expectedSceneId": "scene-1",
        },
        scene_ids_by_episode={
            1: ("ep01-scene-1",),
            2: ("ep02-scene-1", "ep02-scene-2"),
        },
    )

    for payload in (captured["payload"], captured["binding"]):
        assert payload["boundEpisodeNumber"] == 2
        assert payload["deliverableRevisionScope"] == {
            "sceneList": "scene-list-1",
            "screenplayDraft": "draft-1",
        }


async def test_screenplay_child_part_context_never_generates_a_public_plan():
    request = AgentRunRequest(
        messages=(AgentMessage(role="user", content="只生成当前 Part"),),
        model=ModelRequest(provider="openai", model="fixture-model"),
        domain_context=ScreenplayAgentDomainContext(
            project_id="project-1",
            task_id="task-1",
            unit_id="unit-1",
            target_role="screenplayDraft",
            expected_part_type="scene",
            expected_part_key="scene-1",
        ).to_core_context(),
        mode="agent",
        tools_enabled=True,
    )

    assert not hasattr(ScreenplayToolLoopPolicy(), "should_plan")


async def test_screenplay_execution_state_preserves_every_host_bound_scope():
    request = AgentRunRequest(
        messages=(AgentMessage(role="user", content="只生成当前 Part"),),
        model=ModelRequest(provider="openai", model="fixture-model"),
        domain_context=ScreenplayAgentDomainContext(
            project_id="project-1",
            task_id="task-1",
            unit_id="draft:2:scene-1",
            target_role="screenplayDraft",
            expected_part_type="scene",
            expected_part_key="scene-1",
            dependency_part_keys=("section:structure:index",),
            deliverable_revision_scope={
                "sceneList": "scene-list-1",
                "screenplayDraft": "draft-1",
            },
            episode_number=2,
        ).to_core_context(),
        mode="agent",
        tools_enabled=True,
    )

    state = ScreenplayExecutionStateFactory().create(request)

    assert state.domain["dependencyPartKeys"] == ["section:structure:index"]
    assert state.domain["deliverableRevisionScope"] == {
        "sceneList": "scene-list-1",
        "screenplayDraft": "draft-1",
    }
    assert state.domain["boundEpisodeNumber"] == 2


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
        plan_steps=_bound_steps("understand", "draft", "deliver"),
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
    assert steps[3].depends_on == (steps[1].id, steps[2].id)
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
    assert compiled.recipe.metadata["recipeVersion"] == 10
    assert compiled.manifest.digest == repeated.manifest.digest
    assert [part.id for part in compiled.manifest.parts] == [
        part.id for part in repeated.manifest.parts
    ]


@pytest.mark.parametrize("recipe_version", [7, 8])
async def test_screenplay_continuation_rejects_obsolete_recipe(recipe_version):
    with pytest.raises(ValueError, match="Recipe version is unsupported"):
        _execution_recipe_from_metadata({
            "kind": "screenplay.screenplayDraft",
            "recipeVersion": recipe_version,
            "maxParallelism": 1,
            "steps": [{
                "id": "draft:1:scene-1",
                "kind": "generate_draft_scene",
                "executor": "screenplay.task.unit",
            }],
        })


async def test_scene_list_manifest_persists_episode_specific_part_identity():
    compiled = compile_screenplay_manifest(
        intent=ScreenplayIntent(
            action=ScreenplayIntentAction.CREATE,
            instruction="规划两集场景表",
            requested_deliverable="sceneList",
        ),
        target_role="sceneList",
        source_revision_refs=("structure-head",),
        document_sections=("episode-1", "episode-2"),
        plan_steps=_bound_steps("read", "plan", "deliver"),
    )
    episode_steps = [
        step for step in compiled.recipe.steps
        if step.kind == "generate_document_section"
    ]

    assert [step.metadata["input"]["episodeNumber"] for step in episode_steps] == [
        1,
        2,
    ]
    assert all(
        step.metadata["partContractKey"] == "scene_list_episode"
        for step in episode_steps
    )


async def test_manifest_maps_one_model_authored_step_without_padding_the_plan():
    plan_steps = _bound_steps("完成用户要求")
    compiled = compile_screenplay_manifest(
        intent=ScreenplayIntent(
            action=ScreenplayIntentAction.CREATE,
            instruction="形成创意简报",
            requested_deliverable="creativeBrief",
        ),
        target_role="creativeBrief",
        document_sections=("premise",),
        plan_steps=plan_steps,
    )

    assert {step.plan_step_id for step in compiled.recipe.steps} == {
        plan_steps[0].id,
    }


async def test_multi_episode_recipe_keeps_domain_dependencies_while_mapping_plan():
    compiled = compile_screenplay_manifest(
        intent=ScreenplayIntent(
            action=ScreenplayIntentAction.CREATE,
            instruction="连续创作第 4 至 5 集",
            requested_deliverable="screenplayDraft",
        ),
        target_role="screenplayDraft",
        episode_scene_ids={
            4: ("ep04_s01",),
            5: ("ep05_s01",),
        },
        plan_steps=_bound_steps(
            "read-sources",
            "write-episodes",
            "deliver-result",
        ),
    )

    steps = {step.id: step for step in compiled.recipe.steps}
    assert steps["evidence:4"].depends_on == ()
    assert steps["draft:4:ep04_s01"].depends_on == ("evidence:4",)
    assert steps["evidence:5"].depends_on == ("episode:4:validation",)
    assert steps["draft:5:ep05_s01"].depends_on == ("evidence:5",)
    assert steps["compose-final-response"].depends_on == (
        "episode:4:validation",
        "episode:5:validation",
    )
    assert {step.plan_step_id for step in compiled.recipe.steps} == {
        "read-sources",
        "write-episodes",
        "deliver-result",
    }


async def test_manifest_mapping_follows_model_plan_order_without_rewriting_dag():
    plan_steps = _semantic_steps()
    compiled = compile_screenplay_manifest(
        intent=ScreenplayIntent(
            action=ScreenplayIntentAction.CREATE,
            instruction="连续创作第 4 至 5 集",
            requested_deliverable="screenplayDraft",
        ),
        target_role="screenplayDraft",
        episode_scene_ids={
            4: ("ep04_s01",),
            5: ("ep05_s01",),
        },
        plan_steps=plan_steps,
    )

    steps = compiled.recipe.steps
    assert tuple(dict.fromkeys(step.plan_step_id for step in steps)) == tuple(
        step.id for step in plan_steps
    )
    by_id = {step.id: step for step in steps}
    assert by_id["compose-final-response"].depends_on == (
        "episode:4:validation",
        "episode:5:validation",
    )


async def test_source_analysis_recipe_exposes_semantic_progress_titles():
    compiled = compile_screenplay_manifest(
        intent=ScreenplayIntent(
            action=ScreenplayIntentAction.CREATE,
            instruction="分析原作范围",
            requested_deliverable="sourceAnalysis",
        ),
        target_role="sourceAnalysis",
        source_chapters=({
            "id": "chapter-1",
            "title": "第一章",
            "index": 1,
        },),
        plan_steps=_bound_steps("understand", "analyze", "deliver"),
    )

    assert [step.metadata["displayTitle"] for step in compiled.recipe.steps] == [
        "准备原作范围",
        "分析第 1 章",
        "分析人物",
        "梳理故事",
        "分析世界观",
        "提炼主题",
        "评估改编风险",
        "检查分析结果",
        "生成回复",
    ]


async def test_source_analysis_manifest_compiles_one_digest_per_authorized_chapter():
    chapters = tuple({
        "id": f"chapter-{number}",
        "title": f"第 {number} 章",
        "index": number,
    } for number in range(1, 13))
    compiled = compile_screenplay_manifest(
        intent=ScreenplayIntent(
            action=ScreenplayIntentAction.CREATE,
            instruction="分析授权原作",
            requested_deliverable="sourceAnalysis",
        ),
        target_role="sourceAnalysis",
        source_chapters=chapters,
        plan_steps=_bound_steps("read", "analyze", "deliver"),
    )

    by_id = {step.id: step for step in compiled.recipe.steps}
    chapter_ids = tuple(f"source-analysis:chapter:chapter-{number}" for number in range(1, 13))
    assert not any(step.id.startswith("source-analysis:reduce:") for step in by_id.values())
    assert all(
        by_id[part_id].metadata["partContractKey"]
        == "source_analysis.chapter_digest"
        for part_id in chapter_ids
    )
    for section in (
        "characters",
        "story",
        "world",
        "themes",
        "adaptation_risks",
    ):
        assert by_id[f"section:sourceAnalysis:{section}"].depends_on == chapter_ids


async def test_source_analysis_manifest_builds_fixed_twelve_way_reduction_tree():
    chapters = tuple({
        "id": f"chapter-{number}",
        "title": f"第 {number} 章",
        "index": number,
    } for number in range(1, 146))
    compiled = compile_screenplay_manifest(
        intent=ScreenplayIntent(
            action=ScreenplayIntentAction.CREATE,
            instruction="分析长篇原作",
            requested_deliverable="sourceAnalysis",
        ),
        target_role="sourceAnalysis",
        source_chapters=chapters,
        plan_steps=_bound_steps("read", "analyze", "deliver"),
    )

    reductions = [
        step for step in compiled.recipe.steps
        if step.id.startswith("source-analysis:reduce:")
    ]
    assert len(reductions) == 15
    assert all(1 <= len(step.depends_on) <= 12 for step in reductions)
    assert all(
        step.metadata["partContractKey"]
        == "source_analysis.digest_reduction"
        for step in reductions
    )
    final_frontier = (
        "source-analysis:reduce:2:1",
        "source-analysis:reduce:2:2",
    )
    for section in (
        "characters",
        "story",
        "world",
        "themes",
        "adaptation_risks",
    ):
        step = next(
            value for value in compiled.recipe.steps
            if value.id == f"section:sourceAnalysis:{section}"
        )
        assert step.depends_on == final_frontier
    assert compiled.recipe.max_parallelism == 12


async def test_source_analysis_digest_and_final_sections_have_strict_shapes():
    protocol = "purrtypos.screenplay.candidate-validation/v1"
    digest = normalize_screenplay_candidate(
        {
            "protocol": protocol,
            "kind": "source_chapter_digest",
            "chapterId": "chapter-1",
        },
        {
            "payload": {
                "sectionKey": "source_digest:chapter:chapter-1",
                "title": "第一章摘要",
                "contentJson": {
                    "chapterId": "chapter-1",
                    "summary": "主角发现失踪线索。",
                    "characters": [{"key": "lead", "state": "开始调查"}],
                    "events": [{
                        "key": "clue-found",
                        "summary": "发现线索",
                        "consequence": "调查启动",
                    }],
                    "worldFacts": [{"key": "rule", "summary": "夜间停电"}],
                    "themes": ["信任"],
                    "plotThreads": [{"key": "missing", "state": "opened"}],
                    "adaptationRisks": ["内心活动需要视觉化"],
                },
            },
            "contentText": "主角发现失踪线索。",
        },
    )
    assert digest["payload"]["contentJson"]["chapterId"] == "chapter-1"

    with pytest.raises(ValueError, match="source chapter digest"):
        normalize_screenplay_candidate(
            {
                "protocol": protocol,
                "kind": "source_chapter_digest",
                "chapterId": "chapter-1",
            },
            {
                **digest,
                "payload": {
                    **digest["payload"],
                    "contentJson": {
                        **digest["payload"]["contentJson"],
                        "episodes": [{"number": 1}],
                    },
                },
            },
        )

    valid_sections = {
        "characters": {"characters": []},
        "story": {"story": {"beats": [], "openThreads": []}},
        "world": {"world": {"rules": [], "locations": [], "factions": []}},
        "themes": {"themes": []},
        "adaptation_risks": {"adaptationRisks": []},
    }
    for section, content in valid_sections.items():
        normalized = normalize_screenplay_candidate(
            {
                "protocol": protocol,
                "kind": "source_analysis_section",
                "sectionKey": section,
            },
            {
                "payload": {
                    "sectionKey": section,
                    "title": section,
                    "contentJson": content,
                },
                "contentText": section,
            },
        )
        assert normalized["payload"]["contentJson"] == content


async def test_source_analysis_assembly_excludes_internal_digest_parts():
    task = {
        "targetRole": "sourceAnalysis",
        "units": [{
            "id": "document:evidence",
            "kind": "collect_evidence",
            "status": "completed",
            "input": {"targetRole": "sourceAnalysis"},
            "output": {"evidenceDescriptor": {
                "projectId": "project-source",
                "targetRole": "sourceAnalysis",
            }},
        }, {
            "id": "source-analysis:chapter:chapter-1",
            "kind": "generate_document_section",
            "status": "completed",
            "input": {
                "sectionKey": "source_digest:chapter:chapter-1",
                "sourceChapterDigest": True,
            },
            "output": {
                "contentText": "内部逐章摘要不得进入交付物",
                "contentJson": {"chapterId": "chapter-1"},
                "runId": "run-chapter-1",
            },
        }],
    }
    sections = {
        "characters": {"characters": []},
        "story": {"story": {"beats": [], "openThreads": []}},
        "world": {"world": {"rules": [], "locations": [], "factions": []}},
        "themes": {"themes": []},
        "adaptation_risks": {"adaptationRisks": []},
    }
    for position, (section, content) in enumerate(sections.items(), start=1):
        task["units"].append({
            "id": f"section:sourceAnalysis:{section}",
            "kind": "generate_document_section",
            "status": "completed",
            "input": {"sectionKey": section},
            "output": {
                "sectionKey": section,
                "title": section,
                "contentText": f"{section} 正文",
                "contentJson": content,
                "runId": f"run-section-{position}",
                "sourceRunIds": ["run-chapter-1"],
            },
        })
    validation = {
        "id": "document:validation",
        "kind": "validate_manifest_part",
        "dependsOn": [
            f"section:sourceAnalysis:{section}" for section in sections
        ],
        "input": {"validationKind": "document", "targetRole": "sourceAnalysis"},
    }
    task["units"].append(validation)

    result = _validate_document_parts(task, validation)

    assert "内部逐章摘要" not in result["contentText"]
    assert result["contentJson"] == {
        "characters": [],
        "story": {"beats": [], "openThreads": []},
        "world": {"rules": [], "locations": [], "factions": []},
        "themes": [],
        "adaptationRisks": [],
        "schemaVersion": 1,
        "documentKind": "source_analysis",
    }
    assert result["sourceRunIds"] == (
        "run-section-1",
        "run-chapter-1",
        "run-section-2",
        "run-section-3",
        "run-section-4",
        "run-section-5",
    )


async def test_structure_manifest_persists_index_before_episode_part_expansion():
    compiled = compile_screenplay_manifest(
        intent=ScreenplayIntent(
            action=ScreenplayIntentAction.CREATE,
            instruction="设计分集结构",
            requested_deliverable="structure",
        ),
        target_role="structure",
        plan_steps=_bound_steps("read", "design", "deliver"),
    )

    by_id = {step.id: step for step in compiled.recipe.steps}
    assert [step.id for step in compiled.recipe.steps] == [
        "document:evidence",
        "section:structure:series_arc:index",
        "section:structure:series_arc",
        "section:structure:episode_plan:index",
        "section:structure:episode_plan",
        "section:structure:character_arcs:index",
        "section:structure:character_arcs",
        "section:structure:hooks",
        "document:validation",
        "compose-final-response",
    ]
    assert by_id["section:structure:series_arc"].kind == (
        "expand_structure_series_arc"
    )
    assert by_id["section:structure:series_arc"].depends_on == (
        "section:structure:series_arc:index",
    )
    assert by_id["section:structure:episode_plan:index"].kind == (
        "generate_document_section"
    )
    assert by_id["section:structure:episode_plan:index"].depends_on == (
        "section:structure:series_arc",
    )
    assert by_id["section:structure:episode_plan"].kind == (
        "expand_structure_episode_plan"
    )
    assert by_id["section:structure:episode_plan"].depends_on == (
        "section:structure:episode_plan:index",
    )
    assert by_id["section:structure:character_arcs:index"].depends_on == (
        "section:structure:episode_plan",
    )
    assert by_id["section:structure:character_arcs"].kind == (
        "expand_structure_character_arcs"
    )
    assert by_id["section:structure:character_arcs"].depends_on == (
        "section:structure:character_arcs:index",
    )
    assert by_id["section:structure:hooks"].kind == "project_structure_hooks"
    assert by_id["section:structure:hooks"].depends_on == (
        "section:structure:episode_plan",
        "section:structure:character_arcs",
    )
    assert by_id["document:validation"].depends_on == (
        "section:structure:series_arc",
        "section:structure:episode_plan",
        "section:structure:character_arcs",
        "section:structure:hooks",
    )
    assert compiled.recipe.max_parallelism == 12


async def test_compiled_recipe_persists_part_contracts_and_nonempty_budget():
    compiled = compile_screenplay_manifest(
        intent=ScreenplayIntent(
            action=ScreenplayIntentAction.CREATE,
            instruction="设计分集结构",
            requested_deliverable="structure",
        ),
        target_role="structure",
        plan_steps=_bound_steps("read", "design", "deliver"),
    )

    ai_steps = [
        step for step in compiled.recipe.steps
        if step.kind in {
            "generate_draft_scene",
            "generate_episode_metadata",
            "generate_review_dimension",
            "generate_document_section",
            "compose_final_response",
        }
    ]
    assert ai_steps
    assert all(step.metadata.get("partContractKey") for step in ai_steps)
    assert all(
        "partContractKey" not in step.metadata
        for step in compiled.recipe.steps
        if step not in ai_steps
    )

    limits = screenplay_task_budget_limits(
        compiled.recipe,
        ReasoningMode.ENABLED,
    )
    assert limits.max_invocation_attempts is not None
    assert limits.max_input_tokens is not None
    assert limits.max_run_generation_tokens is None
    assert limits.max_reasoning_tokens is None
    assert limits.max_invocation_attempts > len(ai_steps) * 8
    assert screenplay_max_generated_units(compiled.recipe) == 134


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
        plan_steps=_bound_steps("understand", "review", "deliver"),
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


async def test_deterministic_evidence_part_requires_no_dedicated_run(
    temp_db: DatabaseConnection,
):
    _, workspace, session = await _project_and_session(temp_db)
    executor = ScreenplayTaskModelCalls(
        temp_db,
        tool_calling_service=object(),  # unused by deterministic evidence
    )
    before = await temp_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_runs"
    )

    output = await executor.execute(
        task={
            "id": "task-deterministic-evidence",
            "projectId": workspace["project"]["id"],
            "sessionId": session["id"],
            "turnId": "turn-deterministic-evidence",
            "rootRunId": "root-deterministic-evidence",
            "targetRole": "sourceAnalysis",
            "sourceRevisionRefs": [],
            "units": [],
        },
        unit={
            "id": "evidence:main",
            "kind": "collect_evidence",
            "input": {},
        },
        runtime=object(),
    )

    assert output["evidenceDescriptor"]["projectId"] == workspace["project"]["id"]
    assert await temp_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_runs"
    ) == before


def _admission_plan(
    *,
    operation: str,
    deliverable: str | None,
    phases: tuple[str, ...],
) -> ExecutionPlan:
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
    return ExecutionPlan(
        title="完成本轮剧本任务",
        task_spec=TaskSpec(
            goal="根据本轮要求完成剧本任务",
            operation=operation,
            instruction="根据本轮要求完成剧本任务",
            deliverable=deliverable,
            target={"screenplay": {
                "version": 1,
                "scope": {"kind": "current_stage"},
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
            document_sections=("characters",),
            source_chapters=({
                "id": "chapter-1",
                "title": "第一章",
                "index": 1,
            },),
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
    profile = ScreenplayAgentProfile(
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

    decision = await assert_task_orchestration_conforms(
        evaluator=profile.task_admission(),
        request=request,
        plan=_admission_plan(
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


async def test_product_composition_installs_the_screenplay_profile(
    temp_db: DatabaseConnection,
):
    composition = create_agent_composition(temp_db)
    try:
        assert isinstance(
            composition.profile("screenplay"),
            ScreenplayAgentProfile,
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
    profile = ScreenplayAgentProfile(
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
    profile = ScreenplayAgentProfile(
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

    first = await assert_task_orchestration_conforms(
        evaluator=profile.task_admission(),
        request=request,
        plan=plan,
    )
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
    assert "planMappingDigest" in recipe.metadata
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
    profile = ScreenplayAgentProfile(
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


@pytest.mark.parametrize(
    ("root_status", "expected_status"),
    (
        (RunStatus.FAILED, "failed"),
        (RunStatus.CANCELED, "failed"),
    ),
)
async def test_admitted_operation_settles_when_root_fails_before_dispatch(
    temp_db: DatabaseConnection,
    root_status,
    expected_status,
):
    profile, _request, _plan, decision = await _durable_screenplay_admission(
        temp_db,
        owner_id=f"screenplay-predispatch-{expected_status}",
        command_id=f"predispatch-{expected_status}",
    )
    turn_id = str(decision.metadata["turnId"])
    assert await profile._turns.claim_turn(turn_id)
    root_run_id = f"run-{expected_status}"
    await profile._turns.attach_root_run(turn_id, root_run_id)
    turn = await profile._turns.load_turn(turn_id)
    assert turn is not None
    await temp_db.execute(
        "INSERT INTO ai_agent_runs "
            "(id, session_id, status, mode, prompt, binding_namespace, "
            "binding_aggregate_id, binding_command_id, binding_attributes_json) "
            "VALUES (?, ?, 'running', 'agent', '', ?, ?, ?, ?)",
        [
            root_run_id,
            turn["sessionId"],
            "screenplay.conversation_turn",
            turn["projectId"],
            turn["commandId"],
            json.dumps({
                "agentProfile": "screenplay",
                "domainNamespace": "purrtypos.screenplay",
                }),
        ],
    )
    await temp_db.execute(
        "INSERT INTO ai_agent_run_events "
        "(run_id, event_type, payload_json, event_id, turn_id, sequence, "
        "source, kind, channel, visibility, source_event_key) VALUES "
        "(?, 'run.started', '{\"status\":\"running\"}', ?, ?, 1, "
        "'runtime', 'run.lifecycle', 'lifecycle', 'public', ?)",
        [root_run_id, f"event-{root_run_id}", turn_id, f"run:{root_run_id}:running"],
    )
    lifecycle = _ScreenplayTurnRunLifecycle(
        temp_db,
        profile._turns,
        profile._operations,
        turn_id,
    )

    commit = RunCommit(
        terminal_status=root_status,
        error=(
            f"injected_{expected_status}_before_dispatch"
            if root_status is RunStatus.FAILED else None
        ),
    )
    async with temp_db.transaction(cancellation_linearizable=True):
        await ScreenplayAgentRootCompletionProjector(temp_db).project(
            root_run_id,
            commit,
        )
    await lifecycle.on_run_finished(AgentRunResult(
        run_id=root_run_id,
        status=root_status,
        final_response="",
        error=f"injected_{expected_status}_before_dispatch",
    ))

    operation = await profile._operations.load(decision.metadata["operationId"])
    turn = await profile._turns.load_turn(turn_id)
    assert operation is not None and operation.status.value == expected_status
    assert turn is not None and turn["status"] == expected_status

    await lifecycle.on_run_finished(AgentRunResult(
        run_id=root_run_id,
        status=root_status,
        final_response="",
        error=f"injected_{expected_status}_before_dispatch",
    ))


def _admission_dispatcher(db, profile):
    return profile.create_long_task_dispatcher(
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
            run_id="run-atomic-dispatch",
        )

    assert await temp_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_long_tasks"
    ) == {"count": 0}
    assert await temp_db.fetch_all(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN "
        "('ai_agent_work_items', 'ai_agent_work_item_runs')"
    ) == []
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
        run_id="run-atomic-dispatch",
    )
    replay = await dispatcher.dispatch(
        request,
        plan,
        decision,
        run_id="run-atomic-dispatch",
    )
    assert replay.task_id == first.task_id
    assert await temp_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_long_tasks"
    ) == {"count": 1}
    assert await temp_db.fetch_all(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN "
        "('ai_agent_work_items', 'ai_agent_work_item_runs')"
    ) == []
    assert await temp_db.fetch_one(
        "SELECT COUNT(*) AS count FROM screenplay_agent_operation_commands "
        "WHERE command_type = 'attachLongTask'"
    ) == {"count": 1}
    task = await SqliteLongTaskRepository(temp_db).load(first.task_id)
    assert task is not None
    assert task.budget_limits.max_invocation_attempts is not None
    assert task.budget_limits.max_input_tokens is not None
    assert task.budget_limits.max_run_generation_tokens is None
    assert task.budget_limits.max_reasoning_tokens is None
    assert thaw_json_mapping(task.metadata)["maxGeneratedUnits"] == len(
        decision.execution_recipe.steps
    )
    operation = await profile._operations.load(decision.metadata["operationId"])
    assert operation is not None
    assert operation.status.value == "running"
    assert operation.long_task_id == first.task_id


async def test_screenplay_dispatch_preserves_primary_task_create_error(
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
            run_id="run-create-cleanup",
        )

    assert await temp_db.fetch_all(
        "SELECT name FROM sqlite_master WHERE type = 'table' "
        "AND name = 'ai_agent_work_items'"
    ) == []


@pytest.mark.parametrize(
    "status",
    (LongTaskExecutionStatus.PAUSED, LongTaskExecutionStatus.FAILED),
)
async def test_screenplay_dispatcher_leaves_business_terminal_settlement_to_root_commit(
    temp_db: DatabaseConnection,
    status,
):
    profile, request, plan, decision = await _durable_screenplay_admission(
        temp_db,
        owner_id=f"screenplay-{status.value}-root-settlement-test",
        command_id=f"{status.value}-root-settlement",
    )
    dispatcher = _admission_dispatcher(temp_db, profile)
    receipt = await dispatcher.dispatch(
        request,
        plan,
        decision,
        run_id=f"run-{status.value}-root-settlement",
    )
    result = LongTaskExecutionResult(
        task_id=receipt.task_id,
        status=status,
        error="injected_terminal",
    )
    await dispatcher._settle_execution(receipt.task_id, result)

    operation = await profile._operations.load(decision.metadata["operationId"])
    turn = await profile._turns.load_turn(decision.metadata["turnId"])
    assert operation is not None and operation.status.value == "running"
    assert turn is not None and turn["status"] == "running"


async def test_screenplay_dispatcher_exception_does_not_settle_business_state(
    temp_db: DatabaseConnection,
):
    profile, request, plan, decision = await _durable_screenplay_admission(
        temp_db,
        owner_id="screenplay-exception-atomic-test",
        command_id="exception-atomic",
    )
    dispatcher = _admission_dispatcher(temp_db, profile)
    receipt = await dispatcher.dispatch(
        request,
        plan,
        decision,
        run_id="run-exception-atomic",
    )
    await dispatcher._settle_exception(
        receipt.task_id,
        RuntimeError("unit infrastructure failed"),
    )

    operation = await profile._operations.load(decision.metadata["operationId"])
    turn = await profile._turns.load_turn(decision.metadata["turnId"])
    assert operation is not None and operation.status.value == "running"
    assert turn is not None and turn["status"] == "running"


async def test_turn_start_does_not_emit_a_host_authored_plan(
    temp_db: DatabaseConnection,
):
    projects, workspace, session = await _project_and_session(temp_db)
    service = ScreenplayAgentService(
        temp_db,
        owner_id="no-canned-plan-test",
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


async def test_final_response_unit_metadata_exposes_the_generic_durable_contract():
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

    assert result.metadata == {"finalResponse": "任务已经完成。"}
    assert result.output_ref == (
        "screenplay-part-artifact://artifact-final-response"
    )


async def test_host_part_output_is_a_finalized_artifact_without_shadow_json(
    temp_db: DatabaseConnection,
):
    await temp_db.execute(
        "INSERT INTO ai_agent_long_tasks "
        "(id, namespace, kind, owner_id, created_by_run_id, total_units) "
        "VALUES ('task-artifact-only', 'purrtypos.screenplay', 'screenplay', "
        "'project-artifact-only', 'run-artifact-only', 1)"
    )
    await temp_db.execute(
        "INSERT INTO ai_agent_long_task_units "
        "(task_id, unit_id, semantic_key, position, run_id) VALUES "
        "('task-artifact-only', 'evidence:4', 'evidence:4', 0, "
        "'run-artifact-only')"
    )
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
        "rootRunId": "root-final-response-facts",
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


@pytest.mark.parametrize('context_method', ['build_context', 'build_planning_context'])
async def test_composed_screenplay_root_planning_context_uses_db_facts_without_body_leakage(
    temp_db: DatabaseConnection,
    context_method: str,
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
        bundle = await getattr(provider, context_method)(
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
    assert "id" not in facts["project"]
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
    assert project_id not in serialized
    assert "book-planning-leakage" not in serialized
    assert "chapter-planning-leakage" not in serialized
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
                    "sceneList": {"id": "scene-list-current"},
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
    assert resolved.source_revision_refs == (
        "scene-list-current",
        "draft-current",
    )
    assert resolved.episode_scene_ids == {
        1: ("ep01_s01",),
        2: ("ep02_s01", "ep02_s02"),
    }
    assert context.requested_draft_revision_ids == ["draft-current"]


async def test_late_stage_resolver_binds_only_semantic_input_heads():
    workspace = {"workflow": {"heads": {
        "sourceAnalysis": {"id": "analysis-current"},
        "creativeBrief": {"id": "brief-current"},
        "structure": {"id": "structure-current"},
        "sceneList": {"id": "scene-list-current"},
        "screenplayDraft": {"id": "draft-current"},
        "review": {"id": "review-unrelated"},
    }}}

    assert SqliteScreenplayTaskResolver._late_stage_revision_refs(
        workspace,
        "sceneList",
        base_revision_id="scene-list-current",
    ) == ("structure-current",)
    assert SqliteScreenplayTaskResolver._late_stage_revision_refs(
        workspace,
        "screenplayDraft",
        base_revision_id="draft-current",
    ) == (
        "analysis-current",
        "brief-current",
        "structure-current",
        "scene-list-current",
        "draft-current",
    )
    assert SqliteScreenplayTaskResolver._late_stage_revision_refs(
        workspace,
        "review",
        base_revision_id=None,
    ) == ("scene-list-current", "draft-current")


@pytest.mark.parametrize(
    ("source_kind", "action", "heads", "expected"),
    [
        (
            "book",
            ScreenplayIntentAction.CREATE,
            {
                "sourceAnalysis": {"id": "analysis-accepted"},
                "screenplayDraft": {"id": "draft-unrelated"},
            },
            ("analysis-accepted",),
        ),
        (
            "original",
            ScreenplayIntentAction.REVISE,
            {
                "creativeBrief": {"id": "brief-baseline"},
                "review": {"id": "review-unrelated"},
            },
            ("brief-baseline",),
        ),
    ],
)
async def test_creative_brief_resolver_exposes_only_analysis_and_locked_baseline(
    source_kind,
    action,
    heads,
    expected,
):
    class Context:
        async def head_revision_refs(self, project_id):
            raise AssertionError(
                f"creative brief widened {project_id} to every accepted head"
            )

    resolver = object.__new__(SqliteScreenplayTaskResolver)
    resolver._context = Context()
    resolved = await resolver.resolve(
        workspace={
            "project": {
                "id": "project-creative-brief-scope",
                "source": {"type": source_kind},
            },
            "workflow": {"stage": "brief", "heads": heads},
            "deliverables": [{"role": "creativeBrief"}],
            "candidates": [],
        },
        intent=ScreenplayIntent(
            action=action,
            instruction="形成创作简报",
            requested_deliverable="creativeBrief",
        ),
    )

    assert resolved.source_revision_refs == expected


async def test_source_analysis_resolver_uses_only_authorized_leaf_identities():
    class Context:
        async def head_revision_refs(self, project_id):
            assert project_id == "project-source"
            return ()

        async def source_chapter_identities(self, project_id):
            assert project_id == "project-source"
            return (
                {"id": "chapter-2", "title": "第二章", "index": 2},
                {"id": "chapter-5", "title": "第五章", "index": 5},
            )

    resolver = object.__new__(SqliteScreenplayTaskResolver)
    resolver._context = Context()
    resolved = await resolver.resolve(
        workspace={
            "project": {
                "id": "project-source",
                "source": {
                    "type": "book",
                    "scope": {
                        "mode": "selected_chapters",
                        "chapterIds": ["chapter-2", "chapter-5"],
                    },
                },
            },
            "workflow": {"stage": "sourceAnalysis", "heads": {}},
            "deliverables": [{"role": "sourceAnalysis"}],
            "candidates": [],
        },
        intent=ScreenplayIntent(
            action=ScreenplayIntentAction.CREATE,
            instruction="分析授权章节",
            requested_deliverable="sourceAnalysis",
        ),
    )

    assert resolved.source_chapters == (
        {"id": "chapter-2", "title": "第二章", "index": 2},
        {"id": "chapter-5", "title": "第五章", "index": 5},
    )


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
                "model_profile": "deepseek:deepseek-v4-flash", "profile_binding": "compatible",
                "max_generation_tokens": 32_768,
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
        "model_profile": "deepseek:deepseek-v4-flash", "profile_binding": "compatible",
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


async def test_resumed_task_view_uses_the_active_continuation_root():
    class _Parts:
        async def list_task_outputs(self, task_id):
            assert task_id == "task-resumed"
            return {}

    executor = object.__new__(ScreenplayTaskUnitExecutor)
    executor._parts = _Parts()
    executor._long_tasks = SimpleNamespace(
        list_units=lambda task_id: asyncio.sleep(0, result=())
    )
    context = SimpleNamespace(
        run_id="run-continuation",
        task=SimpleNamespace(
            id="task-resumed",
            created_by_run_id="run-canceled-source",
            metadata={
                "projectId": "project-1",
                "sessionId": 1,
                "turnId": "turn-1",
                "targetRole": "creativeBrief",
                "recipe": {"steps": []},
            },
        ),
    )

    task = await executor._task_view(context)

    assert task["rootRunId"] == "run-continuation"


async def test_structure_episode_expansion_creates_stable_persisted_children():
    executor = object.__new__(ScreenplayTaskUnitExecutor)
    parent = SimpleNamespace(
        id="section:structure:episode_plan",
        metadata={
            "input": {
                "targetRole": "structure",
                "sectionKey": "episode_plan",
                "splitStrategy": "structure_episode_plan",
                "instruction": "设计分集结构",
                "constraints": [],
                "preserve": [],
                "baseRevisionId": None,
            },
            "plannerStepId": "design",
        },
    )
    context = SimpleNamespace(
        unit=parent,
        task=SimpleNamespace(metadata={"maxGeneratedUnits": 134, "recipe": {"steps": [
            {"id": "document:evidence"},
            {"id": "section:structure:series_arc:index"},
            {"id": "section:structure:series_arc"},
            {"id": "section:structure:episode_plan:index"},
            {"id": "section:structure:episode_plan"},
            {"id": "section:structure:character_arcs:index"},
            {"id": "section:structure:character_arcs"},
            {"id": "section:structure:hooks"},
            {"id": "document:validation"},
            {"id": "compose-final-response"},
        ]}}),
    )
    error = StructurePartSplit(
        "structure_episode_plan",
        (
            {
                "number": 1,
                "id": "ep01",
                "title": "误入犬域",
                "summary": "进入异空间。",
            },
            {
                "number": 2,
                "id": "ep02",
                "title": "绝境觉醒",
                "summary": "首次能力爆发。",
            },
        ),
        index_run_id="run-episode-index",
    )

    decision = decide_failure(
        executor.classify_failure(error),
        attempts_remaining=3,
    )
    split = executor.split_unit(context, error)

    assert decision.disposition is FailureDisposition.SPLIT_PART
    assert [child.id for child in split.children] == [
        "section:structure:episode_plan:episode-1",
        "section:structure:episode_plan:episode-2",
    ]
    assert split.replacement_dependency_ids == tuple(
        child.id for child in split.children
    )
    assert [child.position for child in split.children] == [22, 23]
    assert split.children[0].dependencies == (
        "document:evidence",
        "section:structure:episode_plan:index",
    )
    first_input = thaw_json_mapping(split.children[0].metadata)["input"]
    assert first_input["sectionKey"] == "episode_plan:episode-1"
    assert first_input["documentSectionKey"] == "episode_plan"
    assert first_input["episodeId"] == "ep01"
    assert first_input["episodeTitle"] == "误入犬域"
    assert "sourceIndexRunId" not in first_input
    assert "episodePlanEntry" not in first_input
    assert thaw_json_mapping(split.children[0].metadata)[
        "partContractKey"
    ] == "structure.episode_plan_fragment"


@pytest.mark.parametrize(
    ("strategy", "parent_id", "entries", "dependency_ids", "expected"),
    [
        (
            "structure_series_arc",
            "section:structure:series_arc",
            ({
                "key": "setup",
                "title": "误入犬域",
                "objective": "建立目标与规则。",
            },),
            (),
            (
                "section:structure:series_arc:phase:setup",
                10,
                (
                    "document:evidence",
                    "section:structure:series_arc:index",
                ),
                "structure.series_arc_phase",
            ),
        ),
        (
            "structure_character_arcs",
            "section:structure:character_arcs",
            ({"key": "linyue", "name": "林月"},),
            (
                "section:structure:episode_plan:episode-1",
                "section:structure:episode_plan:episode-2",
            ),
            (
                "section:structure:character_arcs:character:linyue",
                122,
                (
                    "document:evidence",
                    "section:structure:character_arcs:index",
                    "section:structure:episode_plan:episode-1",
                    "section:structure:episode_plan:episode-2",
                ),
                "structure.character_arc_fragment",
            ),
        ),
    ],
)
async def test_structure_other_expansions_reuse_durable_split_protocol(
    strategy,
    parent_id,
    entries,
    dependency_ids,
    expected,
):
    recipe_ids = [
        "document:evidence",
        "section:structure:series_arc:index",
        "section:structure:series_arc",
        "section:structure:episode_plan:index",
        "section:structure:episode_plan",
        "section:structure:character_arcs:index",
        "section:structure:character_arcs",
        "section:structure:hooks",
        "document:validation",
        "compose-final-response",
    ]
    parent = SimpleNamespace(
        id=parent_id,
        metadata={
            "input": {
                "targetRole": "structure",
                "sectionKey": parent_id.rsplit(":", 1)[-1],
                "splitStrategy": strategy,
            },
            "plannerStepId": "design",
        },
    )
    context = SimpleNamespace(
        unit=parent,
        task=SimpleNamespace(metadata={
            "maxGeneratedUnits": 134,
            "recipe": {"steps": [{"id": value} for value in recipe_ids]},
        }),
    )
    error = StructurePartSplit(
        strategy,
        entries,
        index_run_id="run-index",
        dependency_ids=dependency_ids,
    )

    split = object.__new__(ScreenplayTaskUnitExecutor).split_unit(context, error)
    child = split.children[0]
    child_metadata = thaw_json_mapping(child.metadata)

    assert (child.id, child.position, child.dependencies) == expected[:3]
    assert child_metadata["partContractKey"] == expected[3]
    assert split.replacement_dependency_ids == (child.id,)


async def test_structure_episode_expansion_rejects_first_unit_over_scope():
    task = {
        "maxGeneratedUnits": 2,
        "units": [
            {
                "id": "section:structure:episode_plan:index",
                "kind": "generate_document_section",
                "status": "completed",
                "input": {"episodePlanIndex": True},
                "output": {
                    "contentJson": {"episodes": [
                        {"number": 1, "id": "ep01", "title": "第一集"},
                        {"number": 2, "id": "ep02", "title": "第二集"},
                    ]},
                    "runId": "run-episode-index",
                },
            },
            {
                "id": "section:structure:episode_plan",
                "kind": "expand_structure_episode_plan",
                "status": "running",
                "dependsOn": ["section:structure:episode_plan:index"],
                "input": {
                    "sectionKey": "episode_plan",
                    "splitStrategy": "structure_episode_plan",
                },
            },
        ],
    }

    with pytest.raises(ValueError, match="screenplay_task_scope_too_large"):
        ScreenplayTaskModelCalls._expand_structure_part(
            task,
            task["units"][1],
        )


async def test_structure_character_expansion_binds_completed_episode_parts():
    task = {
        "maxGeneratedUnits": 134,
        "units": [
            {
                "id": "section:structure:character_arcs:index",
                "kind": "generate_document_section",
                "status": "completed",
                "input": {"characterArcsIndex": True},
                "output": {
                    "contentJson": {"characters": [
                        {"key": "linyue", "name": "林月"},
                        {"key": "suwen", "name": "苏文"},
                    ]},
                    "runId": "run-character-index",
                },
            },
            {
                "id": "section:structure:episode_plan:episode-1",
                "kind": "generate_document_section",
                "status": "completed",
                "input": {
                    "documentSectionKey": "episode_plan",
                    "episodeNumber": 1,
                },
                "output": {"contentJson": {"episodes": [{"id": "ep01"}]}},
            },
            {
                "id": "section:structure:character_arcs",
                "kind": "expand_structure_character_arcs",
                "status": "running",
                "dependsOn": ["section:structure:character_arcs:index"],
                "input": {
                    "sectionKey": "character_arcs",
                    "splitStrategy": "structure_character_arcs",
                },
            },
        ],
    }

    with pytest.raises(StructurePartSplit) as raised:
        ScreenplayTaskModelCalls._expand_structure_part(
            task,
            task["units"][-1],
        )

    assert raised.value.strategy == "structure_character_arcs"
    assert [entry["key"] for entry in raised.value.entries] == [
        "linyue",
        "suwen",
    ]
    assert raised.value.dependency_ids == (
        "section:structure:episode_plan:episode-1",
    )


async def test_structure_hooks_are_projected_without_a_model_run():
    task = {
        "units": [
            {
                "id": "section:structure:episode_plan:episode-2",
                "status": "completed",
                "input": {
                    "documentSectionKey": "episode_plan",
                    "episodeNumber": 2,
                },
                "output": {
                    "contentJson": {"episodes": [{
                        "number": 2,
                        "id": "ep02",
                        "hook": "苏文突然惊醒。",
                    }]},
                    "sourceRunIds": ["run-episode-2"],
                },
            },
            {
                "id": "section:structure:episode_plan:episode-1",
                "status": "completed",
                "input": {
                    "documentSectionKey": "episode_plan",
                    "episodeNumber": 1,
                },
                "output": {
                    "contentJson": {"episodes": [{
                        "number": 1,
                        "id": "ep01",
                        "hook": "异响再次出现。",
                    }]},
                    "runId": "run-episode-1",
                },
            },
            {
                "id": "section:structure:character_arcs:character:linyue",
                "status": "completed",
                "input": {"documentSectionKey": "character_arcs"},
                "output": {
                    "contentJson": {"characterArcs": [{"key": "linyue"}]},
                    "runId": "run-character-linyue",
                },
            },
        ],
    }
    unit = {
        "kind": "project_structure_hooks",
        "dependsOn": [item["id"] for item in task["units"]],
        "input": {"sectionKey": "hooks"},
    }

    output = await ScreenplayTaskModelCalls.execute(
        object.__new__(ScreenplayTaskModelCalls),
        task=task,
        unit=unit,
        runtime=object(),
    )

    assert output["contentJson"]["hooks"] == [
        {"episodeId": "ep01", "hook": "异响再次出现。"},
        {"episodeId": "ep02", "hook": "苏文突然惊醒。"},
    ]
    assert output["sourceRunIds"] == [
        "run-episode-2",
        "run-episode-1",
        "run-character-linyue",
    ]


async def test_structure_episode_children_atomically_replace_parent_dependency(
    temp_db: DatabaseConnection,
):
    repository = SqliteLongTaskRepository(temp_db)
    parent_id = "section:structure:episode_plan"
    task = await repository.create(
        "task-structure-episode-expansion",
        LongTaskCreateCommand(
            namespace="purrtypos.screenplay",
            kind="screenplay.structure",
            owner_id="project-structure-expansion",
            created_by_run_id="run-root-structure-expansion",
            units=(
                LongTaskUnitSpec(
                    id=parent_id,
                    position=0,
                    metadata={
                        "executor": "screenplay",
                        "unitKind": "expand_structure_episode_plan",
                        "plannerStepId": "design",
                        "input": {
                            "targetRole": "structure",
                            "sectionKey": "episode_plan",
                            "splitStrategy": "structure_episode_plan",
                        },
                    },
                ),
                LongTaskUnitSpec(
                    id="document:validation",
                    position=1,
                    dependencies=(parent_id,),
                    metadata={
                        "executor": "screenplay",
                        "unitKind": "validate_manifest_part",
                        "input": {"validationKind": "document"},
                    },
                ),
            ),
            metadata={"maxGeneratedUnits": 131, "recipe": {"steps": [
                {"id": parent_id},
                {"id": "document:validation"},
            ]}},
        ),
    )
    task = await repository.start(task.id, expected_revision=task.revision)
    claimed = await repository.claim_ready_unit(
        task.id,
        worker_id="worker-structure-expansion",
        lease_duration_ms=30_000,
    )
    assert claimed is not None and claimed.id == parent_id

    executor = object.__new__(ScreenplayTaskUnitExecutor)
    error = StructurePartSplit(
        "structure_episode_plan",
        (
            {
                "number": 1,
                "id": "ep01",
                "title": "误入犬域",
                "summary": "进入异空间。",
            },
            {
                "number": 2,
                "id": "ep02",
                "title": "绝境觉醒",
                "summary": "首次能力爆发。",
            },
        ),
        index_run_id="run-episode-index",
    )
    context = SimpleNamespace(task=task, unit=claimed)
    failure = executor.classify_failure(error)
    decision = decide_failure(
        failure,
        attempts_remaining=claimed.max_attempts - claimed.attempt,
    )
    split = executor.split_unit(context, error)

    expanded = await repository.expand_unit(
        task.id,
        parent_id,
        worker_id="worker-structure-expansion",
        lease_epoch=claimed.lease_epoch,
        split=split,
        decision=decision,
    )
    units = await repository.list_units(task.id)
    by_id = {unit.id: unit for unit in units}

    assert expanded.total_units == 3
    assert by_id[parent_id].status.value == "expanded"
    assert by_id[parent_id].required is False
    assert by_id["document:validation"].dependencies == (
        "section:structure:episode_plan:episode-1",
        "section:structure:episode_plan:episode-2",
    )
    assert by_id["section:structure:episode_plan:episode-1"].status.value == (
        "pending"
    )
    assert by_id["section:structure:episode_plan:episode-2"].status.value == (
        "pending"
    )


@pytest.mark.parametrize(("code", "retryable", "expected_category"), (
    ("tool_execution_failed", True, FailureCategory.TOOL_EXECUTION),
    ("model_output_truncated", True, FailureCategory.MODEL_OUTPUT_INVALID),
    ("invalid_tool_results", True, FailureCategory.MODEL_OUTPUT_INVALID),
    ("max_model_rounds", True, FailureCategory.MODEL_OUTPUT_INVALID),
    ("invalid_tool_arguments_json", True, FailureCategory.MODEL_OUTPUT_INVALID),
    (
        "invalid_tool_arguments_schema",
        True,
        FailureCategory.MODEL_OUTPUT_INVALID,
    ),
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
    assert decision.disposition is FailureDisposition.FAIL_PERMANENT


async def test_scene_output_truncation_fails_the_attempt_instead_of_pausing():
    error = ModelGatewayError(
        "model output is incomplete",
        code="model_output_truncated",
        retryable=False,
    )
    failure = classify_screenplay_run_failure(error)

    assert failure.retryable is False
    assert decide_failure(
        failure,
        attempts_remaining=3,
    ).disposition is FailureDisposition.FAIL_PERMANENT


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
        "runRole": "related",
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


async def test_screenplay_stream_enriches_tool_operation_display_names():
    chunk = _with_screenplay_tool_display_names({
        "kind": "operation.started",
        "payload": {
            "kind": "tool",
            "display": {
                "labelParams": {
                    "toolName": "inspectScreenplayProject",
                },
            },
        },
    })

    assert chunk["payload"]["display"]["labelParams"]["displayNames"] == {
        "zh-CN": "查看剧本项目",
    }

    episode_chunk = _with_screenplay_tool_display_names({
        "kind": "operation.started",
        "payload": {
            "kind": "tool",
            "display": {
                "labelParams": {
                    "toolName": "writeScreenplayCandidatePart",
                    "episodeNumber": 7,
                },
            },
        },
    })
    assert episode_chunk["payload"]["display"]["labelParams"][
        "displayNames"
    ] == {"zh-CN": "写入第 7 集剧本候选稿"}


async def test_screenplay_stream_replaces_stale_operation_semantics_on_replay():
    chunk = _with_screenplay_tool_display_names(
        {
            "kind": "operation.started",
            "payload": {
                "kind": "tool",
                "display": {
                    "labelParams": {
                        "toolCallId": "dependency",
                        "toolName": "readScreenplayTaskDependencies",
                        "episodeNumber": 1,
                        "targetDetail": "可读产出清单",
                        "displayNames": {
                            "zh-CN": "读取第 1 集任务依赖（可读产出清单）",
                        },
                    },
                },
            },
        },
        projected_params={
            "episodeNumber": 1,
            "readTargets": ["第 1 集第 2 场已完成剧本"],
            "displayNames": {"zh-CN": "读取第 1 集第 2 场已完成剧本"},
        },
    )

    params = chunk["payload"]["display"]["labelParams"]
    assert params["displayNames"] == {
        "zh-CN": "读取第 1 集第 2 场已完成剧本",
    }
    assert params["readTargets"] == ["第 1 集第 2 场已完成剧本"]
    assert "targetDetail" not in params


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


async def test_restart_reconciles_terminal_operation_with_paused_turn(
    temp_db: DatabaseConnection,
):
    _, workspace, session = await _project_and_session(temp_db)
    repository = SqliteScreenplayAgentRepository(
        temp_db,
        owner_id="screenplay-terminal-projection-recovery",
    )
    turn = await repository.begin_turn(
        command_id="terminal-operation-before-restart",
        project_id=workspace["project"]["id"],
        session_id=session["id"],
        content="继续生成剧本",
        stage_command=None,
        runtime_profile={"provider": "openai", "model": "test"},
    )
    await temp_db.execute(
        "UPDATE screenplay_agent_turns SET status = 'paused', "
        "error_json = '{\"code\":\"screenplay_task_paused\"}' WHERE id = ?",
        [turn["id"]],
    )
    await temp_db.execute(
        "INSERT INTO screenplay_agent_operations "
        "(id, turn_id, project_id, session_id, status, target_role, "
        "requirements_json, manifest_digest, error_json) "
        "VALUES ('operation-terminal-before-restart', ?, ?, ?, 'failed', "
        "'screenplayDraft', '{}', 'sha256:terminal-recovery', "
        "'{\"code\":\"execution_projection_failed\","
        "\"message\":\"终态投影失败。\"}')",
        [turn["id"], workspace["project"]["id"], session["id"]],
    )

    recovered = await repository.recover_after_restart()

    assert recovered == (turn["id"],)
    assert await temp_db.fetch_one(
        "SELECT status, error_json FROM screenplay_agent_turns WHERE id = ?",
        [turn["id"]],
    ) == {
        "status": "failed",
        "error_json": (
            '{"code":"execution_projection_failed",'
            '"message":"终态投影失败。"}'
        ),
    }


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


class _CheckpointingToolCalls:
    def __init__(self, *, fail_once_key: str | None = None) -> None:
        self.fail_once_key = fail_once_key
        self.failed = False
        self.calls: list[tuple[str, str, ReasoningMode]] = []
        self.tool_profiles: list[str] = []
        self.user_payloads: list[dict] = []
        self.system_instructions: list[str] = []
        self.dependency_part_keys: list[tuple[str, ...]] = []
        self.contexts: list[ScreenplayAgentDomainContext] = []

    async def run_candidate(self, **kwargs):
        context = kwargs["domain_context"]
        self.contexts.append(context)
        part_type = context.expected_part_type
        part_key = context.expected_part_key
        self.calls.append((
            part_type,
            part_key,
            kwargs.get("reasoning_mode", ReasoningMode.DEFAULT),
        ))
        self.tool_profiles.append(str(context.tool_access))
        self.user_payloads.append(dict(kwargs["user_payload"]))
        self.system_instructions.append(str(kwargs["system_instruction"]))
        self.dependency_part_keys.append(tuple(context.dependency_part_keys))
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
        elif part_type == "document_section" and part_key == "series_arc:index":
            candidate = {
                "artifactId": "artifact-series-index",
                "payload": {
                    "sectionKey": part_key,
                    "title": "全剧阶段索引",
                    "contentJson": {"phases": [
                        {
                            "key": "setup",
                            "title": "进入困局",
                            "objective": "建立目标。",
                        },
                        {
                            "key": "resolution",
                            "title": "完成抉择",
                            "objective": "兑现选择。",
                        },
                    ]},
                },
                "contentText": "- 进入困局\n- 完成抉择",
            }
        elif (
            part_type == "document_section"
            and part_key.startswith("series_arc:phase:")
        ):
            identity = kwargs["user_payload"]["partIdentity"]
            candidate = {
                "artifactId": f"artifact-series-{part_key}",
                "payload": {
                    "sectionKey": part_key,
                    "title": identity["phaseTitle"],
                    "contentJson": {"seriesArc": {"phases": [{
                        "key": identity["phaseKey"],
                        "title": identity["phaseTitle"],
                        "objective": identity["phaseObjective"],
                        "centralConflict": "回家与救人不可兼得。",
                        "turningPoint": "主角决定留下。",
                        "exitState": "团队完成结盟。",
                    }]}},
                },
                "contentText": f"## {identity['phaseTitle']}",
            }
        elif part_type == "document_section" and part_key == "episode_plan:index":
            candidate = {
                "artifactId": "artifact-episode-index",
                "payload": {
                    "sectionKey": part_key,
                    "title": "分集索引",
                    "contentJson": {"episodes": [{
                        "number": number,
                        "id": f"ep{number:02d}",
                        "title": f"第 {number} 集",
                        "summary": f"第 {number} 集叙事边界。",
                    } for number in range(1, 11)]},
                },
                "contentText": "十集轻量索引",
            }
        elif (
            part_type == "document_section"
            and part_key.startswith("episode_plan:episode-")
        ):
            episode_number = int(part_key.removeprefix("episode_plan:episode-"))
            identity = kwargs["user_payload"]["partIdentity"]
            candidate = {
                "artifactId": f"artifact-structure-{part_key}",
                "payload": {
                    "sectionKey": part_key,
                    "title": f"第 {episode_number} 集分集结构",
                    "executionSummary": "依据分集索引展开本集。",
                    "contentJson": {"episodes": [{
                        "number": episode_number,
                        "id": identity["episodeId"],
                        "title": identity["episodeTitle"],
                        "summary": "进入异空间。",
                        "objective": "确认规则",
                        "conflict": "无法返回",
                        "turn": "能力觉醒",
                        "hook": "追兵出现",
                    }]},
                },
                "contentText": f"第 {episode_number} 集分集结构正文",
            }
        elif (
            part_type == "document_section"
            and part_key == "character_arcs:index"
        ):
            candidate = {
                "artifactId": "artifact-character-index",
                "payload": {
                    "sectionKey": part_key,
                    "title": "核心人物索引",
                    "contentJson": {"characters": [
                        {"key": "linyue", "name": "林月"},
                        {"key": "suwen", "name": "苏文"},
                    ]},
                },
                "contentText": "- 林月\n- 苏文",
            }
        elif (
            part_type == "document_section"
            and part_key.startswith("character_arcs:character:")
        ):
            identity = kwargs["user_payload"]["partIdentity"]
            candidate = {
                "artifactId": f"artifact-character-{part_key}",
                "payload": {
                    "sectionKey": part_key,
                    "title": f"{identity['characterName']}人物弧",
                    "contentJson": {"characterArcs": [{
                        "key": identity["characterKey"],
                        "startState": "拒绝承担责任。",
                        "desire": "找到安全的归途。",
                        "turningEpisodes": ["ep01", "ep10"],
                        "endState": "主动承担责任。",
                    }]},
                },
                "contentText": f"## {identity['characterName']}",
            }
        elif part_type == "document_section" and part_key.startswith("episode-"):
            episode_number = int(part_key.removeprefix("episode-"))
            candidate = {
                "artifactId": f"artifact-scene-list-{part_key}",
                "payload": {
                    "sectionKey": part_key,
                    "title": f"第 {part_key} 集场景表",
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
        validation_contract = kwargs.get("candidate_validation_contract")
        if validation_contract is not None:
            candidate = normalize_screenplay_candidate(
                validation_contract,
                candidate,
            )
        return ScreenplayCandidateRunResult(
            run_id=f"run-{part_type}-{part_key}-{len(self.calls)}",
            candidate=candidate,
        )


class _SourceAnalysisToolCalls:
    def __init__(self) -> None:
        self.calls = []

    async def run_candidate(self, **kwargs):
        context = kwargs["domain_context"]
        contract = kwargs["candidate_validation_contract"]
        kind = str(contract["kind"])
        part_key = str(context.expected_part_key)
        if kind == "source_chapter_digest":
            digest_id = str(contract["chapterId"])
            content = {
                "chapterId": digest_id,
                "summary": f"{digest_id} 事件边界",
                "characters": [],
                "events": [],
                "worldFacts": [],
                "themes": [],
                "plotThreads": [],
                "adaptationRisks": [],
            }
        elif kind == "source_digest_reduction":
            digest_id = str(contract["digestId"])
            content = {
                "chapterId": digest_id,
                "summary": "归并后的事件边界",
                "characters": [],
                "events": [],
                "worldFacts": [],
                "themes": [],
                "plotThreads": [],
                "adaptationRisks": [],
            }
        elif kind == "source_analysis_section":
            content = {"characters": []}
        else:
            raise AssertionError(kind)
        candidate = normalize_screenplay_candidate(
            contract,
            {
                "artifactId": f"artifact-{len(self.calls) + 1}",
                "payload": {
                    "sectionKey": part_key,
                    "title": part_key,
                    "contentJson": content,
                },
                "contentText": f"{part_key} 摘要",
            },
        )
        self.calls.append({
            "context": context,
            "payload": dict(kwargs["user_payload"]),
            "instruction": str(kwargs["system_instruction"]),
        })
        return ScreenplayCandidateRunResult(
            run_id=f"run-source-{len(self.calls)}",
            candidate=candidate,
        )


async def test_source_analysis_parts_read_only_bound_identities_and_propagate_runs(
    temp_db: DatabaseConnection,
):
    _, workspace, session = await _project_and_session(temp_db)
    await temp_db.execute(
        "INSERT INTO books (id, title) VALUES ('source-book-phase4', '原作')"
    )
    await temp_db.execute(
        "UPDATE screenplay_projects SET source_kind = 'book', "
        "source_book_id = 'source-book-phase4', source_scope_json = ? "
        "WHERE id = ?",
        [json.dumps({"mode": "whole_book"}), workspace["project"]["id"]],
    )
    tool_calls = _SourceAnalysisToolCalls()
    executor = ScreenplayTaskModelCalls(
        temp_db,
        tool_calling_service=tool_calls,  # type: ignore[arg-type]
    )
    task = {
        "id": "task-source-analysis-phase4",
        "projectId": workspace["project"]["id"],
        "sessionId": session["id"],
        "turnId": "turn-source-analysis-phase4",
        "rootRunId": "root-source-analysis-phase4",
        "targetRole": "sourceAnalysis",
        "units": [{
            "id": "document:evidence",
            "kind": "collect_evidence",
            "status": "completed",
            "input": {"targetRole": "sourceAnalysis"},
            "output": {"evidenceDescriptor": {
                "projectId": workspace["project"]["id"],
                "targetRole": "sourceAnalysis",
            }},
        }],
    }
    for number in (1, 2):
        unit = {
            "id": f"source-analysis:chapter:chapter-{number}",
            "kind": "generate_document_section",
            "status": "pending",
            "dependsOn": ["document:evidence"],
            "input": {
                "targetRole": "sourceAnalysis",
                "sectionKey": f"source_digest:chapter:chapter-{number}",
                "sourceChapterDigest": True,
                "chapterId": f"chapter-{number}",
                "chapterTitle": f"第 {number} 章",
                "chapterIndex": number,
            },
        }
        task["units"].append(unit)
        output = await executor.execute(
            task=task,
            unit=unit,
            runtime=object(),
        )
        output["contentText"] = f"不得注入下游的第 {number} 章摘要正文"
        unit.update({"status": "completed", "output": output})
    reduction = {
        "id": "source-analysis:reduce:1:1",
        "kind": "generate_document_section",
        "status": "pending",
        "dependsOn": [
            "source-analysis:chapter:chapter-1",
            "source-analysis:chapter:chapter-2",
        ],
        "input": {
            "targetRole": "sourceAnalysis",
            "sectionKey": "source_digest:reduce:1:1",
            "sourceDigestReduction": True,
            "digestId": "source-analysis:reduce:1:1",
            "reductionLevel": 1,
            "reductionIndex": 1,
        },
    }
    task["units"].append(reduction)
    reduction_output = await executor.execute(
        task=task,
        unit=reduction,
        runtime=object(),
    )
    reduction_output["contentText"] = "不得注入最终栏目的归并正文"
    reduction.update({"status": "completed", "output": reduction_output})
    section = {
        "id": "section:sourceAnalysis:characters",
        "kind": "generate_document_section",
        "status": "pending",
        "dependsOn": ["source-analysis:reduce:1:1"],
        "input": {
            "targetRole": "sourceAnalysis",
            "sectionKey": "characters",
        },
    }
    task["units"].append(section)
    section_output = await executor.execute(
        task=task,
        unit=section,
        runtime=object(),
    )

    assert [call["context"].tool_access for call in tool_calls.calls] == [
        "source_chapter_digest",
        "source_chapter_digest",
        "source_digest_reduction",
        "source_analysis_section",
    ]
    assert tool_calls.calls[0]["context"].source_scope["mode"] == "whole_book"
    assert tool_calls.calls[1]["context"].source_scope["mode"] == "whole_book"
    assert tool_calls.calls[2]["payload"]["dependencyPartKeys"] == [
        "source-analysis:chapter:chapter-1",
        "source-analysis:chapter:chapter-2",
    ]
    assert tool_calls.calls[3]["payload"]["dependencyPartKeys"] == [
        "source-analysis:reduce:1:1"
    ]
    assert all(
        "不得注入" not in str(call["payload"])
        and "evidenceDescriptor" not in call["payload"]
        for call in tool_calls.calls
    )
    assert section_output["sourceRunIds"] == [
        "run-source-1",
        "run-source-2",
        "run-source-3",
        "run-source-4",
    ]


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
        "projectId": workspace["project"]["id"],
        "targetRole": "screenplayDraft",
        "acceptedRevisionIds": {
            "sourceAnalysis": "analysis-head",
            "creativeBrief": "brief-head",
            "structure": "structure-head",
            "sceneList": "scene-list-head",
        },
        "episodeNumber": 1,
        "sceneIds": ["scene-1", "scene-2"],
        "sceneListRevisionId": "scene-list-head",
    }
    task: dict[str, object] = {
        "id": "task-formal-evidence-checkpoint",
        "projectId": workspace["project"]["id"],
        "sessionId": session["id"],
        "turnId": "turn-formal-evidence-checkpoint",
        "rootRunId": "root-formal-evidence-checkpoint",
        "targetRole": "screenplayDraft",
        "units": [
            {
                "id": "evidence:1",
                "kind": "collect_evidence",
                "status": "completed",
                "input": {"episodeNumber": 1},
                "output": {
                    "evidenceDescriptor": evidence,
                    "evidenceReceipt": "receipt-1",
                },
            },
            {
                "id": "draft:1:scene-1",
                "kind": "generate_draft_scene",
                "status": "pending",
                "dependsOn": ["evidence:1", "evidence:2"],
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
            {
                "id": "evidence:2",
                "kind": "collect_evidence",
                "status": "completed",
                "input": {"episodeNumber": 2},
                "output": {
                    "evidenceDescriptor": {"episodeNumber": 2},
                    "evidenceReceipt": "receipt-2",
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
    assert tool_calls.tool_profiles == [
        "draft_scene",
        "draft_scene",
        "episode_metadata",
    ]
    assert tool_calls.dependency_part_keys == [
        (),
        ("draft:1:scene-1",),
        ("draft:1:scene-2",),
    ]
    assert all(
        context.deliverable_revision_scope == {
            "sourceAnalysis": "analysis-head",
            "creativeBrief": "brief-head",
            "structure": "structure-head",
            "sceneList": "scene-list-head",
        }
        for context in tool_calls.contexts[:2]
    )
    assert all(context.episode_number == 1 for context in tool_calls.contexts)
    assert [payload["dependencyPartKeys"] for payload in tool_calls.user_payloads] == [
        [],
        ["draft:1:scene-1"],
        ["draft:1:scene-2"],
    ]
    for payload in tool_calls.user_payloads:
        assert "previousEpisodeContinuity" not in payload
        assert "previousSceneTail" not in payload
        assert "finalSceneTail" not in payload
    assert validated["sourceRunIds"] == (
        "run-scene-scene-1-1",
        "run-scene-scene-2-2",
        "run-episode_metadata-1-3",
    )


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
        "projectId": workspace["project"]["id"],
        "targetRole": "screenplayDraft",
        "acceptedRevisionIds": {
            "sceneList": "scene-list-head",
            "screenplayDraft": "draft-head",
        },
        "episodeNumber": 1,
        "sceneIds": ["scene-1", "scene-2"],
        "sceneListRevisionId": "scene-list-head",
    }
    task = {
        "id": "task-visible-scene-parts",
        "projectId": workspace["project"]["id"],
        "sessionId": session["id"],
        "turnId": "turn-visible-scene-parts",
        "rootRunId": "root-visible-scene-parts",
        "targetRole": "screenplayDraft",
        "units": [{
            "id": "evidence:1",
            "kind": "collect_evidence",
            "status": "completed",
            "input": {"episodeNumber": 1},
            "output": {"evidenceDescriptor": evidence},
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
    task = {
        "id": "task-review-dimensions",
        "projectId": workspace["project"]["id"],
        "sessionId": session["id"],
        "turnId": "turn-review-dimensions",
        "rootRunId": "root-review-dimensions",
        "targetRole": "review",
        "units": [{
            "id": "review-input:1",
            "kind": "collect_evidence",
            "status": "completed",
            "input": {"episodeNumber": 1, "evidenceKind": "review_input"},
            "output": {"evidenceDescriptor": {
                "projectId": workspace["project"]["id"],
                "targetRole": "review",
                "acceptedRevisionIds": {
                    "sceneList": "scene-list-head",
                    "screenplayDraft": "draft-head",
                },
                "episodeNumber": 1,
                "sceneIds": ["scene-1"],
                "sceneListRevisionId": "scene-list-head",
                "reviewedDraftId": "draft-head",
                "reviewInputRef": {
                    "reviewedRevisionId": "draft-head",
                    "episodeNumber": 1,
                    "scenePartRefs": ["draft-head#scene:scene-1"],
                    "scenePlanRevisionId": "scene-list-head",
                    "contentDigest": "a" * 64,
                },
            }},
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
        output["artifactDigest"] = f"sha256:{dimension}"
        unit.update({"status": "completed", "output": output})
    validation = {
        "id": "review:1:validation",
        "kind": "validate_manifest_part",
        "status": "pending",
        "dependsOn": [f"review:1:{value}" for value in REVIEW_DIMENSIONS],
        "input": {
            "validationKind": "review_episode",
            "episodeNumber": 1,
            "sceneIds": ["scene-1"],
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
    descriptor = tool_calls.user_payloads[0]["evidenceDescriptor"]
    assert descriptor["reviewInputRef"]["reviewedRevisionId"] == "draft-head"
    assert descriptor["reviewInputRef"]["contentDigest"] == "a" * 64
    assert "draftContentText" not in str(descriptor)
    assert len(tool_calls.user_payloads) == len(REVIEW_DIMENSIONS)
    assert {
        payload["evidenceDescriptor"]["reviewInputRef"]["contentDigest"]
        for payload in tool_calls.user_payloads
    } == {"a" * 64}
    assert all(
        payload["reviewedDraftId"] == "draft-head"
        and payload["evidenceDescriptor"]["reviewInputRef"]
        ["reviewedRevisionId"] == "draft-head"
        and "previousReview" not in payload
        and "acceptedReview" not in payload
        and "reviewReport" not in payload
        for payload in tool_calls.user_payloads
    )
    assert "先调用 getScreenplayEpisodeContext 读取当前集材料" in tool_calls.system_instructions[0]
    assert "最多提交 1 个问题" in tool_calls.system_instructions[0]
    assert all(
        context.episode_number == 1
        and context.deliverable_revision_scope == {
            "sceneList": "scene-list-head",
            "screenplayDraft": "draft-head",
        }
        for context in tool_calls.contexts
    )


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
        "rootRunId": "root-failed-review-dimension",
        "targetRole": "review",
        "units": [{
            "id": "review-input:1",
            "kind": "collect_evidence",
            "status": "completed",
            "input": {"episodeNumber": 1},
            "output": {"evidenceDescriptor": {
                "projectId": workspace["project"]["id"],
                "targetRole": "review",
                "acceptedRevisionIds": {
                    "sceneList": "scene-list-head",
                    "screenplayDraft": "draft-head",
                },
                "episodeNumber": 1,
                "sceneIds": ["scene-1"],
                "sceneListRevisionId": "scene-list-head",
                "reviewedDraftId": "draft-head",
                "reviewInputRef": {
                    "reviewedRevisionId": "draft-head",
                    "episodeNumber": 1,
                    "scenePartRefs": ["draft-head#scene:scene-1"],
                    "scenePlanRevisionId": "scene-list-head",
                    "contentDigest": "digest-1",
                },
            }},
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
        "rootRunId": "root-scene-list-sections",
        "targetRole": "sceneList",
        "units": [{
            "id": "document:evidence",
            "kind": "collect_evidence",
            "status": "completed",
            "input": {"targetRole": "sceneList"},
            "output": {"evidenceDescriptor": {
                "projectId": workspace["project"]["id"],
                "targetRole": "sceneList",
                "acceptedRevisionIds": {"structure": "structure-head"},
                "structureRevisionId": "structure-head",
                "structureEpisodeNumbers": [1, 2],
            }},
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
                "episodeNumber": number,
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
    assert [context.episode_number for context in tool_calls.contexts] == [1, 2]
    assert all(
        context.deliverable_revision_scope == {"structure": "structure-head"}
        for context in tool_calls.contexts
    )


async def test_scene_list_assembly_rejects_cross_episode_duplicate_scene_ids(
    temp_db: DatabaseConnection,
):
    executor = ScreenplayTaskModelCalls(
        temp_db,
        tool_calling_service=_CheckpointingToolCalls(),  # type: ignore[arg-type]
    )
    task = {
        "targetRole": "sceneList",
        "units": [{
            "id": "document:evidence",
            "kind": "collect_evidence",
            "status": "completed",
            "input": {"targetRole": "sceneList"},
            "output": {"evidenceDescriptor": {
                "structureRevisionId": "structure-head",
                "structureEpisodeNumbers": [1, 2],
            }},
        }, *[{
            "id": f"section:sceneList:episode-{number}",
            "kind": "generate_document_section",
            "status": "completed",
            "input": {
                "sectionKey": f"episode-{number}",
                "episodeNumber": number,
            },
            "output": {
                **_scene_list_candidate(number, scene_id="duplicate-scene")[
                    "payload"
                ],
                "contentText": f"第 {number} 集场景表",
                "runId": f"run-scene-list-{number}",
            },
        } for number in (1, 2)]],
    }
    validation = {
        "id": "document:validation",
        "kind": "validate_manifest_part",
        "dependsOn": [
            "section:sceneList:episode-1",
            "section:sceneList:episode-2",
        ],
        "input": {"validationKind": "document", "targetRole": "sceneList"},
    }
    task["units"].append(validation)

    with pytest.raises(ValueError, match="scene ids are invalid"):
        await executor.execute(task=task, unit=validation, runtime=object())


async def test_structure_episode_fragment_reads_part_keys_without_injected_bodies(
    temp_db: DatabaseConnection,
):
    _, workspace, session = await _project_and_session(temp_db)
    tool_calls = _CheckpointingToolCalls()
    executor = ScreenplayTaskModelCalls(
        temp_db,
        tool_calling_service=tool_calls,  # type: ignore[arg-type]
    )
    task = {
        "id": "task-structure-fragment",
        "projectId": workspace["project"]["id"],
        "sessionId": session["id"],
        "turnId": "turn-structure-fragment",
        "rootRunId": "root-structure-fragment",
        "targetRole": "structure",
        "units": [
            {
                "id": "document:evidence",
                "kind": "collect_evidence",
                "status": "completed",
                "input": {"targetRole": "structure"},
                "output": {"evidenceDescriptor": {
                    "projectId": workspace["project"]["id"],
                    "targetRole": "structure",
                }},
            },
            {
                "id": "section:structure:series_arc",
                "kind": "generate_document_section",
                "status": "completed",
                "output": {
                    "contentJson": {"seriesArc": {"theme": "觉醒"}},
                    "contentText": "不应直塞的主线正文",
                    "runId": "run-series",
                },
            },
            {
                "id": "section:structure:episode_plan:index",
                "kind": "generate_document_section",
                "status": "completed",
                "output": {
                    "contentJson": {"episodes": [{
                        "number": 1,
                        "id": "ep01",
                        "title": "误入犬域",
                    }]},
                    "contentText": "不应直塞的索引正文",
                    "runId": "run-index",
                },
            },
            {
                "id": "section:structure:episode_plan:episode-1",
                "kind": "generate_document_section",
                "status": "pending",
                "dependsOn": [
                    "document:evidence",
                    "section:structure:series_arc",
                    "section:structure:episode_plan:index",
                ],
                "input": {
                    "sectionKey": "episode_plan:episode-1",
                    "documentSectionKey": "episode_plan",
                    "episodeNumber": 1,
                    "episodeId": "ep01",
                    "episodeTitle": "误入犬域",
                },
            },
        ],
    }

    output = await executor.execute(
        task=task,
        unit=task["units"][-1],
        runtime=object(),
    )

    payload = tool_calls.user_payloads[0]
    assert payload["dependencyPartKeys"] == [
        "section:structure:series_arc",
        "section:structure:episode_plan:index",
    ]
    assert payload["partIdentity"] == {
        "episodeNumber": 1,
        "episodeId": "ep01",
        "episodeTitle": "误入犬域",
    }
    assert "episodePlanEntry" not in payload
    assert "dependencySections" not in payload
    assert "不应直塞" not in str(payload)
    assert output["sourceRunIds"] == [
        "run-series",
        "run-index",
        "run-document_section-episode_plan:episode-1-1",
    ]


async def test_structure_episode_fragments_merge_back_into_one_document_section():
    task = {
        "targetRole": "structure",
        "units": [
            {
                "id": "document:evidence",
                "kind": "collect_evidence",
                "status": "completed",
                "input": {"targetRole": "structure"},
                "output": {"evidenceDescriptor": {
                    "projectId": "project-structure",
                    "targetRole": "structure",
                    "structureEpisodeNumbers": [],
                }},
            },
            {
                "id": "section:structure:series_arc:phase:setup",
                "kind": "generate_document_section",
                "status": "completed",
                "input": {
                    "sectionKey": "series_arc:phase:setup",
                    "documentSectionKey": "series_arc",
                    "phasePosition": 1,
                },
                "output": {
                    "sectionKey": "series_arc:phase:setup",
                    "title": "全剧主线",
                    "contentText": "SERIES-ARC",
                    "contentJson": {"seriesArc": {"phases": [{
                        "key": "setup",
                        "title": "误入犬域",
                        "objective": "建立目标",
                        "centralConflict": "回家与救人冲突",
                        "turningPoint": "主角决定留下",
                        "exitState": "团队结盟",
                    }]}},
                    "runId": "run-series",
                },
            },
            {
                "id": "section:structure:episode_plan:index",
                "kind": "generate_document_section",
                "status": "completed",
                "input": {
                    "sectionKey": "episode_plan:index",
                    "documentSectionKey": "episode_plan",
                    "episodePlanIndex": True,
                },
                "output": {
                    "sectionKey": "episode_plan:index",
                    "title": "分集索引",
                    "contentText": "INDEX-MUST-NOT-BE-PUBLISHED",
                    "contentJson": {"episodes": []},
                    "runId": "run-index",
                },
            },
            {
                "id": "section:structure:episode_plan",
                "kind": "expand_structure_episode_plan",
                "status": "expanded",
                "required": False,
                "input": {
                    "sectionKey": "episode_plan",
                    "splitStrategy": "structure_episode_plan",
                },
            },
            {
                "id": "section:structure:character_arcs:character:linyue",
                "kind": "generate_document_section",
                "status": "completed",
                "input": {
                    "sectionKey": "character_arcs:character:linyue",
                    "documentSectionKey": "character_arcs",
                    "characterPosition": 1,
                },
                "output": {
                    "sectionKey": "character_arcs:character:linyue",
                    "title": "人物弧",
                    "contentText": "CHARACTER-ARCS",
                    "contentJson": {"characterArcs": [{
                        "key": "linyue",
                        "startState": "只想回家",
                        "desire": "找到归途",
                        "turningEpisodes": ["ep01", "ep02"],
                        "endState": "选择守护同伴",
                    }]},
                    "runId": "run-arcs",
                },
            },
            {
                "id": "section:structure:hooks",
                "kind": "project_structure_hooks",
                "status": "completed",
                "input": {"sectionKey": "hooks"},
                "output": {
                    "sectionKey": "hooks",
                    "title": "剧情钩子",
                    "contentText": "HOOKS",
                    "contentJson": {"hooks": [
                        {"episodeId": "ep01", "hook": "异响再次出现。"},
                        {"episodeId": "ep02", "hook": "苏文突然惊醒。"},
                    ]},
                    "runId": "run-hooks",
                },
            },
            {
                "id": "section:structure:episode_plan:episode-1",
                "kind": "generate_document_section",
                "status": "completed",
                "input": {
                    "sectionKey": "episode_plan:episode-1",
                    "documentSectionKey": "episode_plan",
                    "episodeNumber": 1,
                },
                "output": {
                    "sectionKey": "episode_plan:episode-1",
                    "title": "第 1 集",
                    "contentText": "EPISODE-1",
                    "contentJson": {"episodes": [{
                        "number": 1,
                        "id": "ep01",
                            "title": "误入犬域",
                            "summary": "林月进入犬域。",
                            "objective": "找到归途。",
                            "conflict": "必须先救同伴。",
                            "turn": "选择留下。",
                            "hook": "异响再次出现。",
                    }]},
                    "sourceRunIds": ["run-index", "run-episode-1"],
                },
            },
            {
                "id": "section:structure:episode_plan:episode-2",
                "kind": "generate_document_section",
                "status": "completed",
                "input": {
                    "sectionKey": "episode_plan:episode-2",
                    "documentSectionKey": "episode_plan",
                    "episodeNumber": 2,
                },
                "output": {
                    "sectionKey": "episode_plan:episode-2",
                    "title": "第 2 集",
                    "contentText": "EPISODE-2",
                    "contentJson": {"episodes": [{
                        "number": 2,
                        "id": "ep02",
                            "title": "绝境觉醒",
                            "summary": "林月唤醒金鼓。",
                            "objective": "突破包围。",
                            "conflict": "能力可能失控。",
                            "turn": "接受金鼓。",
                            "hook": "苏文突然惊醒。",
                    }]},
                    "sourceRunIds": ["run-index", "run-episode-2"],
                },
            },
        ],
    }
    validation = {
        "id": "document:validation",
        "kind": "validate_manifest_part",
        "status": "running",
        "input": {"validationKind": "document", "targetRole": "structure"},
    }
    task["units"].append(validation)

    result = _validate_document_parts(task, validation)

    assert [episode["number"] for episode in result["contentJson"]["episodes"]] == [
        1,
        2,
    ]
    assert [section["key"] for section in result["sections"]] == [
        "series_arc",
        "episode_plan",
        "character_arcs",
        "hooks",
    ]
    assert result["contentText"].split("\n\n") == [
        "SERIES-ARC",
        "EPISODE-1",
        "EPISODE-2",
        "CHARACTER-ARCS",
        "HOOKS",
    ]
    assert "INDEX-MUST-NOT-BE-PUBLISHED" not in result["contentText"]
    assert result["sourceRunIds"] == (
        "run-series",
        "run-index",
        "run-episode-1",
        "run-episode-2",
        "run-arcs",
        "run-hooks",
    )


async def test_scripted_ten_episode_structure_stays_bounded_per_ai_part():
    protocol = "purrtypos.screenplay.candidate-validation/v1"
    units: list[dict] = [{
        "id": "document:evidence",
        "kind": "collect_evidence",
        "status": "completed",
        "input": {"targetRole": "structure"},
        "output": {"evidenceDescriptor": {
            "projectId": "project-ten-episodes",
            "targetRole": "structure",
            "structureEpisodeNumbers": [],
        }},
    }]
    for position, phase in enumerate((
        {
            "key": "setup",
            "title": "进入困局",
            "objective": "建立目标。",
        },
        {
            "key": "resolution",
            "title": "完成抉择",
            "objective": "兑现人物选择。",
        },
    ), 1):
        candidate = normalize_screenplay_candidate(
            {
                "protocol": protocol,
                "kind": "structure_series_arc_phase",
                "phaseKey": phase["key"],
                "phaseTitle": phase["title"],
                "phaseObjective": phase["objective"],
            },
            {
                "payload": {
                    "sectionKey": f"series_arc:phase:{phase['key']}",
                    "title": phase["title"],
                    "contentJson": {"seriesArc": {"phases": [{
                        **phase,
                        "centralConflict": f"阶段 {position} 核心冲突",
                        "turningPoint": f"阶段 {position} 关键转折",
                        "exitState": f"阶段 {position} 结束状态",
                    }]}},
                },
                "contentText": f"## {phase['title']}",
            },
        )
        units.append({
            "id": f"section:structure:series_arc:phase:{phase['key']}",
            "kind": "generate_document_section",
            "status": "completed",
            "input": {
                "documentSectionKey": "series_arc",
                "phasePosition": position,
            },
            "output": {
                **candidate["payload"],
                "contentText": candidate["contentText"],
                "runId": f"run-phase-{position}",
            },
        })

    episode_outputs = []
    for number in range(1, 11):
        episode_id = f"ep{number:02d}"
        candidate = normalize_screenplay_candidate(
            {
                "protocol": protocol,
                "kind": "structure_episode_plan_fragment",
                "episodeNumber": number,
                "episodeId": episode_id,
                "episodeTitle": f"第 {number} 集",
            },
            {
                "payload": {
                    "sectionKey": f"episode_plan:episode-{number}",
                    "title": f"第 {number} 集",
                    "contentJson": {"episodes": [{
                        "number": number,
                        "id": episode_id,
                        "title": f"第 {number} 集",
                        "summary": f"第 {number} 集只描述自己的叙事边界。",
                        "objective": f"完成目标 {number}",
                        "conflict": f"处理冲突 {number}",
                        "turn": f"发生转折 {number}",
                        "hook": f"留下钩子 {number}",
                    }]},
                },
                "contentText": f"## 第 {number} 集",
            },
        )
        output = {
            **candidate["payload"],
            "contentText": candidate["contentText"],
            "runId": f"run-episode-{number}",
        }
        episode_outputs.append(output)
        units.append({
            "id": f"section:structure:episode_plan:episode-{number}",
            "kind": "generate_document_section",
            "status": "completed",
            "input": {
                "documentSectionKey": "episode_plan",
                "episodeNumber": number,
            },
            "output": output,
        })

    for position, (key, name) in enumerate((
        ("linyue", "林月"),
        ("suwen", "苏文"),
    ), 1):
        candidate = normalize_screenplay_candidate(
            {
                "protocol": protocol,
                "kind": "structure_character_arc_fragment",
                "characterKey": key,
                "characterName": name,
            },
            {
                "payload": {
                    "sectionKey": f"character_arcs:character:{key}",
                    "title": f"{name}人物弧",
                    "contentJson": {"characterArcs": [{
                        "key": key,
                        "startState": "拒绝承担责任。",
                        "desire": "找到安全的归途。",
                        "turningEpisodes": ["ep01", "ep10"],
                        "endState": "主动承担责任。",
                    }]},
                },
                "contentText": f"## {name}",
            },
        )
        units.append({
            "id": f"section:structure:character_arcs:character:{key}",
            "kind": "generate_document_section",
            "status": "completed",
            "input": {
                "documentSectionKey": "character_arcs",
                "characterPosition": position,
            },
            "output": {
                **candidate["payload"],
                "contentText": candidate["contentText"],
                "runId": f"run-character-{key}",
            },
        })

    hook_unit = {
        "id": "section:structure:hooks",
        "kind": "project_structure_hooks",
        "status": "running",
        "dependsOn": [
            str(unit["id"])
            for unit in units
            if str(unit.get("id") or "").startswith(
                "section:structure:episode_plan:episode-"
            ) or str(unit.get("id") or "").startswith(
                "section:structure:character_arcs:character:"
            )
        ],
        "input": {"sectionKey": "hooks"},
    }
    hook_output = ScreenplayTaskModelCalls._project_structure_hooks(
        {"units": units},
        hook_unit,
    )
    hook_unit.update({"status": "completed", "output": hook_output})
    units.append(hook_unit)
    validation = {
        "id": "document:validation",
        "kind": "validate_manifest_part",
        "status": "running",
        "input": {"validationKind": "document", "targetRole": "structure"},
    }
    units.append(validation)

    result = _validate_document_parts(
        {"targetRole": "structure", "units": units},
        validation,
    )

    assert len(result["contentJson"]["episodes"]) == 10
    assert len(result["contentJson"]["seriesArc"]["phases"]) == 2
    assert len(result["contentJson"]["characterArcs"]) == 2
    assert len(result["contentJson"]["hooks"]) == 10
    assert all(
        len(output["contentJson"]["episodes"]) == 1
        for output in episode_outputs
    )
    assert "episode_plan:index" not in result["contentText"]


async def test_scripted_gateway_executes_ten_episode_structure_as_small_runs(
    temp_db: DatabaseConnection,
):
    _, workspace, session = await _project_and_session(temp_db)
    tool_calls = _CheckpointingToolCalls()
    executor = ScreenplayTaskModelCalls(
        temp_db,
        tool_calling_service=tool_calls,  # type: ignore[arg-type]
    )
    task = {
        "id": "task-scripted-structure-ten",
        "projectId": workspace["project"]["id"],
        "sessionId": session["id"],
        "turnId": "turn-scripted-structure-ten",
        "rootRunId": "run-scripted-structure-ten",
        "targetRole": "structure",
        "maxGeneratedUnits": 134,
        "units": [{
            "id": "document:evidence",
            "kind": "collect_evidence",
            "status": "completed",
            "input": {"targetRole": "structure"},
            "output": {"evidenceDescriptor": {
                "projectId": workspace["project"]["id"],
                "targetRole": "structure",
                "sourceRevisionRefs": [],
                "structureEpisodeNumbers": [],
            }},
        }],
    }

    async def run_part(
        unit_id: str,
        unit_input: dict,
        dependencies: list[str],
    ) -> dict:
        unit = {
            "id": unit_id,
            "kind": "generate_document_section",
            "status": "pending",
            "dependsOn": dependencies,
            "input": {"targetRole": "structure", **unit_input},
        }
        task["units"].append(unit)
        output = dict(await executor.execute(
            task=task,
            unit=unit,
            runtime=object(),
        ))
        unit.update({"status": "completed", "output": output})
        return output

    series_index_id = "section:structure:series_arc:index"
    await run_part(
        series_index_id,
        {
            "sectionKey": "series_arc:index",
            "documentSectionKey": "series_arc",
            "seriesArcIndex": True,
        },
        ["document:evidence"],
    )
    phase_ids = []
    for position, phase in enumerate((
        ("setup", "进入困局", "建立目标。"),
        ("resolution", "完成抉择", "兑现选择。"),
    ), 1):
        key, title, objective = phase
        phase_id = f"section:structure:series_arc:phase:{key}"
        phase_ids.append(phase_id)
        await run_part(
            phase_id,
            {
                "sectionKey": f"series_arc:phase:{key}",
                "documentSectionKey": "series_arc",
                "phaseKey": key,
                "phaseTitle": title,
                "phaseObjective": objective,
                "phasePosition": position,
            },
            ["document:evidence", series_index_id],
        )

    episode_index_id = "section:structure:episode_plan:index"
    await run_part(
        episode_index_id,
        {
            "sectionKey": "episode_plan:index",
            "documentSectionKey": "episode_plan",
            "episodePlanIndex": True,
        },
        phase_ids,
    )
    episode_ids = []
    for number in range(1, 11):
        unit_id = f"section:structure:episode_plan:episode-{number}"
        episode_ids.append(unit_id)
        await run_part(
            unit_id,
            {
                "sectionKey": f"episode_plan:episode-{number}",
                "documentSectionKey": "episode_plan",
                "episodeNumber": number,
                "episodeId": f"ep{number:02d}",
                "episodeTitle": f"第 {number} 集",
            },
            ["document:evidence", episode_index_id],
        )

    character_index_id = "section:structure:character_arcs:index"
    await run_part(
        character_index_id,
        {
            "sectionKey": "character_arcs:index",
            "documentSectionKey": "character_arcs",
            "characterArcsIndex": True,
        },
        episode_ids,
    )
    character_ids = []
    for position, (key, name) in enumerate((
        ("linyue", "林月"),
        ("suwen", "苏文"),
    ), 1):
        unit_id = f"section:structure:character_arcs:character:{key}"
        character_ids.append(unit_id)
        await run_part(
            unit_id,
            {
                "sectionKey": f"character_arcs:character:{key}",
                "documentSectionKey": "character_arcs",
                "characterKey": key,
                "characterName": name,
                "characterPosition": position,
            },
            ["document:evidence", character_index_id, *episode_ids],
        )

    hooks = {
        "id": "section:structure:hooks",
        "kind": "project_structure_hooks",
        "status": "pending",
        "dependsOn": [*episode_ids, *character_ids],
        "input": {"targetRole": "structure", "sectionKey": "hooks"},
    }
    task["units"].append(hooks)
    hooks.update({
        "status": "completed",
        "output": dict(await executor.execute(
            task=task,
            unit=hooks,
            runtime=object(),
        )),
    })
    validation = {
        "id": "document:validation",
        "kind": "validate_manifest_part",
        "status": "pending",
        "dependsOn": [*phase_ids, *episode_ids, *character_ids, hooks["id"]],
        "input": {"validationKind": "document", "targetRole": "structure"},
    }
    task["units"].append(validation)
    result = await executor.execute(
        task=task,
        unit=validation,
        runtime=object(),
    )

    assert len(tool_calls.calls) == 17
    assert sum(
        key.startswith("episode_plan:episode-")
        for _part_type, key, _reasoning in tool_calls.calls
    ) == 10
    assert all(
        len(payload["partIdentity"]) == 3
        for payload in tool_calls.user_payloads
        if str(payload["sectionKey"]).startswith("episode_plan:episode-")
    )
    assert "hooks" not in [key for _type, key, _mode in tool_calls.calls]
    assert len(result["contentJson"]["episodes"]) == 10
    assert len(result["contentJson"]["hooks"]) == 10
