"""Runtime context shared only by scalable-v2 follow-up Runs."""

from __future__ import annotations

import json

from agents.novel_analysis.domain import NovelAnalysisRequestScope
from agents.novel_analysis.source_tools import (
    NOVEL_ANALYSIS_SOURCE_SCOPE_STATE_KEY,
)
from purra.cancellation import raise_if_stopped
from purra.contracts import (
    ContextBlock,
    ContextBudgetClaim,
    ContextBundle,
    ExecutionState,
)


class NovelAnalysisFollowUpExecutionStateFactory:
    def create(self, request) -> ExecutionState:
        scope = NovelAnalysisRequestScope.from_request(request)
        return ExecutionState(domain={
            NOVEL_ANALYSIS_SOURCE_SCOPE_STATE_KEY: scope.to_mapping(),
            "sourceRevisionId": scope.source_revision_id,
            "commandId": scope.command_id,
            "sectionIds": list(scope.section_ids),
            "segments": [item.to_mapping() for item in scope.segments],
            "interactionKind": "follow_up",
            **(
                {"analysisArtifactId": str(request.metadata["analysisArtifactId"])}
                if request.metadata.get("analysisArtifactId") else {}
            ),
        })


class NovelAnalysisFollowUpContextProvider:
    async def build_context(self, request, budget, signal=None) -> ContextBundle:
        raise_if_stopped(signal)
        scope = NovelAnalysisRequestScope.from_request(request)
        if budget.allocation_for("novel_analysis_follow_up_policy") < 1:
            return ContextBundle()
        content = json.dumps({
            "schemaVersion": 1,
            "scope": {
                "sourceRevisionId": scope.source_revision_id,
                "sectionCount": len(scope.section_ids),
                "segmentCount": len(scope.segments),
            },
            "rules": [
                "Answer the current question, not a new full-book analysis.",
                *(
                    ["Read the bound review Artifact before relying on prior conclusions."]
                    if request.metadata.get("analysisArtifactId") else []
                ),
                "Read source segments when the question requires original text.",
                "Do not invent omitted facts or quotations.",
            ],
        }, ensure_ascii=False, separators=(",", ":"))
        return ContextBundle(blocks=(ContextBlock(
            name="novel_analysis_follow_up_policy",
            content=content,
            token_count=max(1, len(content) // 4),
            untrusted=False,
            host_metadata={"recipeVersion": 2},
        ),))

    async def build_planning_context(self, request, budget, signal=None):
        return await self.build_context(request, budget, signal)

    async def build_task_context(self, request, budget, task, signal=None):
        del task
        return await self.build_context(request, budget, signal)

    async def describe_context_demands(self, request, signal=None):
        NovelAnalysisRequestScope.from_request(request)
        raise_if_stopped(signal)
        return (ContextBudgetClaim(
            name="novel_analysis_follow_up_policy",
            desired_tokens=384,
            minimum_tokens=128,
            maximum_tokens=384,
            priority=100,
        ),)


__all__ = [
    "NovelAnalysisFollowUpContextProvider",
    "NovelAnalysisFollowUpExecutionStateFactory",
]
