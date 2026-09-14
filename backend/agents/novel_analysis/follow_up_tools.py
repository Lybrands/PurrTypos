"""Read tools owned by replacement Novel Analysis follow-up Runs."""

from __future__ import annotations

import json

from agents.novel_analysis.attempt_artifact import (
    NOVEL_ANALYSIS_ATTEMPT_ARTIFACT_NAMESPACE,
)
from agents.novel_analysis.publication_service import (
    NovelAnalysisReplacementPublicationService,
)
from agents.novel_analysis.review_artifact import (
    NOVEL_ANALYSIS_REVIEWED_ARTIFACT_NAMESPACE,
)
from agents.novel_analysis.review_projection import NovelAnalysisReviewProjection
from infrastructure.persistence.sqlite_artifact_repository import (
    SqliteArtifactRepository,
)
from purra.cancellation import raise_if_stopped
from purra.contracts import (
    ToolDataContract,
    ToolExecutionMode,
    ToolHandlerResult,
    ToolPolicy,
    ToolRiskLevel,
    ToolSchema,
)
from purra.ports import ToolRegistration
from purra.tools import InMemoryToolCatalog


READ_ANALYSIS_REVIEW_ARTIFACT = "readAnalysisReviewArtifact"


def build_novel_analysis_follow_up_tool_catalog(db) -> InMemoryToolCatalog:
    artifacts = SqliteArtifactRepository(db)
    pending = NovelAnalysisReviewProjection(db)
    reviewed = NovelAnalysisReplacementPublicationService(db)

    async def read_review(state, arguments, signal=None):
        del arguments
        raise_if_stopped(signal)
        artifact_id = str(state.domain.get("analysisArtifactId") or "").strip()
        revision_id = str(state.domain.get("sourceRevisionId") or "").strip()
        try:
            artifact = await artifacts.load(artifact_id)
            if artifact is None:
                raise ValueError("analysis review Artifact is unavailable")
            if artifact.namespace == NOVEL_ANALYSIS_ATTEMPT_ARTIFACT_NAMESPACE:
                payload = await pending.load(artifact_id)
            elif artifact.namespace == NOVEL_ANALYSIS_REVIEWED_ARTIFACT_NAMESPACE:
                payload = await reviewed.load_reviewed(artifact_id)
            else:
                raise ValueError("analysis review Artifact belongs to another contract")
            if payload.get("sourceRevisionId") != revision_id:
                raise ValueError("analysis review Artifact belongs to another revision")
            raise_if_stopped(signal)
            return ToolHandlerResult(content=json.dumps(
                payload,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ))
        except ValueError as error:
            return ToolHandlerResult(
                content=json.dumps({
                    "success": False,
                    "code": "invalid_reference",
                    "error": str(error),
                }, ensure_ascii=False),
                error_code="invalid_reference",
            )

    return InMemoryToolCatalog((ToolRegistration(
        schema=ToolSchema(
            name=READ_ANALYSIS_REVIEW_ARTIFACT,
            description=(
                "读取当前追问绑定的已审核或待审核小说分析结果。"
                "需要核对已有结论时先调用；不得猜测未返回的内容。"
            ),
            parameters={
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
            display_names={
                "zh-CN": "读取小说分析结果",
                "en": "Read analysis review",
            },
        ),
        handler=read_review,
        policy=ToolPolicy(
            ToolExecutionMode.READ,
            "读取小说分析结果",
            ToolRiskLevel.READ,
        ),
        concurrency_safe=True,
        data_contract=ToolDataContract(
            model_owned_paths=(),
            host_bound_paths=("sourceRevisionId", "analysisArtifactId"),
        ),
        operation_display_params=lambda state, arguments, call: {
            "displayNames": {
                "zh-CN": "读取小说分析结果",
                "en": "Read analysis review",
            },
        },
    ),))


__all__ = [
    "READ_ANALYSIS_REVIEW_ARTIFACT",
    "build_novel_analysis_follow_up_tool_catalog",
]
