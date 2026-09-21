"""Durable structured-result handoff for scalable Novel Analysis Children."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

from agents.novel_analysis.canonical_materials import validate_canonical_materials
from infrastructure.persistence.sqlite_artifact_claim_repository import (
    SqliteArtifactClaimRepository,
)
from infrastructure.persistence.sqlite_artifact_repository import (
    SqliteArtifactRepository,
)
from purra.artifacts import (
    ArtifactAppendCommand,
    ArtifactCreateCommand,
    ArtifactFinalizeCommand,
    ArtifactLifecycle,
    ArtifactMutationLease,
    ArtifactOwnerRef,
    ArtifactStatus,
    ArtifactWriteClaimCommand,
)
from purra.cancellation import raise_if_stopped
from purra.contracts import (
    ToolDataContract,
    ToolEffectState,
    ToolExecutionMode,
    ToolHandlerResult,
    ToolPolicy,
    ToolRiskLevel,
    ToolSchema,
)
from purra.json_values import canonical_json_digest, thaw_json_mapping
from purra.ports import ToolRegistration


SUBMIT_NOVEL_ANALYSIS_CHILD_RESULT = "submitNovelAnalysisChildResult"
_NAMESPACE = "purrtypos.novel_analysis.v1"
_KIND = "child_structured_result"


class NovelAnalysisChildSubmissionStore:
    """Store exactly one model-owned JSON object for each Child Run."""

    def __init__(self, db, *, lifecycle=None, claims=None) -> None:
        self._repository = SqliteArtifactRepository(db)
        self._claims = claims or SqliteArtifactClaimRepository(db)
        self._lifecycle = lifecycle or ArtifactLifecycle(self._repository)

    async def commit(self, *, child_run_id: str, payload: Mapping[str, object]):
        run_id = str(child_run_id or "").strip()
        if not run_id or not isinstance(payload, Mapping):
            raise ValueError("Child result requires a bound Run and JSON object")
        value = dict(payload)
        owner_ref = ArtifactOwnerRef("agent_run", run_id)
        artifact = await self._repository.find_for_owner(
            namespace=_NAMESPACE,
            kind=_KIND,
            owner_id=run_id,
            owner_ref=owner_ref,
        )
        digest = canonical_json_digest(value)
        if artifact is None:
            artifact = await self._repository.create(
                "analysis_child_" + hashlib.sha256(run_id.encode()).hexdigest()[:32],
                ArtifactCreateCommand(
                    namespace=_NAMESPACE,
                    kind=_KIND,
                    owner_id=run_id,
                    owner_ref=owner_ref,
                    created_by_run_id=run_id,
                    schema_version=1,
                    expected_item_count=1,
                    metadata={"childRunId": run_id, "payloadDigest": digest},
                ),
            )
        if artifact.metadata.get("payloadDigest") != digest:
            raise ValueError("Child Run already submitted a different result")
        if artifact.status is ArtifactStatus.FINALIZED:
            await self._require_same_payload(artifact.id, value)
            return artifact.resource_ref, True
        claim = await self._claims.acquire(ArtifactWriteClaimCommand(
            artifact_id=artifact.id,
            run_id=run_id,
            expected_revision=artifact.revision,
            lease_duration_ms=300_000,
        ))
        lease = ArtifactMutationLease(run_id=run_id, claim_token=claim.claim_token)
        replayed = artifact.committed_item_count == 1
        if artifact.committed_item_count == 0:
            appended = await self._lifecycle.append(ArtifactAppendCommand(
                artifact_id=artifact.id,
                expected_revision=artifact.revision,
                sequence=1,
                batch_id="result",
                idempotency_key=f"child-result:{run_id}",
                items=(value,),
                coverage_keys=(run_id,),
                write_lease=lease,
            ))
            if appended.accepted_count != 1:
                raise RuntimeError("Child result Artifact append was incomplete")
            artifact = await self._lifecycle.get(artifact.id)
        else:
            await self._require_same_payload(artifact.id, value)
        finalized = await self._lifecycle.finalize(ArtifactFinalizeCommand(
            artifact_id=artifact.id,
            expected_revision=artifact.revision,
            expected_item_count=1,
            expected_coverage_keys=(run_id,),
            resource_ref=f"novel-analysis-child://{artifact.id}",
            write_lease=lease,
        ))
        return finalized.resource_ref, replayed

    async def load(self, child_run_id: str) -> dict[str, object]:
        run_id = str(child_run_id or "").strip()
        artifact = await self._repository.find_for_owner(
            namespace=_NAMESPACE,
            kind=_KIND,
            owner_id=run_id,
            owner_ref=ArtifactOwnerRef("agent_run", run_id),
        )
        if artifact is None or artifact.status is not ArtifactStatus.FINALIZED:
            raise ValueError("Child Agent did not submit its structured result")
        batches = await self._repository.list_batches(artifact.id)
        if len(batches) != 1 or len(batches[0].items) != 1:
            raise ValueError("Child result Artifact payload is invalid")
        value = thaw_json_mapping(batches[0].items[0])
        if canonical_json_digest(value) != artifact.metadata.get("payloadDigest"):
            raise ValueError("Child result Artifact digest is invalid")
        return value

    async def _require_same_payload(self, artifact_id, payload) -> None:
        batches = await self._repository.list_batches(artifact_id)
        if (
            len(batches) != 1
            or len(batches[0].items) != 1
            or canonical_json_digest(thaw_json_mapping(batches[0].items[0]))
            != canonical_json_digest(payload)
        ):
            raise ValueError("Child result Artifact payload conflict")


def build_novel_analysis_child_submission_registration(db) -> ToolRegistration:
    submissions = NovelAnalysisChildSubmissionStore(db)

    async def submit(state, arguments, signal=None):
        raise_if_stopped(signal)
        try:
            child_run_id = str(state.run_id or "").strip()
            result = arguments.get("result")
            if not child_run_id or not isinstance(result, Mapping):
                raise ValueError("Structured result must be a JSON object")
            result = _normalize_stage_result(result)
            resource_ref, replayed = await submissions.commit(
                child_run_id=child_run_id,
                payload=result,
            )
            raise_if_stopped(signal)
            return ToolHandlerResult(
                content=json.dumps({
                    "artifactRef": resource_ref,
                    "replayed": replayed,
                }, ensure_ascii=False, allow_nan=False),
                effect_state=ToolEffectState.COMMITTED,
            )
        except (TypeError, ValueError) as error:
            return ToolHandlerResult(
                content=json.dumps({
                    "success": False,
                    "code": "tool_input_invalid",
                    "error": str(error),
                }, ensure_ascii=False),
                error_code="tool_input_invalid",
                effect_state=ToolEffectState.NOT_STARTED,
            )

    return ToolRegistration(
        schema=ToolSchema(
            name=SUBMIT_NOVEL_ANALYSIS_CHILD_RESULT,
            description=(
                "提交当前子 Agent 为本轮分析生成的完整结构化结果。"
                "结果身份由当前子 Agent Run 绑定；不要把结果写在最终回复中。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "result": {
                        "type": "object",
                        "description": "当前分析步骤生成的完整 JSON 对象。",
                    },
                },
                "required": ["result"],
                "additionalProperties": False,
            },
            display_names={
                "zh-CN": "提交当前分析结果",
                "en": "Submit analysis result",
            },
        ),
        handler=submit,
        policy=ToolPolicy(
            ToolExecutionMode.PROPOSE,
            "提交当前分析结果",
            ToolRiskLevel.WRITE,
        ),
        concurrency_safe=False,
        host_managed_durability=True,
        cancellation_linearizable=True,
        max_argument_chars=300_000,
        data_contract=ToolDataContract(
            model_owned_paths=("result",),
            host_bound_paths=("runId",),
        ),
        operation_display_params=lambda state, arguments, call: {
            "displayNames": {
                "zh-CN": "提交当前分析结果",
                "en": "Submit analysis result",
            },
        },
    )


def _normalize_stage_result(result: Mapping[str, object]) -> dict[str, object]:
    value = dict(result)
    canonical_keys = {"summaryMarkdown", "facts", "craftCards"}
    if canonical_keys.intersection(value):
        return validate_canonical_materials(value)
    return value


__all__ = [
    "NovelAnalysisChildSubmissionStore",
    "SUBMIT_NOVEL_ANALYSIS_CHILD_RESULT",
    "build_novel_analysis_child_submission_registration",
]
