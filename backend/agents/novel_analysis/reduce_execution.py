"""Host-bounded input contract for hierarchical novel-analysis Reduce Units."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from agents.novel_analysis.attempt_artifact import NovelAnalysisAttemptArtifactStore
from agents.novel_analysis.child_submission import (
    NovelAnalysisChildSubmissionStore,
    SUBMIT_NOVEL_ANALYSIS_CHILD_RESULT,
)
from agents.novel_analysis.map_execution import (
    PurrAReusableChildCoordinator,
    inherited_agent_input_payload,
    raise_child_run_failure,
)
from infrastructure.persistence.sqlite_run_tree_repository import SqliteRunTreeRepository
from purra.agent_tree import AgentCapabilityGrant, ChildAgentSpec
from purra.cancellation import raise_if_stopped
from purra.context_budget import estimate_json_tokens
from purra.contracts import (
    ToolDataContract,
    ToolExecutionMode,
    ToolHandlerResult,
    ToolPolicy,
    ToolRiskLevel,
    ToolSchema,
)
from purra.json_values import canonical_json_digest, thaw_json_mapping
from purra.long_tasks import LongTaskUnitResult
from purra.ports import ToolRegistration
from purra.recovery import FailureCategory, FailureSignal
from purra.tools import InMemoryToolCatalog


REDUCE_CONTEXT_SHARE_NUMERATOR = 2
REDUCE_CONTEXT_SHARE_DENOMINATOR = 5
REDUCE_PACKING_SAFETY_NUMERATOR = 9
REDUCE_PACKING_SAFETY_DENOMINATOR = 10
READ_NOVEL_ANALYSIS_REDUCE_INPUTS = "readNovelAnalysisReduceInputs"
SCALABLE_REDUCE_SCOPE_STATE_KEY = "novelAnalysisReduceInputScope"
SCALABLE_REDUCE_OUTPUT_SCHEMA_VERSION = 1


class ScalableReduceExecutionError(ValueError):
    code = "novel_analysis_reduce_execution_invalid"


class ScalableReduceOutputError(ValueError):
    code = "novel_analysis_reduce_output_invalid"


@dataclass(frozen=True, slots=True)
class ReduceArtifactLocator:
    unit_id: str
    artifact_id: str
    payload_digest: str
    covered_slice_ids: tuple[str, ...]

    def to_mapping(self) -> dict[str, object]:
        return {
            "unitId": self.unit_id,
            "artifactId": self.artifact_id,
            "payloadDigest": self.payload_digest,
            "coveredSliceIds": list(self.covered_slice_ids),
        }


@dataclass(frozen=True, slots=True)
class ReduceInputScope:
    pass_id: str
    reduce_level: int
    input_token_count: int
    input_token_limit: int
    covered_slice_ids: tuple[str, ...]
    artifacts: tuple[ReduceArtifactLocator, ...]

    def to_mapping(self) -> dict[str, object]:
        return {
            "passId": self.pass_id,
            "reduceLevel": self.reduce_level,
            "inputTokenCount": self.input_token_count,
            "inputTokenLimit": self.input_token_limit,
            "coveredSliceIds": list(self.covered_slice_ids),
            "artifacts": [item.to_mapping() for item in self.artifacts],
        }

    @classmethod
    def from_mapping(cls, value: object) -> "ReduceInputScope":
        if not isinstance(value, Mapping):
            raise ScalableReduceExecutionError("Reduce input scope is unavailable")
        raw_artifacts = value.get("artifacts")
        raw_covered = value.get("coveredSliceIds")
        if not isinstance(raw_artifacts, list) or not isinstance(raw_covered, list):
            raise ScalableReduceExecutionError("Reduce input scope shape is invalid")
        try:
            artifacts = tuple(ReduceArtifactLocator(
                unit_id=str(item["unitId"]),
                artifact_id=str(item["artifactId"]),
                payload_digest=str(item["payloadDigest"]),
                covered_slice_ids=tuple(item["coveredSliceIds"]),
            ) for item in raw_artifacts if isinstance(item, Mapping))
            scope = cls(
                pass_id=str(value["passId"]),
                reduce_level=int(value["reduceLevel"]),
                input_token_count=int(value["inputTokenCount"]),
                input_token_limit=int(value["inputTokenLimit"]),
                covered_slice_ids=tuple(raw_covered),
                artifacts=artifacts,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ScalableReduceExecutionError("Reduce input scope shape is invalid") from error
        if (
            not scope.pass_id
            or scope.reduce_level < 0
            or scope.input_token_count < 0
            or scope.input_token_count > scope.input_token_limit
            or len(scope.artifacts) != len(raw_artifacts)
            or len(scope.artifacts) < 2
            or not scope.covered_slice_ids
            or tuple(item for artifact in scope.artifacts for item in artifact.covered_slice_ids)
            != scope.covered_slice_ids
        ):
            raise ScalableReduceExecutionError("Reduce input scope contract is invalid")
        return scope


class ReduceInputScopeCompiler:
    """Validate dependency Artifacts and expose locators, never their bodies."""

    def __init__(self, db) -> None:
        self._artifacts = NovelAnalysisAttemptArtifactStore(db)

    async def compile(self, context) -> ReduceInputScope:
        metadata = context.unit.metadata
        if str(metadata.get("unitKind") or "") != "reduce":
            raise ScalableReduceExecutionError("Reduce compiler received another Unit kind")
        pass_id = str(metadata.get("passId") or "").strip()
        reduce_level = metadata.get("reduceLevel")
        if not pass_id or type(reduce_level) is not int or reduce_level < 0:
            raise ScalableReduceExecutionError("Reduce Unit semantic contract is invalid")
        dependencies = tuple(context.unit.dependencies)
        if (
            len(dependencies) < 2
            or set(context.dependency_outputs) != set(dependencies)
            or int(metadata.get("fanIn") or 0) != len(dependencies)
        ):
            raise ScalableReduceExecutionError("Reduce dependency contract is invalid")

        locators = []
        payloads = []
        covered = []
        for dependency_id in dependencies:
            artifact_id = _artifact_id(context.dependency_outputs[dependency_id])
            payload = await self._artifacts.load_payload(artifact_id)
            slices = _validate_dependency_payload(payload, pass_id=pass_id)
            overlap = set(covered).intersection(slices)
            if overlap:
                raise ScalableReduceExecutionError(
                    "Reduce dependencies contain overlapping slice lineage"
                )
            covered.extend(slices)
            payloads.append(payload)
            locators.append(ReduceArtifactLocator(
                unit_id=dependency_id,
                artifact_id=artifact_id,
                payload_digest=canonical_json_digest(payload),
                covered_slice_ids=slices,
            ))

        manifest = context.task.metadata.get("sliceManifest")
        if not isinstance(manifest, Mapping):
            raise ScalableReduceExecutionError("Reduce SliceManifest is unavailable")
        context_window = manifest.get("contextWindowTokens")
        if type(context_window) is not int or context_window < 1:
            raise ScalableReduceExecutionError("Reduce context window is invalid")
        hard_limit = (
            context_window * REDUCE_CONTEXT_SHARE_NUMERATOR
            // REDUCE_CONTEXT_SHARE_DENOMINATOR
        )
        input_limit = (
            hard_limit * REDUCE_PACKING_SAFETY_NUMERATOR
            // REDUCE_PACKING_SAFETY_DENOMINATOR
        )
        input_tokens = estimate_json_tokens(payloads)
        if input_tokens > input_limit:
            raise ScalableReduceExecutionError("Reduce dependency payload exceeds its input budget")
        return ReduceInputScope(
            pass_id=pass_id,
            reduce_level=reduce_level,
            input_token_count=input_tokens,
            input_token_limit=input_limit,
            covered_slice_ids=tuple(covered),
            artifacts=tuple(locators),
        )


def build_scalable_reduce_input_tool_catalog(
    db, *, run_tree_repository=None
) -> InMemoryToolCatalog:
    artifacts = NovelAnalysisAttemptArtifactStore(db)
    tree = run_tree_repository or SqliteRunTreeRepository(db)

    async def read_inputs(state, arguments, signal=None):
        del arguments
        raise_if_stopped(signal)
        try:
            scope = await _reduce_scope_for_tool_state(state, tree)
            inputs = []
            actual_tokens = 0
            for locator in scope.artifacts:
                payload = await artifacts.load_payload(locator.artifact_id)
                if (
                    canonical_json_digest(payload) != locator.payload_digest
                    or _validate_dependency_payload(payload, pass_id=scope.pass_id)
                    != locator.covered_slice_ids
                ):
                    raise ScalableReduceExecutionError("Reduce input Artifact changed after binding")
                inputs.append({"unitId": locator.unit_id, "payload": payload})
                actual_tokens += estimate_json_tokens(payload)
            if actual_tokens > scope.input_token_limit:
                raise ScalableReduceExecutionError("Reduce Tool result exceeds its input budget")
            return ToolHandlerResult(content=json.dumps({
                "passId": scope.pass_id,
                "reduceLevel": scope.reduce_level,
                "coveredSliceIds": list(scope.covered_slice_ids),
                "inputs": inputs,
            }, ensure_ascii=False, allow_nan=False))
        except ScalableReduceExecutionError as error:
            return ToolHandlerResult(
                content=json.dumps({"success": False, "code": error.code, "error": str(error)}),
                error_code=error.code,
            )

    return InMemoryToolCatalog((ToolRegistration(
        schema=ToolSchema(
            name=READ_NOVEL_ANALYSIS_REDUCE_INPUTS,
            description="读取 Host 为当前 Reduce Child 绑定的上游分析 Artifact。不能选择或追加输入。",
            parameters={"type": "object", "properties": {}, "required": [], "additionalProperties": False},
            display_names={"zh-CN": "读取待归并分析", "en": "Read reduce inputs"},
        ),
        handler=read_inputs,
        policy=ToolPolicy(ToolExecutionMode.READ, "读取待归并分析", ToolRiskLevel.READ),
        concurrency_safe=True,
        data_contract=ToolDataContract(
            model_owned_paths=(),
            host_bound_paths=("passId", "artifacts", "coveredSliceIds"),
        ),
        operation_display_params=lambda state, arguments, call: {
            "displayNames": {
                "zh-CN": "读取待归并分析",
                "en-US": "Read reduce inputs",
            },
        },
    ),))


@dataclass(frozen=True, slots=True)
class ReduceChildRunResult:
    child_run_id: str
    payload: Mapping[str, object]


class ScalableReduceChildRunner(Protocol):
    async def run(self, *, scope, dimensions, context, signal=None) -> ReduceChildRunResult: ...


class PurrAScalableReduceChildRunner:
    def __init__(self, db=None, *, model_name: str, submissions=None) -> None:
        self._model_name = str(model_name or "").strip()
        if not self._model_name:
            raise ValueError("Reduce Child runner requires a model name")
        self._core = None
        self._children = PurrAReusableChildCoordinator()
        if submissions is None and db is None:
            raise ValueError("Reduce Child runner requires a submission store")
        self._submissions = submissions or NovelAnalysisChildSubmissionStore(db)

    def bind_agent_core(self, core) -> None:
        if self._core is not None and self._core is not core:
            raise RuntimeError("Reduce Child runner is already bound")
        self._core = core
        self._children.bind_agent_core(core)

    async def run(self, *, scope, dimensions, context, signal=None):
        if self._core is None:
            raise RuntimeError("Reduce Child runner has no active Agent Core")
        operation_id = f"{context.task.id}:{context.unit.id}:{context.unit.attempt}"
        child = ChildAgentSpec(
                name=f"reduce-{context.unit.id}"[:64],
                title="归并小说分析",
                instruction=(
                    f"必须调用 {READ_NOVEL_ANALYSIS_REDUCE_INPUTS} 一次读取绑定结果。"
                    "合并同一对象，保留无法消解的冲突，不读取或复述小说原文。"
                    f"完成后必须调用 {SUBMIT_NOVEL_ANALYSIS_CHILD_RESULT} 提交结果；"
                    "最终回复不要承载分析数据。提交的 result 只包含 findings、"
                    "conflicts，以及条目中的 dimension、subject、analysis/description："
                    '{"findings":[{"dimension":"允许维度","subject":"对象",'
                    '"analysis":"整合分析"}],"conflicts":[{"dimension":"允许维度",'
                    '"subject":"对象","description":"冲突说明"}]}。'
                ),
                objective="归并当前层分析；允许维度：" + ", ".join(dimensions),
                input_payload={
                    "reduceInputScope": scope.to_mapping(),
                    "unitId": context.unit.id,
                    "attempt": context.unit.attempt,
                },
                capability_grant=AgentCapabilityGrant(
                    can_spawn_agents=False,
                    max_depth=1,
                    max_children_per_call=1,
                    max_agents_per_root=16,
                    max_parallel_runs=1,
                    allowed_tools=(
                        READ_NOVEL_ANALYSIS_REDUCE_INPUTS,
                        SUBMIT_NOVEL_ANALYSIS_CHILD_RESULT,
                    ),
                    allowed_models=(self._model_name,),
                ),
            )
        child_run_id, aggregation = await self._children.run(
            root_run_id=context.run_id,
            reuse_key=f"reduce-unit:{context.unit.id}:attempt:{context.unit.attempt}",
            idempotency_key=f"reduce-child:{operation_id}",
            child=child,
            signal=signal,
        )
        if aggregation.pending_run_ids or aggregation.required_failures:
            raise_child_run_failure(aggregation, child_run_id, "Reduce")
        try:
            payload = await self._submissions.load(child_run_id)
        except ValueError as error:
            raise ScalableReduceOutputError(
                "Reduce Child did not submit its structured result"
            ) from error
        return ReduceChildRunResult(child_run_id, payload)


class ScalableReduceUnitExecutor:
    def __init__(self, db, *, child_runner: ScalableReduceChildRunner) -> None:
        self._db = db
        self._runner = child_runner
        self._compiler = ReduceInputScopeCompiler(db)
        self._artifacts = NovelAnalysisAttemptArtifactStore(db)

    def bind_agent_core(self, core) -> None:
        self._runner.bind_agent_core(core)

    async def execute(self, context, signal=None) -> LongTaskUnitResult:
        raise_if_stopped(signal)
        scope = await self._compiler.compile(context)
        dimensions = tuple(context.unit.metadata.get("dimensions") or ())
        if not dimensions or any(not isinstance(item, str) or not item for item in dimensions):
            raise ScalableReduceExecutionError("Reduce dimensions are invalid")
        operation_id = f"{context.task.id}:{context.unit.id}:{context.unit.attempt}"
        recovery = await self._artifacts.try_load_execution_payload(
            task_id=context.task.id,
            unit_id=context.unit.id,
            attempt=context.unit.attempt,
            error_code=getattr(context.unit, "error_code", None),
        )
        recovered_operation_id = None
        if recovery is None:
            result = await self._runner.run(
                scope=scope, dimensions=dimensions, context=context, signal=signal
            )
            child_run_id = result.child_run_id
            await _require_child(self._db, context.run_id, child_run_id)
            payload = _validate_reduce_output(
                result.payload, scope=scope, dimensions=dimensions, child_run_id=child_run_id
            )
        else:
            recovered, recovered_operation_id = recovery
            child_run_id = str(recovered.get("childRunId") or "")
            await _require_child(
                self._db,
                context.run_id,
                child_run_id,
                allow_previous_root=recovered_operation_id != operation_id,
            )
            payload = _validate_committed_reduce_output(recovered, scope=scope, dimensions=dimensions)
        receipt = await self._artifacts.commit(
            task_id=context.task.id,
            unit_id=context.unit.id,
            attempt=context.unit.attempt,
            operation_id=operation_id,
            run_id=child_run_id,
            payload=dict(payload),
        )
        return LongTaskUnitResult(
            output_ref=receipt.resource_ref,
            artifact_digest=canonical_json_digest(payload),
            validation_receipt={
                "schemaVersion": SCALABLE_REDUCE_OUTPUT_SCHEMA_VERSION,
                "unitKind": "reduce",
                "operationId": operation_id,
                "childRunId": child_run_id,
                "coveredSliceIds": list(scope.covered_slice_ids),
                "artifactReplayed": receipt.replayed,
                **(
                    {"recoveredOperationId": recovered_operation_id}
                    if recovered_operation_id is not None
                    and recovered_operation_id != operation_id
                    else {}
                ),
            },
            metadata={"artifactId": receipt.artifact_id, "childRunId": child_run_id},
        )

    def classify_failure(self, error):
        retryable = isinstance(error, ScalableReduceOutputError) or not isinstance(error, ScalableReduceExecutionError)
        category = (
            FailureCategory.MODEL_OUTPUT_INVALID
            if isinstance(error, ScalableReduceOutputError)
            else FailureCategory.TOOL_EXECUTION
            if retryable
            else FailureCategory.BUSINESS_INVARIANT
        )
        return FailureSignal(
            category=category,
            code=str(getattr(error, "code", "") or type(error).__name__)[:240],
            retryable=retryable,
        )


def _artifact_id(resource_ref: str) -> str:
    prefix = "novel-analysis://"
    if not isinstance(resource_ref, str) or not resource_ref.startswith(prefix):
        raise ScalableReduceExecutionError("Reduce dependency output ref is invalid")
    artifact_id = resource_ref[len(prefix):].strip()
    if not artifact_id:
        raise ScalableReduceExecutionError("Reduce dependency Artifact id is empty")
    return artifact_id


def _validate_dependency_payload(
    payload: Mapping[str, object], *, pass_id: str
) -> tuple[str, ...]:
    if payload.get("passId") != pass_id:
        raise ScalableReduceExecutionError("Reduce dependency belongs to another pass")
    kind = payload.get("kind")
    if kind == "map":
        slice_id = str(payload.get("sliceId") or "").strip()
        slices = (slice_id,) if slice_id else ()
    elif kind == "reduce":
        raw = payload.get("coveredSliceIds")
        slices = tuple(raw) if isinstance(raw, list) else ()
    else:
        slices = ()
    if (
        not slices
        or any(not isinstance(item, str) or not item.strip() for item in slices)
        or len(set(slices)) != len(slices)
    ):
        raise ScalableReduceExecutionError("Reduce dependency lineage is invalid")
    return slices


async def _reduce_scope_for_tool_state(state, tree) -> ReduceInputScope:
    raw = state.domain.get(SCALABLE_REDUCE_SCOPE_STATE_KEY)
    if raw is None:
        run_id = str(state.run_id or "").strip()
        if not run_id:
            raise ScalableReduceExecutionError("Reduce Tool requires a bound Child Run")
        try:
            raw = await inherited_agent_input_payload(
                tree, run_id, "reduceInputScope"
            )
        except Exception as error:
            raise ScalableReduceExecutionError("Reduce Child Run is unavailable") from error
    return ReduceInputScope.from_mapping(thaw_json_mapping(raw))


def _normalize_items(raw, *, dimensions, label):
    if not isinstance(raw, list):
        raise ScalableReduceOutputError(f"Reduce {label} must be a list")
    result = []
    expected = {"dimension", "subject", "analysis" if label == "findings" else "description"}
    text_key = "analysis" if label == "findings" else "description"
    for item in raw:
        if not isinstance(item, Mapping) or not expected.issubset(item):
            raise ScalableReduceOutputError(f"Reduce {label} item shape is invalid")
        dimension = str(item.get("dimension") or "")
        subject = str(item.get("subject") or "").strip()
        text = str(item.get(text_key) or "").strip()
        if dimension not in dimensions or not subject or not text:
            raise ScalableReduceOutputError(f"Reduce {label} is outside its contract")
        result.append({"dimension": dimension, "subject": subject, text_key: text})
    return result


def _validate_reduce_output(payload, *, scope, dimensions, child_run_id):
    if not isinstance(payload, Mapping) or not {"findings", "conflicts"}.issubset(payload):
        raise ScalableReduceOutputError("Reduce output must contain findings and conflicts")
    return {
        "schemaVersion": SCALABLE_REDUCE_OUTPUT_SCHEMA_VERSION,
        "kind": "reduce",
        "passId": scope.pass_id,
        "reduceLevel": scope.reduce_level,
        "childRunId": child_run_id,
        "inputArtifactIds": [item.artifact_id for item in scope.artifacts],
        "coveredSliceIds": list(scope.covered_slice_ids),
        "findings": _normalize_items(payload.get("findings"), dimensions=dimensions, label="findings"),
        "conflicts": _normalize_items(payload.get("conflicts"), dimensions=dimensions, label="conflicts"),
    }


def _validate_committed_reduce_output(payload, *, scope, dimensions):
    expected = {
        "schemaVersion", "kind", "passId", "reduceLevel", "childRunId",
        "inputArtifactIds", "coveredSliceIds", "findings", "conflicts",
    }
    if not isinstance(payload, Mapping) or set(payload) != expected:
        raise ScalableReduceExecutionError("Committed Reduce Artifact shape is invalid")
    if (
        payload.get("schemaVersion") != SCALABLE_REDUCE_OUTPUT_SCHEMA_VERSION
        or payload.get("kind") != "reduce"
        or payload.get("passId") != scope.pass_id
        or payload.get("reduceLevel") != scope.reduce_level
        or payload.get("inputArtifactIds") != [item.artifact_id for item in scope.artifacts]
        or payload.get("coveredSliceIds") != list(scope.covered_slice_ids)
    ):
        raise ScalableReduceExecutionError("Committed Reduce Artifact identity conflict")
    return _validate_reduce_output(
        {"findings": payload.get("findings"), "conflicts": payload.get("conflicts")},
        scope=scope,
        dimensions=dimensions,
        child_run_id=str(payload.get("childRunId") or ""),
    )


async def _require_child(
    db, root_run_id, child_run_id, *, allow_previous_root=False
):
    if not child_run_id or child_run_id == root_run_id:
        raise ScalableReduceExecutionError("Reduce model execution requires a Child Run")
    row = await db.fetch_one(
        "SELECT root_run_id, parent_run_id FROM ai_agent_runs WHERE id = ?", [child_run_id]
    )
    if row is None or not (
        (
            row["root_run_id"] == root_run_id
            and row["parent_run_id"] == root_run_id
        )
        or (
            allow_previous_root
            and row["root_run_id"]
            and row["parent_run_id"] == row["root_run_id"]
        )
    ):
        raise ScalableReduceExecutionError("Reduce Child Run belongs to another Root")


__all__ = [
    "PurrAScalableReduceChildRunner",
    "READ_NOVEL_ANALYSIS_REDUCE_INPUTS",
    "ReduceArtifactLocator",
    "ReduceChildRunResult",
    "ReduceInputScope",
    "ReduceInputScopeCompiler",
    "ScalableReduceExecutionError",
    "ScalableReduceOutputError",
    "ScalableReduceUnitExecutor",
    "build_scalable_reduce_input_tool_catalog",
]
