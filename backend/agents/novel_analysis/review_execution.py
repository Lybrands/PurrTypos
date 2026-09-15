"""Final review and Root response projection for scalable novel analysis."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass

from agents.novel_analysis.attempt_artifact import NovelAnalysisAttemptArtifactStore
from agents.novel_analysis.child_submission import (
    NovelAnalysisChildSubmissionStore,
    SUBMIT_NOVEL_ANALYSIS_CHILD_RESULT,
)
from agents.novel_analysis.canonical_materials import (
    CanonicalAnalysisMaterialError,
    validate_canonical_materials,
)
from agents.novel_analysis.map_execution import (
    PurrAReusableChildCoordinator,
    inherited_agent_input_payload,
    raise_child_run_failure,
)
from agents.novel_analysis.reduce_execution import _artifact_id
from infrastructure.persistence.sqlite_run_tree_repository import SqliteRunTreeRepository
from purra.agent_tree import AgentCapabilityGrant, ChildAgentSpec
from purra.cancellation import raise_if_stopped
from purra.contracts import ToolDataContract, ToolExecutionMode, ToolHandlerResult, ToolPolicy, ToolRiskLevel, ToolSchema
from purra.json_values import canonical_json_digest, thaw_json_mapping
from purra.long_tasks import LongTaskUnitResult
from purra.ports import ToolRegistration
from purra.recovery import FailureCategory, FailureSignal
from purra.tools import InMemoryToolCatalog


READ_NOVEL_ANALYSIS_REVIEW_INPUT = "readNovelAnalysisReviewInput"
REVIEW_SCOPE_STATE_KEY = "novelAnalysisReviewScope"
REVIEW_OUTPUT_SCHEMA_VERSION = 3


class ScalableReviewExecutionError(ValueError):
    code = "novel_analysis_review_execution_invalid"


class ScalableReviewOutputError(ValueError):
    code = "novel_analysis_review_output_invalid"


@dataclass(frozen=True, slots=True)
class ReviewInputScope:
    skill_artifact_id: str
    skill_digest: str
    coverage_artifact_id: str
    coverage_digest: str
    synthesis_artifact_id: str
    synthesis_digest: str
    fact_ids: tuple[str, ...]
    card_ids: tuple[str, ...]
    technique_result: Mapping[str, object]

    def to_mapping(self):
        return {
            "skillArtifactId": self.skill_artifact_id,
            "skillDigest": self.skill_digest,
            "coverageArtifactId": self.coverage_artifact_id,
            "coverageDigest": self.coverage_digest,
            "synthesisArtifactId": self.synthesis_artifact_id,
            "synthesisDigest": self.synthesis_digest,
            "factIds": list(self.fact_ids),
            "cardIds": list(self.card_ids),
            "techniqueResult": dict(self.technique_result),
        }

    @classmethod
    def from_mapping(cls, raw):
        if not isinstance(raw, Mapping) or set(raw) != {
            "skillArtifactId", "skillDigest",
            "coverageArtifactId", "coverageDigest", "synthesisArtifactId",
            "synthesisDigest", "factIds", "cardIds", "techniqueResult",
        }:
            raise ScalableReviewExecutionError("Review scope shape is invalid")
        identifiers = tuple(str(raw[key] or "").strip() for key in (
            "skillArtifactId", "skillDigest", "coverageArtifactId", "coverageDigest",
            "synthesisArtifactId", "synthesisDigest"
        ))
        fact_ids, card_ids = raw.get("factIds"), raw.get("cardIds")
        if not isinstance(fact_ids, (list, tuple)) or not isinstance(card_ids, (list, tuple)):
            raise ScalableReviewExecutionError("Review material ids are invalid")
        technique_result = raw.get("techniqueResult")
        if not isinstance(technique_result, Mapping):
            raise ScalableReviewExecutionError("Review Skill result is invalid")
        scope = cls(
            *identifiers,
            tuple(str(item or "").strip() for item in fact_ids),
            tuple(str(item or "").strip() for item in card_ids),
            dict(technique_result),
        )
        if not all(identifiers) or not scope.fact_ids or any(not item for item in (*scope.fact_ids, *scope.card_ids)):
            raise ScalableReviewExecutionError("Review scope is incomplete")
        return scope


class ReviewInputScopeCompiler:
    def __init__(self, db):
        self._store = NovelAnalysisAttemptArtifactStore(db)

    async def compile(self, context):
        if str(context.unit.metadata.get("unitKind") or "") != "review":
            raise ScalableReviewExecutionError("Review compiler received another Unit kind")
        dependencies = tuple(context.unit.dependencies)
        if len(dependencies) != 1 or set(context.dependency_outputs) != set(dependencies):
            raise ScalableReviewExecutionError("Review dependency contract is invalid")
        skill_id = _artifact_id(context.dependency_outputs[dependencies[0]])
        skill = await self._store.load_payload(skill_id)
        if skill.get("kind") != "skill" or not isinstance(skill.get("techniqueResult"), Mapping):
            raise ScalableReviewExecutionError("Review requires a completed writing Skill")
        coverage_id = str(skill.get("coverageArtifactId") or "").strip()
        coverage = await self._store.load_payload(coverage_id)
        if (
            coverage.get("kind") != "coverage"
            or coverage.get("covered") is not True
            or coverage.get("summaryMarkdownPresent") is not True
        ):
            raise ScalableReviewExecutionError("Review requires a successful Coverage Artifact")
        synthesis_id = str(coverage.get("synthesisArtifactId") or "").strip()
        synthesis = await self._store.load_payload(synthesis_id)
        if (
            canonical_json_digest(synthesis) != coverage.get("synthesisDigest")
            or synthesis.get("kind") != "synthesize"
            or not str(synthesis.get("summaryMarkdown") or "").strip()
        ):
            raise ScalableReviewExecutionError("Review Synthesis Artifact failed verification")
        facts, cards = synthesis.get("facts"), synthesis.get("craftCards")
        if not isinstance(facts, list) or not facts or not isinstance(cards, list):
            raise ScalableReviewExecutionError("Review Synthesis materials are invalid")
        return ReviewInputScope(
            skill_id, canonical_json_digest(skill),
            coverage_id, canonical_json_digest(coverage),
            synthesis_id, canonical_json_digest(synthesis),
            tuple(str(item.get("id") or "") for item in facts if isinstance(item, Mapping)),
            tuple(str(item.get("id") or "") for item in cards if isinstance(item, Mapping)),
            dict(skill["techniqueResult"]),
        )


def build_review_input_tool_catalog(db, *, run_tree_repository=None):
    store = NovelAnalysisAttemptArtifactStore(db)
    tree = run_tree_repository or SqliteRunTreeRepository(db)

    async def read_input(state, arguments, signal=None):
        del arguments
        raise_if_stopped(signal)
        try:
            raw = state.domain.get(REVIEW_SCOPE_STATE_KEY)
            if raw is None:
                raw = await inherited_agent_input_payload(
                    tree, str(state.run_id or ""), "reviewInputScope"
                )
            scope = ReviewInputScope.from_mapping(thaw_json_mapping(raw))
            skill = await store.load_payload(scope.skill_artifact_id)
            coverage = await store.load_payload(scope.coverage_artifact_id)
            synthesis = await store.load_payload(scope.synthesis_artifact_id)
            if canonical_json_digest(skill) != scope.skill_digest or canonical_json_digest(coverage) != scope.coverage_digest or canonical_json_digest(synthesis) != scope.synthesis_digest:
                raise ScalableReviewExecutionError("Review inputs changed after binding")
            return ToolHandlerResult(content=json.dumps({
                "coverage": {
                    "covered": coverage["covered"],
                    "expectedSliceIds": coverage["expectedSliceIds"],
                    "expectedPassIds": coverage["expectedPassIds"],
                },
                "synthesis": {
                    "summaryMarkdown": synthesis["summaryMarkdown"],
                    "facts": synthesis["facts"],
                    "craftCards": synthesis["craftCards"],
                    "techniqueResult": scope.technique_result,
                },
            }, ensure_ascii=False, allow_nan=False))
        except Exception as error:
            failure = error if isinstance(error, ScalableReviewExecutionError) else ScalableReviewExecutionError("Review inputs are unavailable")
            return ToolHandlerResult(content=json.dumps({"success": False, "code": failure.code, "error": str(failure)}), error_code=failure.code)

    return InMemoryToolCatalog((ToolRegistration(
        schema=ToolSchema(name=READ_NOVEL_ANALYSIS_REVIEW_INPUT, description="读取已通过覆盖门禁的整书总结。不能读取原文或选择其他 Artifact。", parameters={"type": "object", "properties": {}, "required": [], "additionalProperties": False}, display_names={"zh-CN": "审核整书分析", "en": "Read analysis for review"}),
        handler=read_input,
        policy=ToolPolicy(ToolExecutionMode.READ, "审核整书分析", ToolRiskLevel.READ),
        concurrency_safe=True,
        data_contract=ToolDataContract(model_owned_paths=(), host_bound_paths=("skillArtifactId", "coverageArtifactId", "synthesisArtifactId")),
        operation_display_params=lambda state, arguments, call: {"displayNames": {"zh-CN": "审核整书分析", "en-US": "Read analysis for review"}},
    ),))


class PurrAScalableReviewChildRunner:
    def __init__(self, db=None, *, model_name, submissions=None):
        self._model_name = str(model_name or "").strip()
        if not self._model_name:
            raise ValueError("Review Child runner requires a model name")
        self._core = None
        self._children = PurrAReusableChildCoordinator()
        if submissions is None and db is None:
            raise ValueError("Review Child runner requires a submission store")
        self._submissions = submissions or NovelAnalysisChildSubmissionStore(db)

    def bind_agent_core(self, core):
        self._core = core
        self._children.bind_agent_core(core)

    async def run(self, *, scope, context, signal=None):
        if self._core is None:
            raise RuntimeError("Review Child runner has no active Agent Core")
        operation_id = f"{context.task.id}:{context.unit.id}:{context.unit.attempt}"
        child = ChildAgentSpec(
            name="review-whole-work", title="审核整书分析总结",
            instruction=(
                f"必须调用 {READ_NOVEL_ANALYSIS_REVIEW_INPUT} 一次。"
                "逐项审核规范 facts 和 craftCards。"
                "人物资料按单个角色组织，每条 character_summary 只记录一个角色。"
                "可以修正文案、修正 factKind 或删除不可靠对象，但不得新增或改变 id，且技法引用必须仍指向保留的 craftCards。"
                "必须保留且只保留一条 background。"
                "人物、背景、世界设定的 value 必须保持创作表单字段结构。"
                "写作 Skill 已由专用 Creator 创建，只审核它与写法观察是否一致，不要重写文件或返回 techniqueResult。"
                f"完成后必须调用 {SUBMIT_NOVEL_ANALYSIS_CHILD_RESULT} 提交结果；"
                "最终回复不要承载分析数据。提交的 result 只包含输入中的 summaryMarkdown、facts、craftCards 三类资料。"
            ),
            objective="形成可供用户编辑并直接发布到续写流程的规范来源资料。",
            input_payload={
                "reviewInputScope": scope.to_mapping(),
                "unitId": context.unit.id,
                "attempt": context.unit.attempt,
            },
            capability_grant=AgentCapabilityGrant(can_spawn_agents=False, max_depth=1, max_children_per_call=1, max_agents_per_root=16, max_parallel_runs=1, allowed_tools=(READ_NOVEL_ANALYSIS_REVIEW_INPUT, SUBMIT_NOVEL_ANALYSIS_CHILD_RESULT), allowed_models=(self._model_name,)),
        )
        child_id, aggregation = await self._children.run(
            root_run_id=context.run_id,
            reuse_key=f"review-unit:{context.unit.id}:attempt:{context.unit.attempt}",
            idempotency_key=f"review-child:{operation_id}",
            child=child,
            signal=signal,
        )
        if aggregation.pending_run_ids or aggregation.required_failures:
            raise_child_run_failure(aggregation, child_id, "Review")
        try:
            return child_id, await self._submissions.load(child_id)
        except ValueError as error:
            raise ScalableReviewOutputError(
                "Review Child did not submit its structured result"
            ) from error


class ScalableReviewUnitExecutor:
    def __init__(self, db, *, child_runner):
        self._db, self._runner = db, child_runner
        self._compiler, self._store = ReviewInputScopeCompiler(db), NovelAnalysisAttemptArtifactStore(db)

    def bind_agent_core(self, core): self._runner.bind_agent_core(core)

    async def execute(self, context, signal=None):
        scope = await self._compiler.compile(context)
        operation_id = f"{context.task.id}:{context.unit.id}:{context.unit.attempt}"
        recovery = await self._store.try_load_execution_payload(task_id=context.task.id, unit_id=context.unit.id, attempt=context.unit.attempt, error_code=getattr(context.unit, "error_code", None))
        recovered_operation_id = None
        if recovery is None:
            child_id, raw = await self._runner.run(scope=scope, context=context, signal=signal)
            await _require_child(self._db, context.run_id, child_id)
            payload = _validate_output(raw, scope, child_id)
        else:
            recovered, recovered_operation_id = recovery
            child_id = str(recovered.get("childRunId") or "")
            await _require_child(self._db, context.run_id, child_id, allow_previous_root=recovered_operation_id != operation_id)
            payload = _validate_committed(recovered, scope)
        receipt = await self._store.commit(task_id=context.task.id, unit_id=context.unit.id, attempt=context.unit.attempt, operation_id=operation_id, run_id=child_id, payload=payload)
        return LongTaskUnitResult(output_ref=receipt.resource_ref, artifact_digest=canonical_json_digest(payload), validation_receipt={"schemaVersion": 1, "unitKind": "review", "childRunId": child_id, "artifactReplayed": receipt.replayed, **({"recoveredOperationId": recovered_operation_id} if recovered_operation_id is not None and recovered_operation_id != operation_id else {})}, metadata={"artifactId": receipt.artifact_id, "finalResponse": payload["summaryMarkdown"]})

    def classify_failure(self, error):
        retryable = isinstance(error, ScalableReviewOutputError) or not isinstance(error, ScalableReviewExecutionError)
        category = (
            FailureCategory.MODEL_OUTPUT_INVALID
            if isinstance(error, ScalableReviewOutputError)
            else FailureCategory.TOOL_EXECUTION
            if retryable
            else FailureCategory.BUSINESS_INVARIANT
        )
        return FailureSignal(category=category, code=str(getattr(error, "code", "") or type(error).__name__)[:240], retryable=retryable)


def _validate_output(raw, scope, child_id):
    if not isinstance(raw, Mapping):
        raise ScalableReviewOutputError("Review output shape is invalid")
    try:
        materials = validate_canonical_materials(raw)
    except CanonicalAnalysisMaterialError as error:
        raise ScalableReviewOutputError(str(error)) from error
    facts, cards = materials["facts"], materials["craftCards"]
    if any(item["id"] not in scope.fact_ids for item in facts) or any(item["id"] not in scope.card_ids for item in cards):
        raise ScalableReviewOutputError("Review introduced a new material id")
    return {
        "schemaVersion": REVIEW_OUTPUT_SCHEMA_VERSION,
        "kind": "review",
        "childRunId": child_id,
        "skillArtifactId": scope.skill_artifact_id,
        "skillDigest": scope.skill_digest,
        "coverageArtifactId": scope.coverage_artifact_id,
        "coverageDigest": scope.coverage_digest,
        "synthesisArtifactId": scope.synthesis_artifact_id,
        "synthesisDigest": scope.synthesis_digest,
        **materials,
        "techniqueResult": dict(scope.technique_result),
    }


def _validate_committed(payload, scope):
    if payload.get("kind") != "review" or payload.get("skillArtifactId") != scope.skill_artifact_id or payload.get("skillDigest") != scope.skill_digest or payload.get("coverageArtifactId") != scope.coverage_artifact_id or payload.get("coverageDigest") != scope.coverage_digest or payload.get("synthesisArtifactId") != scope.synthesis_artifact_id or payload.get("synthesisDigest") != scope.synthesis_digest:
        raise ScalableReviewExecutionError("Committed Review identity conflict")
    return _validate_output({key: payload.get(key) for key in ("summaryMarkdown", "facts", "craftCards")}, scope, str(payload.get("childRunId") or ""))


async def _require_child(db, root_id, child_id, *, allow_previous_root=False):
    row = await db.fetch_one("SELECT root_run_id, parent_run_id FROM ai_agent_runs WHERE id = ?", [child_id])
    if not child_id or child_id == root_id or row is None or not ((row["root_run_id"] == root_id and row["parent_run_id"] == root_id) or (allow_previous_root and row["root_run_id"] and row["parent_run_id"] == row["root_run_id"])):
        raise ScalableReviewExecutionError("Review requires a Child owned by this Root")


__all__ = ["PurrAScalableReviewChildRunner", "READ_NOVEL_ANALYSIS_REVIEW_INPUT", "ReviewInputScopeCompiler", "ScalableReviewExecutionError", "ScalableReviewOutputError", "ScalableReviewUnitExecutor", "build_review_input_tool_catalog"]
