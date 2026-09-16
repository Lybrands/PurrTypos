"""Bounded whole-work synthesis over final per-pass analysis Artifacts."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from agents.novel_analysis.attempt_artifact import NovelAnalysisAttemptArtifactStore
from agents.novel_analysis.child_submission import (
    NovelAnalysisChildSubmissionStore,
    SUBMIT_NOVEL_ANALYSIS_CHILD_RESULT,
)
from agents.novel_analysis.canonical_materials import (
    CANONICAL_CLAIM_NATURES,
    CanonicalAnalysisMaterialError,
    validate_canonical_materials,
)
from agents.novel_analysis.domain import NOVEL_ANALYSIS_PUBLISHABLE_FACT_KINDS
from agents.novel_analysis.map_execution import (
    PurrAReusableChildCoordinator,
    inherited_agent_input_payload,
    raise_child_run_failure,
)
from agents.novel_analysis.root_model_execution import (
    is_root_model_producer,
    uses_root_model,
)
from agents.novel_analysis.reduce_execution import _artifact_id, _validate_dependency_payload
from infrastructure.persistence.sqlite_run_tree_repository import SqliteRunTreeRepository
from purra.agent_tree import AgentCapabilityGrant, ChildAgentSpec
from purra.cancellation import raise_if_stopped
from purra.context_budget import estimate_json_tokens
from purra.contracts import ToolDataContract, ToolExecutionMode, ToolHandlerResult, ToolPolicy, ToolRiskLevel, ToolSchema
from purra.json_values import canonical_json_digest, thaw_json_mapping
from purra.long_tasks import LongTaskUnitResult
from purra.ports import ToolRegistration
from purra.recovery import FailureCategory, FailureSignal
from purra.tools import InMemoryToolCatalog


READ_NOVEL_ANALYSIS_SYNTHESIS_INPUTS = "readNovelAnalysisSynthesisInputs"
SYNTHESIS_SCOPE_STATE_KEY = "novelAnalysisSynthesisInputScope"
SYNTHESIS_OUTPUT_SCHEMA_VERSION = 3


class ScalableSynthesisExecutionError(ValueError):
    code = "novel_analysis_synthesis_execution_invalid"


class ScalableSynthesisOutputError(ValueError):
    code = "novel_analysis_synthesis_output_invalid"


@dataclass(frozen=True, slots=True)
class SynthesisInputLocator:
    pass_id: str
    unit_id: str
    artifact_id: str
    payload_digest: str

    def to_mapping(self):
        return {"passId": self.pass_id, "unitId": self.unit_id, "artifactId": self.artifact_id, "payloadDigest": self.payload_digest}


@dataclass(frozen=True, slots=True)
class SynthesisInputScope:
    expected_slice_ids: tuple[str, ...]
    sections: tuple[str, ...]
    input_token_limit: int
    artifacts: tuple[SynthesisInputLocator, ...]

    def to_mapping(self):
        return {
            "expectedSliceIds": list(self.expected_slice_ids),
            "sections": list(self.sections),
            "inputTokenLimit": self.input_token_limit,
            "artifacts": [item.to_mapping() for item in self.artifacts],
        }

    @classmethod
    def from_mapping(cls, raw):
        if not isinstance(raw, Mapping) or not isinstance(raw.get("artifacts"), list):
            raise ScalableSynthesisExecutionError("Synthesis input scope is invalid")
        try:
            scope = cls(
                expected_slice_ids=tuple(raw["expectedSliceIds"]),
                sections=tuple(raw["sections"]),
                input_token_limit=int(raw["inputTokenLimit"]),
                artifacts=tuple(SynthesisInputLocator(
                    pass_id=str(item["passId"]), unit_id=str(item["unitId"]),
                    artifact_id=str(item["artifactId"]), payload_digest=str(item["payloadDigest"]),
                ) for item in raw["artifacts"]),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ScalableSynthesisExecutionError("Synthesis input scope is invalid") from error
        if not scope.expected_slice_ids or not scope.sections or not scope.artifacts or scope.input_token_limit < 1:
            raise ScalableSynthesisExecutionError("Synthesis input scope is incomplete")
        if (
            len({item.pass_id for item in scope.artifacts}) != len(scope.artifacts)
            or any(not item.pass_id or not item.unit_id or not item.artifact_id or not item.payload_digest for item in scope.artifacts)
        ):
            raise ScalableSynthesisExecutionError("Synthesis input locators are invalid")
        return scope


class SynthesisInputScopeCompiler:
    def __init__(self, db):
        self._artifacts = NovelAnalysisAttemptArtifactStore(db)

    async def compile(self, context):
        if str(context.unit.metadata.get("unitKind") or "") != "synthesize":
            raise ScalableSynthesisExecutionError("Synthesis compiler received another Unit kind")
        dependencies = tuple(context.unit.dependencies)
        if not dependencies or set(context.dependency_outputs) != set(dependencies):
            raise ScalableSynthesisExecutionError("Synthesis dependency contract is invalid")
        plan = context.task.metadata.get("analysisPlan")
        manifest = context.task.metadata.get("sliceManifest")
        if not isinstance(plan, Mapping) or not isinstance(manifest, Mapping):
            raise ScalableSynthesisExecutionError("Synthesis task contract is unavailable")
        passes = plan.get("passes")
        slices = manifest.get("slices")
        sections = tuple(context.unit.metadata.get("sections") or ())
        if (
            not isinstance(passes, Sequence)
            or isinstance(passes, (str, bytes))
            or not isinstance(slices, Sequence)
            or isinstance(slices, (str, bytes))
            or not sections
        ):
            raise ScalableSynthesisExecutionError("Synthesis task contract is invalid")
        expected_passes = tuple(str(item.get("id") or "") for item in passes if isinstance(item, Mapping))
        expected_slices = tuple(str(item.get("sliceId") or "") for item in slices if isinstance(item, Mapping))
        locators = []
        payloads = []
        seen_passes = []
        for unit_id in dependencies:
            artifact_id = _artifact_id(context.dependency_outputs[unit_id])
            payload = await self._artifacts.load_payload(artifact_id)
            pass_id = str(payload.get("passId") or "")
            try:
                lineage = _validate_dependency_payload(payload, pass_id=pass_id)
            except ValueError as error:
                raise ScalableSynthesisExecutionError("Synthesis dependency lineage is invalid") from error
            if tuple(lineage) != expected_slices:
                raise ScalableSynthesisExecutionError("Synthesis pass does not cover every slice")
            seen_passes.append(pass_id)
            payloads.append(payload)
            locators.append(SynthesisInputLocator(pass_id, unit_id, artifact_id, canonical_json_digest(payload)))
        if tuple(seen_passes) != expected_passes:
            raise ScalableSynthesisExecutionError("Synthesis dependencies do not match planned passes")
        context_window = manifest.get("contextWindowTokens")
        if type(context_window) is not int or context_window < 1:
            raise ScalableSynthesisExecutionError("Synthesis context window is invalid")
        input_limit = context_window * 2 // 5 * 9 // 10
        projected_inputs = [
            {"passId": locator.pass_id, "payload": payload}
            for locator, payload in zip(locators, payloads, strict=True)
        ]
        if estimate_json_tokens(projected_inputs) > input_limit:
            raise ScalableSynthesisExecutionError("Synthesis inputs exceed their budget")
        return SynthesisInputScope(expected_slices, sections, input_limit, tuple(locators))


def build_synthesis_input_tool_catalog(db, *, run_tree_repository=None):
    store = NovelAnalysisAttemptArtifactStore(db)
    tree = run_tree_repository or SqliteRunTreeRepository(db)

    async def read_inputs(state, arguments, signal=None):
        del arguments
        raise_if_stopped(signal)
        try:
            raw = state.domain.get(SYNTHESIS_SCOPE_STATE_KEY)
            if raw is None:
                raw = await inherited_agent_input_payload(
                    tree, str(state.run_id or ""), "synthesisInputScope"
                )
            scope = SynthesisInputScope.from_mapping(thaw_json_mapping(raw))
            inputs = []
            for locator in scope.artifacts:
                payload = await store.load_payload(locator.artifact_id)
                if canonical_json_digest(payload) != locator.payload_digest:
                    raise ScalableSynthesisExecutionError("Synthesis input changed after binding")
                inputs.append({"passId": locator.pass_id, "payload": payload})
            if estimate_json_tokens(inputs) > scope.input_token_limit:
                raise ScalableSynthesisExecutionError("Synthesis Tool result exceeds its budget")
            return ToolHandlerResult(content=json.dumps({"sections": list(scope.sections), "inputs": inputs}, ensure_ascii=False, allow_nan=False))
        except Exception as error:
            failure = error if isinstance(error, ScalableSynthesisExecutionError) else ScalableSynthesisExecutionError("Synthesis inputs are unavailable")
            return ToolHandlerResult(content=json.dumps({"success": False, "code": failure.code, "error": str(failure)}), error_code=failure.code)

    return InMemoryToolCatalog((ToolRegistration(
        schema=ToolSchema(name=READ_NOVEL_ANALYSIS_SYNTHESIS_INPUTS, description="读取 Host 绑定的各分析 pass 最终 Artifact。不能选择输入。", parameters={"type": "object", "properties": {}, "required": [], "additionalProperties": False}, display_names={"zh-CN": "读取整书分析结果", "en": "Read synthesis inputs"}),
        handler=read_inputs,
        policy=ToolPolicy(ToolExecutionMode.READ, "读取整书分析结果", ToolRiskLevel.READ),
        concurrency_safe=True,
        data_contract=ToolDataContract(model_owned_paths=(), host_bound_paths=("artifacts", "sections")),
        operation_display_params=lambda state, arguments, call: {"displayNames": {"zh-CN": "读取整书分析结果", "en-US": "Read synthesis inputs"}},
    ),))


def _synthesis_result_instruction(scope) -> str:
    required_sections = json.dumps(
        list(scope.sections), ensure_ascii=False, allow_nan=False
    )
    fact_kinds = "、".join(sorted(NOVEL_ANALYSIS_PUBLISHABLE_FACT_KINDS))
    claim_natures = "、".join(sorted(CANONICAL_CLAIM_NATURES))
    return (
        "只综合整部作品，不按分片罗列，不引用原文。"
        f"Planner 要求覆盖的主题为：{required_sections}。这些是分析范围，不是输出字段。"
        "直接生成续写所消费的规范资料：facts 是独立人物、背景、设定、事件、"
        "时间线、未决情节或伏笔；craftCards 是独立写作技法观察。"
        f"factKind 使用以下一种：{fact_kinds}。"
        f"claimNature 使用 {claim_natures}；未决情节由 factKind=unresolved_plot 表达。"
        "每个 fact/card 使用本次结果内唯一且稳定的短 id。"
        "必须且只能输出一条 background；原文明示不足时基于全文给出 inference 背景归纳。"
        "人物按单个角色分别输出 character_summary，value 使用 "
        '{"name":"与 subjectKey 完全一致","tags":"人物标签",'
        '"profile_md":"完整 Markdown 人物档案"}。'
        "background 的 value 使用 "
        '{"content":"完整 Markdown 背景"}。'
        "setting/location/faction/item/world_rule 的 value 使用 "
        '{"entity_type":"location、faction、item 或 other",'
        '"name":"与 subjectKey 完全一致","tags":"逗号分隔标签",'
        '"profile_md":"完整 Markdown 档案"}；setting 和 world_rule 使用 other。'
        "只返回 summaryMarkdown、facts、craftCards。"
    )


class PurrAScalableSynthesisChildRunner:
    def __init__(self, db=None, *, model_name, submissions=None, root_runner=None):
        self._model_name = str(model_name or "").strip()
        if not self._model_name:
            raise ValueError("Synthesis Child runner requires a model name")
        self._core = None
        self._root_runner = root_runner
        self._artifacts = NovelAnalysisAttemptArtifactStore(db) if db is not None else None
        self._children = PurrAReusableChildCoordinator()
        if submissions is None and db is None:
            raise ValueError("Synthesis Child runner requires a submission store")
        self._submissions = submissions or NovelAnalysisChildSubmissionStore(db)

    def bind_agent_core(self, core):
        self._core = core
        self._children.bind_agent_core(core)

    async def run(self, *, scope, context, signal=None):
        if uses_root_model(context):
            if self._root_runner is None or self._artifacts is None:
                raise RuntimeError("Synthesis Root runner is unavailable")
            inputs = [
                {
                    "passId": locator.pass_id,
                    "payload": await self._artifacts.load_payload(locator.artifact_id),
                }
                for locator in scope.artifacts
            ]
            payload = await self._root_runner.complete(
                context=context,
                instruction=_synthesis_result_instruction(scope),
                inputs={"sections": list(scope.sections), "inputs": inputs},
                signal=signal,
            )
            return context.run_id, payload
        if self._core is None:
            raise RuntimeError("Synthesis Child runner has no active Agent Core")
        operation_id = f"{context.task.id}:{context.unit.id}:{context.unit.attempt}"
        child = ChildAgentSpec(
            name="synthesize-whole-work", title="形成整书分析总结",
                instruction=(
                    f"必须调用 {READ_NOVEL_ANALYSIS_SYNTHESIS_INPUTS} 一次。"
                    + _synthesis_result_instruction(scope)
                    + f"完成后必须调用 {SUBMIT_NOVEL_ANALYSIS_CHILD_RESULT} 提交结果；"
                    "最终回复不要承载分析数据。"
                ),
            objective="形成整书人物、背景、设定、情节与技法总结。",
            input_payload={
                "synthesisInputScope": scope.to_mapping(),
                "unitId": context.unit.id,
                "attempt": context.unit.attempt,
            },
            capability_grant=AgentCapabilityGrant(can_spawn_agents=False, max_depth=1, max_children_per_call=1, max_agents_per_root=16, max_parallel_runs=1, allowed_tools=(READ_NOVEL_ANALYSIS_SYNTHESIS_INPUTS, SUBMIT_NOVEL_ANALYSIS_CHILD_RESULT), allowed_models=(self._model_name,)),
        )
        child_id, aggregation = await self._children.run(
            root_run_id=context.run_id,
            reuse_key=f"synthesis-unit:{context.unit.id}:attempt:{context.unit.attempt}",
            idempotency_key=f"synthesis-child:{operation_id}",
            child=child,
            signal=signal,
        )
        if aggregation.pending_run_ids or aggregation.required_failures:
            raise_child_run_failure(aggregation, child_id, "Synthesis")
        try:
            payload = await self._submissions.load(child_id)
        except ValueError as error:
            raise ScalableSynthesisOutputError(
                "Synthesis Child did not submit its structured result"
            ) from error
        return child_id, payload


class ScalableSynthesisUnitExecutor:
    def __init__(self, db, *, child_runner):
        self._db, self._runner = db, child_runner
        self._compiler, self._store = SynthesisInputScopeCompiler(db), NovelAnalysisAttemptArtifactStore(db)

    def bind_agent_core(self, core): self._runner.bind_agent_core(core)

    async def execute(self, context, signal=None):
        scope = await self._compiler.compile(context)
        operation_id = f"{context.task.id}:{context.unit.id}:{context.unit.attempt}"
        recovery = await self._store.try_load_execution_payload(task_id=context.task.id, unit_id=context.unit.id, attempt=context.unit.attempt, error_code=getattr(context.unit, "error_code", None))
        recovered_operation_id = None
        if recovery is None:
            child_id, raw = await self._runner.run(scope=scope, context=context, signal=signal)
            await _require_model_run(self._db, context, child_id)
            payload = _validate_output(raw, scope, child_id)
        else:
            recovered, recovered_operation_id = recovery
            child_id = str(recovered.get("childRunId") or "")
            await _require_model_run(self._db, context, child_id, allow_previous_root=recovered_operation_id != operation_id)
            payload = _validate_committed(recovered, scope)
        receipt = await self._store.commit(task_id=context.task.id, unit_id=context.unit.id, attempt=context.unit.attempt, operation_id=operation_id, run_id=child_id, payload=payload)
        return LongTaskUnitResult(output_ref=receipt.resource_ref, artifact_digest=canonical_json_digest(payload), validation_receipt={"schemaVersion": 1, "unitKind": "synthesize", "childRunId": child_id, "artifactReplayed": receipt.replayed, **({"recoveredOperationId": recovered_operation_id} if recovered_operation_id is not None and recovered_operation_id != operation_id else {})}, metadata={"artifactId": receipt.artifact_id, "summaryMarkdown": payload["summaryMarkdown"]})

    def classify_failure(self, error):
        retryable = isinstance(error, ScalableSynthesisOutputError) or not isinstance(error, ScalableSynthesisExecutionError)
        category = (
            FailureCategory.MODEL_OUTPUT_INVALID
            if isinstance(error, ScalableSynthesisOutputError)
            else FailureCategory.TOOL_EXECUTION
            if retryable
            else FailureCategory.BUSINESS_INVARIANT
        )
        return FailureSignal(category=category, code=str(getattr(error, "code", "") or type(error).__name__), retryable=retryable)


def _validate_output(raw, scope, child_id):
    if not isinstance(raw, Mapping):
        raise ScalableSynthesisOutputError("Synthesis output shape is invalid")
    try:
        materials = validate_canonical_materials(raw)
    except CanonicalAnalysisMaterialError as error:
        raise ScalableSynthesisOutputError(str(error)) from error
    return {"schemaVersion": SYNTHESIS_OUTPUT_SCHEMA_VERSION, "kind": "synthesize", "childRunId": child_id, "inputArtifactIds": [item.artifact_id for item in scope.artifacts], "coveredSliceIds": list(scope.expected_slice_ids), **materials}


async def _require_model_run(db, context, run_id, *, allow_previous_root=False):
    if await is_root_model_producer(
        db, context, run_id, allow_previous_root=allow_previous_root
    ):
        return
    await _require_child(
        db,
        context.run_id,
        run_id,
        allow_previous_root=allow_previous_root,
    )


def _validate_committed(payload, scope):
    if payload.get("schemaVersion") != SYNTHESIS_OUTPUT_SCHEMA_VERSION or payload.get("kind") != "synthesize" or payload.get("inputArtifactIds") != [item.artifact_id for item in scope.artifacts] or payload.get("coveredSliceIds") != list(scope.expected_slice_ids):
        raise ScalableSynthesisExecutionError("Committed Synthesis identity conflict")
    return _validate_output({key: payload.get(key) for key in ("summaryMarkdown", "facts", "craftCards")}, scope, str(payload.get("childRunId") or ""))


async def _require_child(db, root_id, child_id, *, allow_previous_root=False):
    row = await db.fetch_one("SELECT root_run_id, parent_run_id FROM ai_agent_runs WHERE id = ?", [child_id])
    if not child_id or child_id == root_id or row is None or not ((row["root_run_id"] == root_id and row["parent_run_id"] == root_id) or (allow_previous_root and row["root_run_id"] and row["parent_run_id"] == row["root_run_id"])):
        raise ScalableSynthesisExecutionError("Synthesis requires a Child owned by this Root")


__all__ = ["PurrAScalableSynthesisChildRunner", "READ_NOVEL_ANALYSIS_SYNTHESIS_INPUTS", "ScalableSynthesisExecutionError", "ScalableSynthesisOutputError", "ScalableSynthesisUnitExecutor", "SynthesisInputScopeCompiler", "build_synthesis_input_tool_catalog"]
