"""Commit screenplay Agent effects as immutable Candidate Revisions."""

from __future__ import annotations

from collections.abc import Mapping

from agent_core.events import AgentEvent
from agent_core.json_values import thaw_json_mapping
from infrastructure.persistence.sqlite_screenplay_v2_repository import (
    SqliteScreenplayV2Repository,
)


SCREENPLAY_PROPOSAL_EVENT = "screenplay.document_proposal"
SCREENPLAY_REVISION_READY_EVENT = "screenplay.revision_ready"

_ROLE_BY_PROPOSAL_KIND = {
    "source_analysis": "sourceAnalysis",
    "creative_brief": "creativeBrief",
    "beat_sheet": "structure",
    "episode_outline": "structure",
    "scene_list": "sceneList",
    "scene_draft": "screenplayDraft",
    "review": "review",
}


class ScreenplayV2ProposalProjector:
    """Transactional Run-event projector used by ``SqliteRunRepository``."""

    def __init__(self, db) -> None:
        self._repository = SqliteScreenplayV2Repository(db)

    async def project(
        self,
        run_id: str,
        event: AgentEvent,
    ) -> AgentEvent | None:
        if event.type != SCREENPLAY_PROPOSAL_EVENT:
            return None

        proposal = thaw_json_mapping(event.payload)
        kind = str(proposal.get("kind") or "").strip()
        target_role = _ROLE_BY_PROPOSAL_KIND.get(kind)
        if target_role is None:
            raise ValueError(f"unsupported screenplay proposal kind: {kind}")

        raw_content = proposal.get("contentJson")
        if not isinstance(raw_content, Mapping):
            raise ValueError(
                "screenplay proposal contentJson must be an object"
            )
        title = str(proposal.get("title") or "").strip()
        if not title:
            raise ValueError("screenplay proposal title is required")
        derived_from_ids = tuple(dict.fromkeys(
            str(value).strip()
            for value in proposal.get("derivedFromIds", [])
            if str(value).strip()
        ))
        result = await self._repository.finalize_agent_candidate(
            finalizing_run_id=str(run_id),
            proposal_kind=kind,
            target_role=target_role,
            title=title,
            content_json=dict(raw_content),
            content_text=str(proposal.get("contentText") or ""),
            derived_from_ids=derived_from_ids,
        )
        if result is None:
            raise ValueError("screenplay proposal has no native project owner")
        reference = {
            "schemaVersion": 1,
            "projectId": str(result["projectId"]),
            "operationId": str(result["operationId"]),
            "revisionId": str(result["revisionId"]),
            "role": str(result["role"]),
            "revisionNo": int(result["revisionNo"]),
        }
        task_id = str(raw_content.get("longTaskId") or "").strip()
        if task_id:
            reference["taskId"] = task_id
        # The full proposal was consumed inside the same transaction and now
        # lives in immutable Revision Parts.  Persist and publish only its
        # business reference so Run recovery and conversations cannot create
        # another mutable content authority.
        return AgentEvent(
            type=SCREENPLAY_REVISION_READY_EVENT,
            run_id=str(run_id),
            payload=reference,
        )


__all__ = [
    "SCREENPLAY_PROPOSAL_EVENT",
    "SCREENPLAY_REVISION_READY_EVENT",
    "ScreenplayV2ProposalProjector",
]
