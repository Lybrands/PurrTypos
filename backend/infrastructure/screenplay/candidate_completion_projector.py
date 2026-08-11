"""Atomically project a completed screenplay Run into its candidate Artifact."""

from __future__ import annotations

import json
from collections.abc import Mapping

from purra.events import AgentEvent, CoreEventType

from domains.screenplay_agent.candidate_projection import (
    SCREENPLAY_CANDIDATE_PROJECTION_ATTRIBUTE,
    SCREENPLAY_CANDIDATE_PROJECTION_PROTOCOL,
)
from infrastructure.screenplay.tools.candidate_artifact import (
    ScreenplayCandidateArtifacts,
)


class ScreenplayCandidateCompletionError(RuntimeError):
    """A completed model result could not become a durable candidate."""

    code = "candidate_commit_failed"


class ScreenplayCandidateCompletionProjector:
    """Finalize the candidate in the same transaction as ``run.completed``."""

    def __init__(self, db) -> None:
        self._db = db
        self._candidates = ScreenplayCandidateArtifacts(
            db,
            join_ambient_transaction=True,
        )

    async def project(
        self,
        run_id: str,
        event: AgentEvent,
    ) -> None:
        if event.type != CoreEventType.RUN_COMPLETED:
            return None
        row = await self._db.fetch_one(
            "SELECT binding_attributes_json FROM ai_agent_runs "
            "WHERE id = ? AND binding_namespace = ?",
            [run_id, "screenplay.agent.task"],
        )
        if row is None:
            return None
        attributes = _json_mapping(row.get("binding_attributes_json"))
        projection = attributes.get(
            SCREENPLAY_CANDIDATE_PROJECTION_ATTRIBUTE
        )
        if not isinstance(projection, Mapping) or str(
            projection.get("protocol") or ""
        ) != SCREENPLAY_CANDIDATE_PROJECTION_PROTOCOL:
            return None

        try:
            host_capture = projection.get("hostCapture")
            if isinstance(host_capture, Mapping):
                candidate = _host_candidate(host_capture, event)
                scope = projection.get("scope")
                if not isinstance(scope, Mapping):
                    raise ValueError("candidate projection scope is missing")
                await self._candidates.write_host_candidate(
                    run_id=run_id,
                    scope=scope,
                    candidate=candidate,
                )
            await self._candidates.finalize_run(run_id)
        except Exception as error:
            raise ScreenplayCandidateCompletionError(
                "screenplay candidate could not be committed"
            ) from error

        if isinstance(projection.get("hostCapture"), Mapping):
            # The long body has one durable source: the finalized Artifact.
            # Keep only the transient in-memory Run result needed by the caller.
            await self._db.execute(
                "UPDATE ai_agent_runs SET final_response = '' WHERE id = ?",
                [run_id],
            )


def _json_mapping(value: object) -> dict[str, object]:
    if isinstance(value, Mapping):
        return dict(value)
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def _host_candidate(
    capture: Mapping[str, object],
    event: AgentEvent,
) -> dict[str, object]:
    template = capture.get("candidateTemplate")
    if not isinstance(template, Mapping):
        raise ValueError("host candidate template is missing")
    text_field = str(capture.get("textField") or "").strip()
    text = str(event.payload.get("final_response") or "").strip()
    if not text_field or not text:
        raise ValueError("host candidate response is empty")
    candidate = dict(template)
    if text_field in candidate:
        raise ValueError("host candidate text field must be host-owned")
    candidate[text_field] = text
    return candidate


__all__ = [
    "ScreenplayCandidateCompletionError",
    "ScreenplayCandidateCompletionProjector",
]
