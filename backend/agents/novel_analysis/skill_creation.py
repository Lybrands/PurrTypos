"""Create one editable file-backed writing Skill from reviewed analysis evidence."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass

from agents.novel_analysis.attempt_artifact import NovelAnalysisAttemptArtifactStore
from agents.novel_analysis.child_submission import (
    NovelAnalysisChildSubmissionStore,
    SUBMIT_NOVEL_ANALYSIS_CHILD_RESULT,
)
from agents.novel_analysis.creator_skill_resource import creator_skill_instructions
from agents.novel_analysis.map_execution import (
    PurrAReusableChildCoordinator,
    inherited_agent_input_payload,
    raise_child_run_failure,
)
from agents.novel_analysis.reduce_execution import _artifact_id
from agents.novel_analysis.review_execution import _require_child
from application.writing_technique_service import WritingTechniqueService
from infrastructure.persistence.sqlite_run_tree_repository import SqliteRunTreeRepository
from infrastructure.persistence.writing.technique_document_parser import file_manifest
from purra.agent_tree import AgentCapabilityGrant, ChildAgentSpec
from purra.cancellation import raise_if_stopped
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


READ_NOVEL_ANALYSIS_SKILL_INPUT = "readNovelAnalysisSkillInput"
SKILL_SCOPE_STATE_KEY = "novelAnalysisSkillScope"


class ScalableSkillCreationError(ValueError):
    code = "novel_analysis_skill_creation_invalid"


class ScalableSkillOutputError(ValueError):
    code = "novel_analysis_skill_output_invalid"


@dataclass(frozen=True, slots=True)
class SkillInputScope:
    coverage_artifact_id: str
    coverage_digest: str
    synthesis_artifact_id: str
    synthesis_digest: str
    source_revision_id: str
    card_ids: tuple[str, ...]

    def to_mapping(self) -> dict[str, object]:
        return {
            "coverageArtifactId": self.coverage_artifact_id,
            "coverageDigest": self.coverage_digest,
            "synthesisArtifactId": self.synthesis_artifact_id,
            "synthesisDigest": self.synthesis_digest,
            "sourceRevisionId": self.source_revision_id,
            "cardIds": list(self.card_ids),
        }

    @classmethod
    def from_mapping(cls, raw):
        if not isinstance(raw, Mapping):
            raise ScalableSkillCreationError("Skill input scope is invalid")
        try:
            scope = cls(
                coverage_artifact_id=str(raw["coverageArtifactId"] or "").strip(),
                coverage_digest=str(raw["coverageDigest"] or "").strip(),
                synthesis_artifact_id=str(raw["synthesisArtifactId"] or "").strip(),
                synthesis_digest=str(raw["synthesisDigest"] or "").strip(),
                source_revision_id=str(raw["sourceRevisionId"] or "").strip(),
                card_ids=tuple(str(item or "").strip() for item in raw["cardIds"]),
            )
        except (KeyError, TypeError) as error:
            raise ScalableSkillCreationError("Skill input scope is incomplete") from error
        if not all((scope.coverage_artifact_id, scope.coverage_digest, scope.synthesis_artifact_id, scope.synthesis_digest, scope.source_revision_id)) or any(not item for item in scope.card_ids):
            raise ScalableSkillCreationError("Skill input scope is incomplete")
        return scope


class SkillInputScopeCompiler:
    def __init__(self, db):
        self._db = db
        self._store = NovelAnalysisAttemptArtifactStore(db)

    async def compile(self, context) -> tuple[SkillInputScope, dict[str, object]]:
        if str(context.unit.metadata.get("unitKind") or "") != "skill":
            raise ScalableSkillCreationError("Skill compiler received another Unit kind")
        dependencies = tuple(context.unit.dependencies)
        if len(dependencies) != 1 or set(context.dependency_outputs) != set(dependencies):
            raise ScalableSkillCreationError("Skill dependency contract is invalid")
        coverage_id = _artifact_id(context.dependency_outputs[dependencies[0]])
        coverage = await self._store.load_payload(coverage_id)
        synthesis_id = str(coverage.get("synthesisArtifactId") or "").strip()
        synthesis = await self._store.load_payload(synthesis_id)
        task = await self._db.fetch_one(
            "SELECT owner_id FROM ai_agent_long_tasks WHERE id = ?", [context.task.id]
        )
        if (
            coverage.get("kind") != "coverage"
            or coverage.get("covered") is not True
            or canonical_json_digest(synthesis) != coverage.get("synthesisDigest")
            or synthesis.get("kind") != "synthesize"
            or task is None
        ):
            raise ScalableSkillCreationError("Skill inputs failed verification")
        cards = synthesis.get("craftCards")
        if not isinstance(cards, list):
            raise ScalableSkillCreationError("Skill craft inputs are invalid")
        scope = SkillInputScope(
            coverage_id,
            canonical_json_digest(coverage),
            synthesis_id,
            canonical_json_digest(synthesis),
            str(task["owner_id"]),
            tuple(str(item.get("id") or "") for item in cards if isinstance(item, Mapping)),
        )
        return scope, synthesis


def build_skill_input_tool_catalog(db, *, run_tree_repository=None):
    store = NovelAnalysisAttemptArtifactStore(db)
    tree = run_tree_repository or SqliteRunTreeRepository(db)

    async def read_input(state, arguments, signal=None):
        del arguments
        raise_if_stopped(signal)
        try:
            raw = state.domain.get(SKILL_SCOPE_STATE_KEY)
            if raw is None:
                raw = await inherited_agent_input_payload(
                    tree, str(state.run_id or ""), "skillInputScope"
                )
            scope = SkillInputScope.from_mapping(thaw_json_mapping(raw))
            coverage = await store.load_payload(scope.coverage_artifact_id)
            synthesis = await store.load_payload(scope.synthesis_artifact_id)
            if canonical_json_digest(coverage) != scope.coverage_digest or canonical_json_digest(synthesis) != scope.synthesis_digest:
                raise ScalableSkillCreationError("Skill inputs changed after binding")
            return ToolHandlerResult(content=json.dumps({
                "storyOverview": synthesis["summaryMarkdown"],
                "craftCards": synthesis["craftCards"],
            }, ensure_ascii=False, allow_nan=False))
        except Exception as error:
            failure = error if isinstance(error, ScalableSkillCreationError) else ScalableSkillCreationError("Skill inputs are unavailable")
            return ToolHandlerResult(
                content=json.dumps({"success": False, "code": failure.code, "error": str(failure)}),
                error_code=failure.code,
            )

    return InMemoryToolCatalog((ToolRegistration(
        schema=ToolSchema(
            name=READ_NOVEL_ANALYSIS_SKILL_INPUT,
            description="读取已通过覆盖门禁的写法观察和整书上下文，用于创建完整写作 Skill。",
            parameters={"type": "object", "properties": {}, "required": [], "additionalProperties": False},
            display_names={"zh-CN": "读取写作 Skill 创建资料", "en": "Read writing Skill inputs"},
        ),
        handler=read_input,
        policy=ToolPolicy(ToolExecutionMode.READ, "读取写作 Skill 创建资料", ToolRiskLevel.READ),
        concurrency_safe=True,
        data_contract=ToolDataContract(model_owned_paths=(), host_bound_paths=("coverageArtifactId", "synthesisArtifactId")),
        operation_display_params=lambda state, arguments, call: {
            "displayNames": {"zh-CN": "读取写作 Skill 创建资料", "en-US": "Read writing Skill inputs"}
        },
    ),))


class PurrAScalableSkillChildRunner:
    def __init__(self, db=None, *, model_name, submissions=None):
        self._model_name = str(model_name or "").strip()
        if not self._model_name:
            raise ValueError("Skill Child runner requires a model name")
        self._core = None
        self._children = PurrAReusableChildCoordinator()
        if submissions is None and db is None:
            raise ValueError("Skill Child runner requires a submission store")
        self._submissions = submissions or NovelAnalysisChildSubmissionStore(db)

    def bind_agent_core(self, core):
        self._core = core
        self._children.bind_agent_core(core)

    async def run(self, *, scope, context, signal=None):
        if self._core is None:
            raise RuntimeError("Skill Child runner has no active Agent Core")
        operation_id = f"{context.task.id}:{context.unit.id}:{context.unit.attempt}"
        child = ChildAgentSpec(
            name="create-writing-skill",
            title="创建完整写作 Skill",
            instruction=(
                creator_skill_instructions()
                + f"\n\n必须调用 {READ_NOVEL_ANALYSIS_SKILL_INPUT} 一次。"
                f"完成后必须调用 {SUBMIT_NOVEL_ANALYSIS_CHILD_RESULT} 提交结果；"
                "最终回复不要承载 Skill 数据。提交的 result 包含 files、evidenceRefs、scopeNotes。"
                "files 是包含 path 与 content 的完整文件集合；evidenceRefs 只填写所采用 craftCards 的 id。"
            ),
            objective="把整书写法分析创建为可编辑、可版本化、可供创作 Agent 按需读取的完整 Skill。",
            input_payload={
                "skillInputScope": scope.to_mapping(),
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
                    READ_NOVEL_ANALYSIS_SKILL_INPUT,
                    SUBMIT_NOVEL_ANALYSIS_CHILD_RESULT,
                ),
                allowed_models=(self._model_name,),
            ),
        )
        child_id, aggregation = await self._children.run(
            root_run_id=context.run_id,
            reuse_key=f"skill-unit:{context.unit.id}:attempt:{context.unit.attempt}",
            idempotency_key=f"skill-child:{operation_id}",
            child=child,
            signal=signal,
        )
        if aggregation.pending_run_ids or aggregation.required_failures:
            raise_child_run_failure(aggregation, child_id, "Skill")
        try:
            return child_id, await self._submissions.load(child_id)
        except ValueError as error:
            raise ScalableSkillOutputError(
                "Skill Child did not submit its structured result"
            ) from error


class ScalableSkillUnitExecutor:
    def __init__(self, db, *, child_runner):
        self._db = db
        self._runner = child_runner
        self._compiler = SkillInputScopeCompiler(db)
        self._store = NovelAnalysisAttemptArtifactStore(db)
        self._techniques = WritingTechniqueService(db)

    def bind_agent_core(self, core):
        self._runner.bind_agent_core(core)

    async def execute(self, context, signal=None):
        scope, synthesis = await self._compiler.compile(context)
        operation_id = f"{context.task.id}:{context.unit.id}:{context.unit.attempt}"
        recovery = await self._store.try_load_execution_payload(
            task_id=context.task.id,
            unit_id=context.unit.id,
            attempt=context.unit.attempt,
            error_code=getattr(context.unit, "error_code", None),
        )
        recovered_operation_id = None
        if recovery is not None:
            payload, recovered_operation_id = recovery
            payload = _validate_committed(payload, scope)
        elif not synthesis["craftCards"]:
            payload = _empty_payload(scope)
        else:
            child_id, raw = await self._runner.run(scope=scope, context=context, signal=signal)
            await _require_child(self._db, context.run_id, child_id)
            result = _validate_model_result(raw, scope, self._techniques)
            candidate = await self._materialize(context, scope, result["files"])
            payload = {
                "schemaVersion": 1,
                "kind": "skill",
                "childRunId": child_id,
                "coverageArtifactId": scope.coverage_artifact_id,
                "coverageDigest": scope.coverage_digest,
                "synthesisArtifactId": scope.synthesis_artifact_id,
                "synthesisDigest": scope.synthesis_digest,
                "techniqueResult": {
                    "status": "generated",
                    "candidate": candidate,
                    "evidenceRefs": result["evidenceRefs"],
                    "scopeNotes": result["scopeNotes"],
                    "reason": "",
                },
            }
        receipt = await self._store.commit(
            task_id=context.task.id,
            unit_id=context.unit.id,
            attempt=context.unit.attempt,
            operation_id=operation_id,
            run_id=str(payload.get("childRunId") or context.run_id),
            payload=payload,
        )
        return LongTaskUnitResult(
            output_ref=receipt.resource_ref,
            artifact_digest=canonical_json_digest(payload),
            validation_receipt={
                "schemaVersion": 1,
                "unitKind": "skill",
                "artifactReplayed": receipt.replayed,
                **({"recoveredOperationId": recovered_operation_id} if recovered_operation_id and recovered_operation_id != operation_id else {}),
            },
            metadata={"artifactId": receipt.artifact_id},
        )

    async def _materialize(self, context, scope, files):
        base = f"{context.task.id}:{context.unit.id}:{context.unit.attempt}"
        draft = await self._techniques.create_draft(
            operation_id=base + ":draft",
            storage_scope="analysis_candidate",
            owner={"sourceRevisionId": scope.source_revision_id, "taskId": context.task.id},
        )
        draft = await self._techniques.apply_changes(
            draft["techniqueId"],
            draft["draftId"],
            expected_revision=draft["draftRevision"],
            operation_id=base + ":files",
            changes=[{"action": "put", "path": path, "content": content} for path, content in files.items()],
        )
        sealed = await self._techniques.seal(
            "technique",
            draft["techniqueId"],
            draft["draftId"],
            expected_revision=draft["draftRevision"],
            expected_tree_digest=draft["treeDigest"],
            operation_id=base + ":seal",
        )
        return {
            "techniqueId": draft["techniqueId"],
            "draftId": draft["draftId"],
            "versionId": sealed["sealedRef"]["versionId"],
        }

    def classify_failure(self, error):
        retryable = isinstance(error, ScalableSkillOutputError) or not isinstance(error, ScalableSkillCreationError)
        category = (
            FailureCategory.MODEL_OUTPUT_INVALID
            if isinstance(error, ScalableSkillOutputError)
            else FailureCategory.TOOL_EXECUTION
            if retryable
            else FailureCategory.BUSINESS_INVARIANT
        )
        return FailureSignal(
            category=category,
            code=str(getattr(error, "code", "") or type(error).__name__)[:240],
            retryable=retryable,
        )


def _validate_model_result(raw, scope, service):
    if not isinstance(raw, Mapping):
        raise ScalableSkillOutputError("Skill output is incomplete")
    raw_files = raw.get("files")
    if not isinstance(raw_files, list) or not raw_files:
        raise ScalableSkillOutputError("Skill files are empty")
    files: dict[str, str] = {}
    for item in raw_files:
        if not isinstance(item, Mapping):
            raise ScalableSkillOutputError("Skill file is invalid")
        path, content = item.get("path"), item.get("content")
        if not isinstance(path, str) or not isinstance(content, str) or path in files:
            raise ScalableSkillOutputError("Skill file is invalid")
        files[path] = content
    try:
        file_manifest(files, limits=service.techniques.limits)
    except Exception as error:
        raise ScalableSkillOutputError(str(error)) from error
    refs = raw.get("evidenceRefs")
    notes = raw.get("scopeNotes", [])
    if not isinstance(refs, list) or not refs or any(str(item) not in scope.card_ids for item in refs):
        raise ScalableSkillOutputError("Skill evidence references are invalid")
    if not isinstance(notes, list) or any(not isinstance(item, str) for item in notes):
        raise ScalableSkillOutputError("Skill scope notes are invalid")
    return {"files": files, "evidenceRefs": list(dict.fromkeys(map(str, refs))), "scopeNotes": notes}


def _empty_payload(scope):
    return {
        "schemaVersion": 1,
        "kind": "skill",
        "childRunId": "",
        "coverageArtifactId": scope.coverage_artifact_id,
        "coverageDigest": scope.coverage_digest,
        "synthesisArtifactId": scope.synthesis_artifact_id,
        "synthesisDigest": scope.synthesis_digest,
        "techniqueResult": {
            "status": "insufficient_material",
            "candidate": None,
            "evidenceRefs": [],
            "scopeNotes": [],
            "reason": "当前分析没有形成可用于创建写作 Skill 的写法观察",
        },
    }


def _validate_committed(payload, scope):
    if (
        payload.get("kind") != "skill"
        or payload.get("coverageArtifactId") != scope.coverage_artifact_id
        or payload.get("coverageDigest") != scope.coverage_digest
        or payload.get("synthesisArtifactId") != scope.synthesis_artifact_id
        or payload.get("synthesisDigest") != scope.synthesis_digest
    ):
        raise ScalableSkillCreationError("Committed Skill identity conflict")
    return dict(payload)


__all__ = [
    "PurrAScalableSkillChildRunner",
    "READ_NOVEL_ANALYSIS_SKILL_INPUT",
    "ScalableSkillCreationError",
    "ScalableSkillOutputError",
    "ScalableSkillUnitExecutor",
    "SkillInputScopeCompiler",
    "build_skill_input_tool_catalog",
]
