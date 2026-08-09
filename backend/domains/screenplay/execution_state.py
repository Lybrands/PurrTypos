"""Run-scoped state for screenplay Agent requests."""

from __future__ import annotations

from agent_core.contracts import AgentRunRequest, ExecutionState
from domains.screenplay.contracts import ScreenplayDomainContext


class ScreenplayExecutionStateFactory:
    def create(self, request: AgentRunRequest) -> ExecutionState:
        context = ScreenplayDomainContext.from_core_context(
            request.domain_context
        )
        return ExecutionState(domain={
            "screenplayProjectId": context.project_id,
            "activeDocumentId": context.active_document_id,
            "chatAgentMode": request.mode or "",
            "contextWindow": context.context_window_label,
            "screenplayDraftSceneCount": context.draft_scene_count,
            "screenplayDraftScope": context.draft_scope,
            "screenplayBoundDraftSceneIds": list(
                context.bound_draft_scene_ids
            ),
        })
