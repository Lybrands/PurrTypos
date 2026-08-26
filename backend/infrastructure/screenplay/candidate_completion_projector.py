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
    parse_candidate_validation_contract,
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
                or not turn_id
            ):
                raise ValueError("candidate projection contract is invalid")
            normalized_validation = parse_candidate_validation_contract(
                validation_contract
            )
            scope_validation = scope.get("candidateValidation")
            if (
                not isinstance(scope_validation, Mapping)
                or parse_candidate_validation_contract(scope_validation)
                != normalized_validation
            ):
                raise ValueError("candidate projection contract is invalid")
            await self._validate_started_event(run_id, turn_id)
            await self._validate_dependency_read(run_id, scope)
            if (
                str(scope.get("toolAccess") or "") == "draft_scene"
                and not await self._has_successful_tool_read(
                    run_id,
                    "getScreenplayEpisodeContext",
                )
            ):
                raise ValueError("draft scene episode context was not read")
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

    async def _validate_dependency_read(
        self,
        run_id: str,
        scope: Mapping[str, object],
    ) -> None:
        dependency_part_keys = scope.get("dependencyPartKeys", [])
        if (
            not isinstance(dependency_part_keys, list)
            or any(
                not isinstance(value, str) or not value.strip()
                for value in dependency_part_keys
            )
            or len(dependency_part_keys) != len(set(dependency_part_keys))
        ):
            raise ValueError("candidate dependency scope is invalid")
        if not dependency_part_keys:
            return
        if not await self._has_successful_tool_read(
            run_id,
            "readScreenplayTaskDependencies",
        ):
            raise ValueError("candidate dependencies were not read")

    async def _has_successful_tool_read(
        self,
        run_id: str,
        tool_name: str,
    ) -> bool:
        row = await self._db.fetch_one(
            "SELECT 1 AS present FROM ai_agent_run_events AS started "
            "JOIN ai_agent_run_events AS finished "
            "ON finished.run_id = started.run_id "
            "AND finished.event_type = 'operation.finished' "
            "AND json_extract(finished.payload_json, '$.operationId') = "
            "json_extract(started.payload_json, '$.operationId') "
            "WHERE started.run_id = ? "
            "AND started.event_type = 'operation.started' "
            "AND json_extract(started.payload_json, '$.kind') = 'tool' "
            "AND json_extract("
            "started.payload_json, '$.display.labelParams.toolName'"
            ") = ? "
            "AND json_extract(finished.payload_json, '$.status') = 'succeeded' "
            "LIMIT 1",
            [run_id, tool_name],
        )
        return row is not None


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
