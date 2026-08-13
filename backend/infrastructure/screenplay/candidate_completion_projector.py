"""Atomically project a completed screenplay Run into its candidate Artifact."""

from __future__ import annotations

import json
from collections.abc import Mapping

from purra.contracts import RunStatus
from purra.errors import RunCommitProjectionError
from purra.ports import RunCommit

from domains.screenplay_agent.candidate_projection import (
    SCREENPLAY_CANDIDATE_PROJECTION_ATTRIBUTE,
    SCREENPLAY_CANDIDATE_PROJECTION_PROTOCOL,
    SCREENPLAY_CANDIDATE_VALIDATION_PROTOCOL,
)
from infrastructure.screenplay.tools.candidate_artifact import (
    ScreenplayCandidateArtifacts,
)


class ScreenplayCandidateCompletionError(RunCommitProjectionError):
    """A completed model result could not become a durable candidate."""

    default_code = "candidate_commit_failed"


class ScreenplayCandidateCompletionProjector:
    """Finalize the candidate in the same transaction as ``run.completed``."""

    def __init__(self, db, *, candidate_normalizer) -> None:
        self._db = db
        self._candidates = ScreenplayCandidateArtifacts(
            db,
            join_ambient_transaction=True,
            candidate_normalizer=candidate_normalizer,
        )

    async def project(
        self,
        run_id: str,
        commit: RunCommit,
    ) -> None:
        if commit.terminal_status is not RunStatus.DONE:
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
        if projection is None:
            return None
        if not isinstance(projection, Mapping) or str(
            projection.get("protocol") or ""
        ) != SCREENPLAY_CANDIDATE_PROJECTION_PROTOCOL:
            raise ScreenplayCandidateCompletionError(
                "screenplay candidate projection protocol is invalid"
            )

        try:
            scope = projection.get("scope")
            validation_contract = projection.get("validationContract")
            turn_id = str(projection.get("turnId") or "").strip()
            if (
                not isinstance(scope, Mapping)
                or not isinstance(validation_contract, Mapping)
                or validation_contract.get("protocol")
                != SCREENPLAY_CANDIDATE_VALIDATION_PROTOCOL
                or scope.get("candidateValidation") != validation_contract
                or not turn_id
            ):
                raise ValueError("candidate projection contract is invalid")
            await self._validate_started_event(run_id, turn_id)
            host_capture = projection.get("hostCapture")
            if isinstance(host_capture, Mapping):
                candidate = _host_candidate(
                    host_capture,
                    commit.validated_result,
                )
                await self._candidates.write_host_candidate(
                    run_id=run_id,
                    scope=scope,
                    candidate=candidate,
                )
            await self._candidates.validate_run(run_id=run_id, scope=scope)
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

    async def _validate_started_event(self, run_id: str, turn_id: str) -> None:
        rows = await self._db.fetch_all(
            "SELECT turn_id, source, kind, channel, visibility, "
            "output_stream_id, invocation_id FROM ai_agent_run_events "
            "WHERE source_event_key = ?",
            [f"run:{run_id}:running"],
        )
        expected = {
            "turn_id": turn_id,
            "source": "runtime",
            "kind": "run.lifecycle",
            "channel": "lifecycle",
            "visibility": "public",
            "output_stream_id": None,
            "invocation_id": None,
        }
        if (
            len(rows) != 1
            or {key: rows[0].get(key) for key in expected} != expected
        ):
            raise ValueError("candidate projection turn identity is invalid")


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
    validated_result: str | None,
) -> dict[str, object]:
    template = capture.get("candidateTemplate")
    if not isinstance(template, Mapping):
        raise ValueError("host candidate template is missing")
    text_field = str(capture.get("textField") or "").strip()
    text = str(validated_result or "").strip()
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
