"""SQLite canonical output journal and output-stream state."""

from __future__ import annotations

import json
from dataclasses import replace
from constants import AGENT_PUBLIC_COMMENTARY_OPEN
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from purra.api import (
    PLANNING_STREAM_SCHEMA,
    PlanningScope,
    PlanningStreamError,
    PlanningStreamParser,
    PlanningTextDeltaParser,
)
from purra.contracts import (
    DomainEffect,
    ModelFinishReason,
    RunCreateParams,
    RunId,
    RunStatus,
)
from purra.errors import ContractViolationError, RunCommitProjectionError
from purra.json_values import thaw_json_mapping
from purra.normalization import non_negative_int, positive_int, required_text
from purra.output import (
    AGENT_PROGRESS_SCHEMA,
    AgentOutputEvent,
    AgentOutputEventDraft,
    AgentOutputIntent,
    DomainEffectOutput,
    OutputChannel,
    OutputCommitMode,
    OutputEventKind,
    OutputSource,
    OutputStreamSpec,
    OutputVisibility,
    RunLifecycleOutputDraft,
    TERMINAL_STREAM_ABORT_CAUSE,
    TERMINAL_STREAM_ABORT_ERROR_CODE,
)
from purra.ports import RunBeginResult, RunCommit
from purra.ports import RunBeginProjector, RunCommitProjector
from infrastructure.persistence.run_store import runtime_limits_from_mapping


_VALIDATED_RESULT_SCHEMA = "purra.run-validated-result/v1"


class SqliteAgentOutputRepository:
    """Persist canonical events before any transport is allowed to publish."""

    def __init__(
        self,
        db,
        *,
        run_repository=None,
        domain_projector=None,
        run_begin_projector: RunBeginProjector | None = None,
        run_commit_projector: RunCommitProjector | None = None,
    ) -> None:
        self._db = db
        self._runs = run_repository
        self._domain_projector = domain_projector
        self._run_begin_projector = run_begin_projector
        self._run_commit_projector = run_commit_projector

    async def open_stream(self, spec: OutputStreamSpec) -> OutputStreamSpec:
        if not isinstance(spec, OutputStreamSpec):
            raise TypeError("output repository requires an OutputStreamSpec")
        async with self._db.transaction(cancellation_linearizable=True):
            run = await self._db.fetch_one(
                "SELECT id, status FROM ai_agent_runs WHERE id = ?",
                [spec.run_id],
            )
            if run is None:
                raise ContractViolationError(
                    f"output stream run {spec.run_id!r} does not exist"
                )
            existing = await self._db.fetch_one(
                "SELECT * FROM ai_agent_output_streams "
                "WHERE id = ? OR invocation_id = ?",
                [spec.output_stream_id, spec.invocation_id],
            )
            if existing is not None:
                if _stream_spec(existing) != spec:
                    raise ContractViolationError(
                        "output stream id or invocation id is already bound"
                    )
                if (
                    run["status"] != RunStatus.RUNNING.value
                    and existing["status"] == "open"
                ):
                    raise ContractViolationError(
                        "terminal run cannot retain an open output stream"
                    )
                return spec
            if run["status"] != RunStatus.RUNNING.value:
                raise ContractViolationError(
                    "terminal run cannot open a new output stream"
                )
            if spec.planning_scope is not None:
                if not await self._planning_operation_is_active(
                    spec.run_id,
                    spec.planning_scope.operation_id,
                ):
                    raise ContractViolationError(
                        "planning operation is not active"
                    )
            await self._db.execute(
                "INSERT INTO ai_agent_output_streams "
                "(id, run_id, turn_id, invocation_id, intent, commit_mode, "
                "output_protocol, planning_run_id, planning_operation_id, "
                "planning_revision, planning_attempt) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    spec.output_stream_id,
                    spec.run_id,
                    spec.turn_id,
                    spec.invocation_id,
                    spec.intent.value,
                    spec.commit_mode.value,
                    spec.output_protocol,
                    (
                        spec.planning_scope.run_id
                        if spec.planning_scope is not None
                        else None
                    ),
                    (
                        spec.planning_scope.operation_id
                        if spec.planning_scope is not None
                        else None
                    ),
                    (
                        spec.planning_scope.revision
                        if spec.planning_scope is not None
                        else 0
                    ),
                    spec.planning_attempt,
                ],
            )
        return spec

    async def append_event(
        self,
        draft: AgentOutputEventDraft,
    ) -> AgentOutputEvent:
        if not isinstance(draft, AgentOutputEventDraft):
            raise TypeError("output repository requires an AgentOutputEventDraft")
        async with self._db.transaction(cancellation_linearizable=True):
            return await self._append_event_in_transaction(draft)

    async def append_batch(
        self,
        drafts: tuple[AgentOutputEventDraft, ...],
    ) -> tuple[AgentOutputEvent, ...]:
        normalized = tuple(drafts)
        if any(not isinstance(item, AgentOutputEventDraft) for item in normalized):
            raise TypeError("output batch requires AgentOutputEventDraft values")
        if not normalized:
            return ()
        run_ids = {item.run_id for item in normalized}
        if len(run_ids) != 1:
            raise ContractViolationError("one output batch cannot span Runs")
        source_keys = [item.source_event_key for item in normalized]
        if len(source_keys) != len(set(source_keys)):
            raise ContractViolationError("output batch source keys must be unique")
        async with self._db.transaction(cancellation_linearizable=True):
            pending: list[AgentOutputEventDraft] = []
            for item in normalized:
                if await self._existing_event_for_draft(item) is None:
                    await self._validate_draft_for_append(item)
                    pending.append(item)
            costs = tuple(_provider_output_cost(item) for item in pending)
            await self._reserve_provider_output_budget(
                normalized[0].run_id,
                event_count=sum(count for count, _ in costs),
                payload_bytes=sum(size for _, size in costs),
            )
            return tuple(
                [
                    await self._append_event_in_transaction(
                        item,
                        budget_prechecked=True,
                    )
                    for item in normalized
                ]
            )

    async def begin_run_lifecycle(
        self,
        params: RunCreateParams,
        started_event,
    ) -> tuple[RunBeginResult, AgentOutputEvent]:
        if self._runs is None:
            raise RuntimeError(
                "run lifecycle output requires the owning run repository"
            )
        async with self._runs.write_transaction():
            begun = await self._runs.begin_in_ambient_transaction(
                params,
                started_event,
                persist_legacy_event=False,
            )
            if self._run_begin_projector is not None:
                projected = await self._run_begin_projector.project(
                    begun.run_id,
                    params,
                )
                if projected is not None:
                    raise TypeError("run begin projector must return None")
            output = await self._append_event_in_transaction(
                AgentOutputEventDraft(
                    run_id=begun.run_id,
                    turn_id=params.turn_id,
                    output_stream_id=None,
                    invocation_id=None,
                    source_event_key=f"run:{begun.run_id}:running",
                    source=OutputSource.RUNTIME,
                    kind=OutputEventKind.RUN_LIFECYCLE,
                    channel=OutputChannel.LIFECYCLE,
                    visibility=OutputVisibility.PUBLIC,
                    payload=begun.event.payload,
                    occurred_at=_now(),
                )
            )
        return begun, output

    async def commit_run_lifecycle(
        self,
        run_id: RunId,
        commit: RunCommit,
        draft: RunLifecycleOutputDraft,
        related_drafts: tuple[AgentOutputEventDraft, ...] = (),
    ) -> tuple[AgentOutputEvent, ...]:
        normalized_run_id = required_text(run_id, "run id")
        if not isinstance(commit, RunCommit):
            raise TypeError("run lifecycle output requires a RunCommit")
        if not isinstance(draft, RunLifecycleOutputDraft):
            raise TypeError(
                "run lifecycle output requires a RunLifecycleOutputDraft"
            )
        expected_status = commit.terminal_status or RunStatus.RUNNING
        if draft.status != expected_status:
            raise ContractViolationError(
                "run lifecycle output status does not match RunCommit"
            )
        payload_status = str(draft.payload.get("status") or "").strip()
        if payload_status and payload_status != draft.status.value:
            raise ContractViolationError(
                "run lifecycle output payload status does not match draft"
            )
        if self._runs is None:
            raise RuntimeError(
                "run lifecycle output requires the owning run repository"
            )

        event_draft = AgentOutputEventDraft(
            run_id=normalized_run_id,
            turn_id=draft.turn_id,
            output_stream_id=None,
            invocation_id=None,
            source_event_key=draft.source_event_key,
            source=OutputSource.RUNTIME,
            kind=OutputEventKind.RUN_LIFECYCLE,
            channel=OutputChannel.LIFECYCLE,
            visibility=OutputVisibility.PUBLIC,
            payload=draft.payload,
            occurred_at=draft.occurred_at,
        )
        validated_result_draft = (
            AgentOutputEventDraft(
                run_id=normalized_run_id,
                turn_id=draft.turn_id,
                output_stream_id=None,
                invocation_id=None,
                source_event_key=(
                    f"run:{normalized_run_id}:validated-result"
                ),
                source=OutputSource.RUNTIME,
                kind=OutputEventKind.RUN_VALIDATED_RESULT,
                channel=OutputChannel.DIAGNOSTIC,
                visibility=OutputVisibility.PRIVATE,
                payload={
                    "schemaVersion": _VALIDATED_RESULT_SCHEMA,
                    "content": commit.validated_result,
                },
                occurred_at=draft.occurred_at,
            )
            if commit.validated_result is not None
            else None
        )
        for attempt in range(3):
            try:
                return await self._commit_run_lifecycle_attempt(
                    normalized_run_id,
                    commit,
                    event_draft,
                    related_drafts,
                    validated_result_draft,
                    draft.status,
                )
            except RunCommitProjectionError as error:
                if not error.retryable or attempt == 2:
                    raise
        raise AssertionError("unreachable Run projection retry state")

    async def _commit_run_lifecycle_attempt(
        self,
        run_id: str,
        commit: RunCommit,
        event_draft: AgentOutputEventDraft,
        related_drafts: tuple[AgentOutputEventDraft, ...],
        validated_result_draft: AgentOutputEventDraft | None,
        expected_status: RunStatus,
    ) -> tuple[AgentOutputEvent, ...]:
        # Every retry re-enters the authoritative Run transaction. Its current
        # status, lease owner, and lease expiry are revalidated before writes;
        # a concurrent terminal commit therefore follows the existing
        # idempotency/conflict path and is never overwritten.
        async with self._runs.write_transaction():
            existing = await self._existing_event_for_draft(event_draft)
            if existing is not None:
                run = await self._db.fetch_one(
                    "SELECT status FROM ai_agent_runs WHERE id = ?",
                    [run_id],
                )
                if run is None or run["status"] != expected_status.value:
                    raise ContractViolationError(
                        "canonical lifecycle event does not match Run state"
                    )
                related = tuple([
                    await self._require_event_by_source_key(
                        item.source_event_key
                    )
                    for item in related_drafts
                ])
                validated = await self._validated_replay_events(
                    run_id,
                    validated_result_draft,
                )
                terminal_aborts = await self._terminal_stream_abort_replay_events(
                    run_id,
                    commit.terminal_status,
                )
                return (*related, *validated, *terminal_aborts, existing)
            terminal_abort_drafts = await self._open_stream_abort_drafts(
                run_id,
                commit.terminal_status,
                event_draft.occurred_at,
            )
            atomic_drafts = (
                *related_drafts,
                *(
                    (validated_result_draft,)
                    if validated_result_draft is not None
                    else ()
                ),
                *terminal_abort_drafts,
            )
            for item in atomic_drafts:
                if await self._existing_event_for_draft(item) is not None:
                    raise ContractViolationError(
                        "partial canonical lifecycle commit already exists"
                    )
            await self._runs.apply_commit_in_ambient_transaction(
                run_id,
                commit,
            )
            if self._run_commit_projector is not None:
                try:
                    projected = await self._run_commit_projector.project(
                        run_id,
                        commit,
                    )
                except RunCommitProjectionError:
                    raise
                except Exception as error:
                    raise RunCommitProjectionError(
                        "run commit projector rejected the terminal transaction"
                    ) from error
                if projected is not None:
                    raise ContractViolationError(
                        "run commit projector must return None"
                    )
            if commit.terminal_status is not None:
                await self._db.execute(
                    "DELETE FROM ai_agent_artifact_claims WHERE run_id = ?",
                    [run_id],
                )
            related = tuple(
                [
                    await self._append_event_in_transaction(item)
                    for item in related_drafts
                ]
            )
            validated = ()
            if validated_result_draft is not None:
                validated = (
                    await self._append_event_in_transaction(
                        validated_result_draft
                    ),
                )
            terminal_aborts = tuple(
                [
                    await self._append_event_in_transaction(item)
                    for item in terminal_abort_drafts
                ]
            )
            if terminal_abort_drafts:
                await self._db.execute(
                    "UPDATE ai_agent_output_streams SET status = 'aborted', "
                    "error_code = ?, finish_reason = NULL, "
                    "update_time = CURRENT_TIMESTAMP WHERE run_id = ? "
                    "AND status = 'open'",
                    [TERMINAL_STREAM_ABORT_ERROR_CODE, run_id],
                )
            lifecycle = await self._append_event_in_transaction(event_draft)
            return (*related, *validated, *terminal_aborts, lifecycle)

    async def _open_stream_abort_drafts(
        self,
        run_id: str,
        terminal_status: RunStatus | None,
        occurred_at: datetime,
    ) -> tuple[AgentOutputEventDraft, ...]:
        if terminal_status is None:
            return ()
        rows = await self._db.fetch_all(
            "SELECT * FROM ai_agent_output_streams "
            "WHERE run_id = ? AND status = 'open' ORDER BY id",
            [run_id],
        )
        drafts = []
        for row in rows:
            spec = _stream_spec(row)
            draft = _terminal_stream_abort_draft(spec, terminal_status, occurred_at)
            if await self._has_live_commentary(spec):
                draft = replace(draft, channel=OutputChannel.COMMENTARY, visibility=OutputVisibility.PUBLIC)
            drafts.append(draft)
        return tuple(drafts)

    async def _terminal_stream_abort_replay_events(
        self,
        run_id: str,
        terminal_status: RunStatus | None,
    ) -> tuple[AgentOutputEvent, ...]:
        if terminal_status is None:
            return ()
        open_stream = await self._db.fetch_one(
            "SELECT id FROM ai_agent_output_streams "
            "WHERE run_id = ? AND status = 'open' LIMIT 1",
            [run_id],
        )
        if open_stream is not None:
            raise ContractViolationError(
                "terminal Run replay found an open output stream"
            )
        rows = await self._db.fetch_all(
            "SELECT e.*, s.status AS stream_status, "
            "s.error_code AS stream_error_code "
            "FROM ai_agent_run_events AS e "
            "JOIN ai_agent_output_streams AS s ON s.id = e.output_stream_id "
            "WHERE e.run_id = ? AND e.kind = ? ORDER BY e.sequence, e.id",
            [run_id, OutputEventKind.STREAM_ABORTED.value],
        )
        events = []
        for row in rows:
            payload = _json_mapping(row.get("payload_json"))
            if (
                payload.get("cause") != TERMINAL_STREAM_ABORT_CAUSE
                or payload.get("runStatus") != terminal_status.value
            ):
                continue
            if (
                row.get("stream_status") != "aborted"
                or row.get("stream_error_code")
                != TERMINAL_STREAM_ABORT_ERROR_CODE
            ):
                raise ContractViolationError(
                    "terminal stream abort event does not match stream state"
                )
            events.append(_event(row))
        return tuple(events)

    async def _validated_replay_events(
        self,
        run_id: str,
        expected: AgentOutputEventDraft | None,
    ) -> tuple[AgentOutputEvent, ...]:
        rows = await self._db.fetch_all(
            "SELECT * FROM ai_agent_run_events WHERE run_id = ? AND kind = ? "
            "ORDER BY sequence, id",
            [run_id, OutputEventKind.RUN_VALIDATED_RESULT.value],
        )
        if expected is None:
            if rows:
                raise ContractViolationError(
                    "terminal replay changed the validated result identity"
                )
            return ()
        if len(rows) != 1:
            raise ContractViolationError(
                "terminal replay requires exactly one validated result"
            )
        try:
            persisted = await self._existing_event_for_draft(expected)
        except ContractViolationError as error:
            raise ContractViolationError(
                "terminal replay changed the validated result identity"
            ) from error
        if persisted is None:
            raise ContractViolationError(
                "completed validated Run is missing its validated result"
            )
        return (persisted,)

    async def commit_stream(
        self,
        output_stream_id: str,
        finish_reason: ModelFinishReason,
    ) -> AgentOutputEvent:
        stream_id = required_text(output_stream_id, "output stream id")
        reason = ModelFinishReason(finish_reason)
        async with self._db.transaction(cancellation_linearizable=True):
            stream = await self._require_stream(stream_id)
            if stream["status"] == "committed":
                return await self._require_event_by_source_key(
                    f"stream:{stream_id}:committed"
                )
            if stream["status"] != "open":
                raise ContractViolationError("aborted output stream cannot commit")

            spec = _stream_spec(stream)
            live_commentary = await self._has_live_commentary(spec)
            if spec.intent is AgentOutputIntent.FINAL_PUBLIC:
                await self._project_final_conversation(spec)

            event = await self._append_event_in_transaction(
                AgentOutputEventDraft(
                    run_id=spec.run_id,
                    turn_id=spec.turn_id,
                    output_stream_id=spec.output_stream_id,
                    invocation_id=spec.invocation_id,
                    source_event_key=f"stream:{stream_id}:committed",
                    source=OutputSource.RUNTIME,
                    kind=OutputEventKind.STREAM_COMMITTED,
                    channel=OutputChannel.COMMENTARY if live_commentary else _stream_channel(spec),
                    visibility=OutputVisibility.PUBLIC if live_commentary else _stream_visibility(spec),
                    payload={"finishReason": reason.value},
                    occurred_at=_now(),
                )
            )
            await self._db.execute(
                "UPDATE ai_agent_output_streams SET status = 'committed', "
                "finish_reason = ?, error_code = NULL, "
                "update_time = CURRENT_TIMESTAMP WHERE id = ?",
                [reason.value, stream_id],
            )
            return event

    async def _has_live_commentary(self, spec):
        return await self._db.fetch_one(
            "SELECT id FROM ai_agent_run_events WHERE source_event_key = ?",
            [f"public-commentary:{spec.invocation_id}:1"],
        ) is not None

    async def publish_stream_content_as_commentary(
        self,
        output_stream_id: str,
    ) -> tuple[AgentOutputEvent, ...]:
        stream_id = required_text(output_stream_id, "output stream id")
        async with self._db.transaction(cancellation_linearizable=True):
            stream = await self._require_stream(stream_id)
            if stream["status"] != "committed":
                raise ContractViolationError(
                    "only a committed model stream can publish commentary"
                )
            spec = _stream_spec(stream)
            if await self._has_live_commentary(spec):
                return ()
            if spec.output_protocol is not None:
                raise ContractViolationError(
                    "planning streams cannot be promoted to commentary"
                )
            if spec.intent is not AgentOutputIntent.STRUCTURED_PRIVATE:
                raise ContractViolationError(
                    "only private model content can be promoted to commentary"
                )

            commentary_key = f"provider:{spec.invocation_id}:commentary"
            committed_key = f"stream:{stream_id}:commentary:committed"
            existing_commentary = await self._db.fetch_one(
                "SELECT * FROM ai_agent_run_events WHERE source_event_key = ?",
                [commentary_key],
            )
            existing_commit = await self._db.fetch_one(
                "SELECT * FROM ai_agent_run_events WHERE source_event_key = ?",
                [committed_key],
            )
            if existing_commentary is not None or existing_commit is not None:
                if existing_commentary is None or existing_commit is None:
                    raise ContractViolationError(
                        "partial commentary publication already exists"
                    )
                return (_event(existing_commentary), _event(existing_commit))

            scoped = await self._db.fetch_all(
                "SELECT kind, payload_json, occurred_at, channel, visibility "
                "FROM ai_agent_run_events WHERE output_stream_id = ? "
                "AND source = ? ORDER BY sequence, id",
                [
                    stream_id,
                    OutputSource.PROVIDER.value,
                ],
            )
            if not any(_row_has_tool_call_delta(row) for row in scoped):
                raise ContractViolationError(
                    "commentary publication requires a Provider tool call"
                )
            if any(
                row.get("kind") == OutputEventKind.AGENT_PROGRESS.value
                for row in scoped
            ):
                return ()
            rows = [
                row for row in scoped
                if row.get("channel") == OutputChannel.DIAGNOSTIC.value
                and row.get("visibility") == OutputVisibility.PRIVATE.value
                and row.get("kind") in {
                    OutputEventKind.PROVIDER_CONTENT_DELTA.value,
                    OutputEventKind.PROVIDER_DELTA_BATCH.value,
                }
            ]
            content = "".join(
                _provider_text(row)
                for row in rows
            )
            if content.startswith(AGENT_PUBLIC_COMMENTARY_OPEN):
                return ()
            if not content.strip():
                return ()

            commentary = await self._append_event_in_transaction(
                AgentOutputEventDraft.public_text(
                    run_id=spec.run_id,
                    turn_id=spec.turn_id,
                    output_stream_id=spec.output_stream_id,
                    invocation_id=spec.invocation_id,
                    source_event_key=commentary_key,
                    source=OutputSource.PROVIDER,
                    channel=OutputChannel.COMMENTARY,
                    delta=content,
                    occurred_at=datetime.fromisoformat(str(rows[0]["occurred_at"])),
                ),
                allow_committed_stream=True,
            )
            committed = await self._append_event_in_transaction(
                AgentOutputEventDraft(
                    run_id=spec.run_id,
                    turn_id=spec.turn_id,
                    output_stream_id=spec.output_stream_id,
                    invocation_id=spec.invocation_id,
                    source_event_key=committed_key,
                    source=OutputSource.RUNTIME,
                    kind=OutputEventKind.STREAM_COMMITTED,
                    channel=OutputChannel.COMMENTARY,
                    visibility=OutputVisibility.PUBLIC,
                    payload={"finishReason": str(stream.get("finish_reason") or "")},
                    occurred_at=_now(),
                ),
                allow_committed_stream=True,
            )
            return commentary, committed

    async def publish_stream_content_as_final(
        self,
        output_stream_id: str,
    ) -> tuple[AgentOutputEvent, ...]:
        stream_id = required_text(output_stream_id, "output stream id")
        async with self._db.transaction(cancellation_linearizable=True):
            stream = await self._require_stream(stream_id)
            if stream["status"] != "committed":
                raise ContractViolationError(
                    "only a committed model stream can publish final output"
                )
            spec = _stream_spec(stream)
            if spec.output_protocol is not None:
                raise ContractViolationError(
                    "planning streams cannot be promoted to final"
                )
            if spec.intent is not AgentOutputIntent.STRUCTURED_PRIVATE:
                raise ContractViolationError(
                    "only private model content can be promoted to final"
                )

            final_key = f"provider:{spec.invocation_id}:final"
            committed_key = f"stream:{stream_id}:final:committed"
            existing_final = await self._db.fetch_one(
                "SELECT * FROM ai_agent_run_events WHERE source_event_key = ?",
                [final_key],
            )
            existing_commit = await self._db.fetch_one(
                "SELECT * FROM ai_agent_run_events WHERE source_event_key = ?",
                [committed_key],
            )
            if existing_final is not None or existing_commit is not None:
                if existing_final is None or existing_commit is None:
                    raise ContractViolationError(
                        "partial final publication already exists"
                    )
                return _event(existing_final), _event(existing_commit)

            scoped = await self._db.fetch_all(
                "SELECT kind, payload_json, occurred_at, channel, visibility "
                "FROM ai_agent_run_events WHERE output_stream_id = ? "
                "AND source = ? ORDER BY sequence, id",
                [stream_id, OutputSource.PROVIDER.value],
            )
            if any(_row_has_tool_call_delta(row) for row in scoped):
                raise ContractViolationError(
                    "final publication rejects Provider tool calls"
                )
            rows = [
                row
                for row in scoped
                if row.get("channel") == OutputChannel.DIAGNOSTIC.value
                and row.get("visibility") == OutputVisibility.PRIVATE.value
                and row.get("kind")
                in {
                    OutputEventKind.PROVIDER_CONTENT_DELTA.value,
                    OutputEventKind.PROVIDER_DELTA_BATCH.value,
                }
            ]
            content = "".join(_provider_text(row) for row in rows)
            if not content.strip():
                return ()

            final = await self._append_event_in_transaction(
                AgentOutputEventDraft.public_text(
                    run_id=spec.run_id,
                    turn_id=spec.turn_id,
                    output_stream_id=spec.output_stream_id,
                    invocation_id=spec.invocation_id,
                    source_event_key=final_key,
                    source=OutputSource.PROVIDER,
                    channel=OutputChannel.FINAL,
                    delta=content,
                    occurred_at=datetime.fromisoformat(
                        str(rows[0]["occurred_at"])
                    ),
                ),
                allow_committed_stream=True,
            )
            await self._project_final_conversation(spec)
            committed = await self._append_event_in_transaction(
                AgentOutputEventDraft(
                    run_id=spec.run_id,
                    turn_id=spec.turn_id,
                    output_stream_id=spec.output_stream_id,
                    invocation_id=spec.invocation_id,
                    source_event_key=committed_key,
                    source=OutputSource.RUNTIME,
                    kind=OutputEventKind.STREAM_COMMITTED,
                    channel=OutputChannel.FINAL,
                    visibility=OutputVisibility.PUBLIC,
                    payload={
                        "finishReason": str(stream.get("finish_reason") or "")
                    },
                    occurred_at=_now(),
                ),
                allow_committed_stream=True,
            )
            return final, committed

    async def abort_stream(
        self,
        output_stream_id: str,
        error_code: str,
    ) -> AgentOutputEvent:
        stream_id = required_text(output_stream_id, "output stream id")
        normalized_error = required_text(error_code, "output stream error code")
        async with self._db.transaction(cancellation_linearizable=True):
            stream = await self._require_stream(stream_id)
            if stream["status"] == "aborted":
                return await self._require_event_by_source_key(
                    f"stream:{stream_id}:aborted"
                )
            if stream["status"] != "open":
                raise ContractViolationError("committed output stream cannot abort")
            spec = _stream_spec(stream)
            live_commentary = await self._has_live_commentary(spec)
            event = await self._append_event_in_transaction(
                AgentOutputEventDraft(
                    run_id=spec.run_id,
                    turn_id=spec.turn_id,
                    output_stream_id=spec.output_stream_id,
                    invocation_id=spec.invocation_id,
                    source_event_key=f"stream:{stream_id}:aborted",
                    source=OutputSource.RUNTIME,
                    kind=OutputEventKind.STREAM_ABORTED,
                    channel=OutputChannel.COMMENTARY if live_commentary else _stream_channel(spec),
                    visibility=OutputVisibility.PUBLIC if live_commentary else _stream_visibility(spec),
                    payload={"errorCode": normalized_error},
                    occurred_at=_now(),
                )
            )
            await self._db.execute(
                "UPDATE ai_agent_output_streams SET status = 'aborted', "
                "error_code = ?, finish_reason = NULL, "
                "update_time = CURRENT_TIMESTAMP WHERE id = ?",
                [normalized_error, stream_id],
            )
            return event

    async def has_public_progress(self, run_id: str) -> bool:
        row = await self._db.fetch_one(
            "SELECT 1 AS present FROM ai_agent_run_events e "
            "JOIN ai_agent_output_streams s ON s.id = e.output_stream_id "
            "WHERE e.run_id = ? AND e.visibility = 'public' "
            "AND e.channel = 'commentary' AND s.status != 'aborted' "
            "AND ((e.kind = 'provider.content_delta' AND trim(json_extract(e.payload_json, '$.delta')) != '') "
            "OR (e.kind = 'agent.progress' AND trim(json_extract(e.payload_json, '$.text')) != '')) "
            "LIMIT 1",
            [str(run_id)],
        )
        return row is not None

    async def list_events(
        self,
        run_id: RunId,
        *,
        after_sequence: int,
        limit: int = 200,
    ) -> tuple[AgentOutputEvent, ...]:
        normalized_run_id = required_text(run_id, "run id")
        cursor = non_negative_int(after_sequence, "after sequence")
        page_size = positive_int(limit, "limit")
        rows = await self._db.fetch_all(
            "SELECT * FROM ai_agent_run_events "
            "WHERE run_id = ? AND event_id IS NOT NULL AND sequence > ? "
            "ORDER BY sequence, id LIMIT ?",
            [normalized_run_id, cursor, page_size],
        )
        return tuple(_event(row) for row in rows)

    async def list_root_events(
        self,
        root_run_id: RunId,
        *,
        after_root_sequence: int,
        limit: int = 200,
    ) -> tuple[AgentOutputEvent, ...]:
        normalized_root_id = required_text(root_run_id, "root Run id")
        cursor = non_negative_int(after_root_sequence, "after root sequence")
        page_size = positive_int(limit, "limit")
        root = await self._db.fetch_one(
            "SELECT id, root_run_id FROM ai_agent_runs WHERE id = ?",
            [normalized_root_id],
        )
        if root is None:
            raise ContractViolationError(
                f"run {normalized_root_id!r} does not exist"
            )
        if str(root.get("root_run_id") or root["id"]) != normalized_root_id:
            raise ContractViolationError(
                "Root journal query requires a Root Run",
                code="run_scope_conflict",
            )
        rows = await self._db.fetch_all(
            "SELECT * FROM ai_agent_run_events "
            "WHERE root_run_id = ? AND event_id IS NOT NULL "
            "AND root_sequence > ? ORDER BY root_sequence, id LIMIT ?",
            [normalized_root_id, cursor, page_size],
        )
        return tuple(_event(row) for row in rows)

    async def load_validated_result(self, run_id: RunId) -> str:
        """Read one private validated result from the canonical Run journal."""

        normalized_run_id = required_text(run_id, "run id")
        run = await self._db.fetch_one(
            "SELECT status FROM ai_agent_runs WHERE id = ?",
            [normalized_run_id],
        )
        if run is None or run.get("status") != RunStatus.DONE.value:
            raise ContractViolationError(
                "validated result requires a completed Run"
            )
        lifecycle_rows = await self._db.fetch_all(
            "SELECT * FROM ai_agent_run_events "
            "WHERE run_id = ? AND kind = ? ORDER BY sequence, id",
            [normalized_run_id, OutputEventKind.RUN_LIFECYCLE.value],
        )
        completed_rows = [
            row
            for row in lifecycle_rows
            if str(_json_mapping(row.get("payload_json")).get("status") or "")
            == RunStatus.DONE.value
        ]
        if len(completed_rows) != 1:
            raise ContractViolationError(
                "validated result requires exactly one completed lifecycle event"
            )
        rows = await self._db.fetch_all(
            "SELECT * FROM ai_agent_run_events "
            "WHERE run_id = ? AND kind = ? ORDER BY sequence, id",
            [normalized_run_id, OutputEventKind.RUN_VALIDATED_RESULT.value],
        )
        if len(rows) != 1:
            raise ContractViolationError(
                "validated result requires exactly one canonical event"
            )
        row = rows[0]
        expected = {
            "run_id": normalized_run_id,
            "turn_id": completed_rows[0].get("turn_id"),
            "output_stream_id": None,
            "invocation_id": None,
            "source_event_key": f"run:{normalized_run_id}:validated-result",
            "source": OutputSource.RUNTIME.value,
            "event_type": OutputEventKind.RUN_VALIDATED_RESULT.value,
            "kind": OutputEventKind.RUN_VALIDATED_RESULT.value,
            "channel": OutputChannel.DIAGNOSTIC.value,
            "visibility": OutputVisibility.PRIVATE.value,
        }
        if {key: row.get(key) for key in expected} != expected:
            raise ContractViolationError(
                "validated result canonical event has invalid scope"
            )
        payload = _json_mapping(row.get("payload_json"))
        if set(payload) != {"schemaVersion", "content"} or (
            payload.get("schemaVersion") != _VALIDATED_RESULT_SCHEMA
            or not isinstance(payload.get("content"), str)
        ):
            raise ContractViolationError(
                "validated result canonical event has invalid payload"
            )
        return payload["content"]

    async def list_session_events(
        self,
        *,
        session_id: int,
        after_cursor: int,
        limit: int = 200,
    ) -> tuple[tuple[int, AgentOutputEvent], ...]:
        rows = await self._db.fetch_all(
            "SELECT e.* FROM ai_agent_run_events AS e "
            "JOIN ai_agent_runs AS r ON r.id = e.run_id "
            "WHERE r.session_id = ? "
            "AND e.event_id IS NOT NULL AND e.visibility = 'public' "
            "AND e.id > ? ORDER BY e.id LIMIT ?",
            [
                int(session_id),
                non_negative_int(after_cursor, "after cursor"),
                positive_int(limit, "limit"),
            ],
        )
        return tuple((int(row["id"]), _event(row)) for row in rows)

    async def list_bound_events(
        self, *, aggregate_id: str, namespaces: tuple[str, ...],
        after_cursor: int, limit: int,
    ) -> tuple[tuple[int, AgentOutputEvent], ...]:
        marks = ",".join("?" for _ in namespaces)
        rows = await self._db.fetch_all(
            "SELECT e.* FROM ai_agent_run_events e "
            "JOIN ai_agent_runs r ON r.id = e.run_id "
            "JOIN ai_agent_runs root ON root.id = "
            "COALESCE(r.root_run_id, r.id) "
            f"WHERE root.binding_namespace IN ({marks}) "
            "AND root.binding_aggregate_id = ? "
            "AND e.event_id IS NOT NULL AND e.visibility = 'public' "
            "AND e.id > ? ORDER BY e.id LIMIT ?",
            [*namespaces, aggregate_id, non_negative_int(after_cursor, "after cursor"),
             positive_int(limit, "limit")],
        )
        return tuple((int(row["id"]), _event(row)) for row in rows)

    async def _append_event_in_transaction(
        self,
        draft: AgentOutputEventDraft,
        *,
        allow_committed_stream: bool = False,
        budget_prechecked: bool = False,
    ) -> AgentOutputEvent:
        existing = await self._existing_event_for_draft(draft)
        if existing is not None:
            return existing

        await self._validate_draft_for_append(
            draft,
            allow_committed_stream=allow_committed_stream,
        )
        if not budget_prechecked:
            event_count, payload_bytes = _provider_output_cost(draft)
            await self._reserve_provider_output_budget(
                draft.run_id,
                event_count=event_count,
                payload_bytes=payload_bytes,
            )

        if draft.output_stream_id is not None:
            stream = await self._require_stream(draft.output_stream_id)
            if stream["status"] != "open" and not (
                allow_committed_stream and stream["status"] == "committed"
            ):
                raise ContractViolationError(
                    "canonical event requires an open output stream"
                )
            if (
                stream["run_id"] != draft.run_id
                or stream.get("turn_id") != draft.turn_id
                or stream["invocation_id"] != draft.invocation_id
            ):
                raise ContractViolationError(
                    "canonical event does not match its output stream"
                )
        else:
            run = await self._db.fetch_one(
                "SELECT id FROM ai_agent_runs WHERE id = ?",
                [draft.run_id],
            )
            if run is None:
                raise ContractViolationError(
                    f"canonical event run {draft.run_id!r} does not exist"
                )
        is_domain_effect = draft.kind is OutputEventKind.DOMAIN_EFFECT
        if is_domain_effect != (draft.source is OutputSource.DOMAIN):
            raise ContractViolationError(
                "domain effect events require domain source and kind"
            )
        if is_domain_effect and self._domain_projector is not None:
            effect_type = required_text(
                draft.payload.get("type"),
                "domain effect type",
            )
            effect_payload = draft.payload.get("payload")
            if not isinstance(effect_payload, Mapping):
                raise ContractViolationError(
                    "domain effect payload must be a mapping"
                )
            projected = await self._domain_projector.project(
                draft.run_id,
                DomainEffectOutput(
                    effect_id=draft.source_event_key,
                    run_id=draft.run_id,
                    effect=DomainEffect(
                        type=effect_type,
                        payload=thaw_json_mapping(effect_payload),
                    ),
                    occurred_at=draft.occurred_at,
                ),
            )
            if projected is not None:
                raise ContractViolationError(
                    "domain event projector must return None"
                )

        run_scope = await self._require_run_scope(draft.run_id)
        sequence_row = await self._db.fetch_one(
            "SELECT COALESCE(MAX(sequence), 0) AS value "
            "FROM ai_agent_run_events "
            "WHERE run_id = ?",
            [draft.run_id],
        )
        sequence = int((sequence_row or {}).get("value") or 0) + 1
        root_sequence_row = await self._db.fetch_one(
            "SELECT COALESCE(MAX(root_sequence), 0) AS value "
            "FROM ai_agent_run_events WHERE root_run_id = ? "
            "AND event_id IS NOT NULL",
            [run_scope["root_run_id"]],
        )
        root_sequence = int(
            (root_sequence_row or {}).get("value") or 0
        ) + 1
        emitted_at = _now()
        event_id = f"output-event-{uuid4().hex}"
        await self._db.execute(
            "INSERT INTO ai_agent_run_events "
            "(run_id, event_type, payload_json, event_id, turn_id, "
            "invocation_id, output_stream_id, sequence, source, kind, "
            "channel, visibility, occurred_at, emitted_at, source_event_key, "
            "root_run_id, agent_id, parent_run_id, root_sequence) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                draft.run_id,
                draft.kind.value,
                json.dumps(
                    thaw_json_mapping(draft.payload),
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                event_id,
                draft.turn_id,
                draft.invocation_id,
                draft.output_stream_id,
                sequence,
                draft.source.value,
                draft.kind.value,
                draft.channel.value,
                draft.visibility.value,
                draft.occurred_at.isoformat(),
                emitted_at.isoformat(),
                draft.source_event_key,
                run_scope["root_run_id"],
                run_scope["agent_id"],
                run_scope["parent_run_id"],
                root_sequence,
            ],
        )
        return AgentOutputEvent(
            event_id=event_id,
            output_stream_id=draft.output_stream_id,
            run_id=draft.run_id,
            turn_id=draft.turn_id,
            invocation_id=draft.invocation_id,
            sequence=sequence,
            source=draft.source,
            kind=draft.kind,
            channel=draft.channel,
            visibility=draft.visibility,
            payload=draft.payload,
            occurred_at=draft.occurred_at,
            emitted_at=emitted_at,
            root_run_id=run_scope["root_run_id"],
            agent_id=run_scope["agent_id"],
            parent_run_id=run_scope["parent_run_id"],
            root_sequence=root_sequence,
            source_event_key=draft.source_event_key,
        )

    async def _validate_draft_for_append(
        self,
        draft: AgentOutputEventDraft,
        *,
        allow_committed_stream: bool = False,
    ) -> None:
        if draft.output_stream_id is not None:
            stream = await self._require_stream(draft.output_stream_id)
            if stream["status"] != "open" and not (
                allow_committed_stream and stream["status"] == "committed"
            ):
                raise ContractViolationError(
                    "canonical event requires an open output stream"
                )
            if (
                stream["run_id"] != draft.run_id
                or stream.get("turn_id") != draft.turn_id
                or stream["invocation_id"] != draft.invocation_id
            ):
                raise ContractViolationError(
                    "canonical event does not match its output stream"
                )
            if (
                stream.get("output_protocol") is not None
                and draft.visibility is OutputVisibility.PUBLIC
                and draft.kind not in {
                    OutputEventKind.PLANNING_PROGRESS,
                    OutputEventKind.PLANNING_DELTA,
                }
            ):
                raise ContractViolationError(
                    "planning bytes cannot be published as ordinary text"
                )
        else:
            run = await self._db.fetch_one(
                "SELECT id FROM ai_agent_runs WHERE id = ?",
                [draft.run_id],
            )
            if run is None:
                raise ContractViolationError(
                    f"canonical event run {draft.run_id!r} does not exist"
                )
        if draft.kind in {
            OutputEventKind.PLANNING_PROGRESS,
            OutputEventKind.PLANNING_DELTA,
        }:
            await self._validate_planning_projection(draft)
        if draft.kind is OutputEventKind.AGENT_PROGRESS:
            await self._validate_agent_progress_projection(draft)
        is_domain_effect = draft.kind is OutputEventKind.DOMAIN_EFFECT
        if is_domain_effect != (draft.source is OutputSource.DOMAIN):
            raise ContractViolationError(
                "domain effect events require domain source and kind"
            )

    async def _reserve_provider_output_budget(
        self,
        run_id: str,
        *,
        event_count: int,
        payload_bytes: int,
    ) -> None:
        if event_count == 0 and payload_bytes == 0:
            return
        run_scope = await self._require_run_scope(run_id)
        root = await self._db.fetch_one(
            "SELECT runtime_limits_json FROM ai_agent_runs WHERE id = ?",
            [run_scope["root_run_id"]],
        )
        raw_limits = _json_mapping((root or {}).get("runtime_limits_json"))
        limits = runtime_limits_from_mapping(raw_limits)
        usage = await self._db.fetch_one(
            "SELECT COALESCE(SUM(provider_output_events), 0) AS events, "
            "COALESCE(SUM(provider_output_bytes), 0) AS bytes "
            "FROM ai_agent_runs WHERE root_run_id = ?",
            [run_scope["root_run_id"]],
        )
        if int((usage or {}).get("events") or 0) + event_count > (
            limits.max_provider_output_events
        ):
            raise ContractViolationError(
                "Root Run Provider output event budget was exceeded",
                code="runtime_budget_exceeded",
                details={"budgetKind": "provider_output_events"},
            )
        if int((usage or {}).get("bytes") or 0) + payload_bytes > (
            limits.max_provider_output_bytes
        ):
            raise ContractViolationError(
                "Root Run Provider output byte budget was exceeded",
                code="runtime_budget_exceeded",
                details={"budgetKind": "provider_output_bytes"},
            )
        await self._db.execute(
            "UPDATE ai_agent_runs SET provider_output_events = "
            "provider_output_events + ?, provider_output_bytes = "
            "provider_output_bytes + ?, update_time = CURRENT_TIMESTAMP "
            "WHERE id = ?",
            [event_count, payload_bytes, run_id],
        )

    async def _require_run_scope(self, run_id: str) -> dict[str, Any]:
        run = await self._db.fetch_one(
            "SELECT id, root_run_id, agent_id, parent_run_id "
            "FROM ai_agent_runs WHERE id = ?",
            [run_id],
        )
        if run is None:
            raise ContractViolationError(
                f"canonical event run {run_id!r} does not exist"
            )
        root_run_id = str(run.get("root_run_id") or run["id"])
        root = await self._db.fetch_one(
            "SELECT id, root_run_id FROM ai_agent_runs WHERE id = ?",
            [root_run_id],
        )
        if root is None or str(root.get("root_run_id") or root["id"]) != (
            root_run_id
        ):
            raise ContractViolationError(
                "canonical event Run has an invalid Root scope",
                code="run_scope_conflict",
            )
        return {
            "root_run_id": root_run_id,
            "agent_id": str(run.get("agent_id") or run["id"]),
            "parent_run_id": (
                str(run["parent_run_id"])
                if run.get("parent_run_id") is not None
                else None
            ),
        }

    async def _existing_event_for_draft(
        self,
        draft: AgentOutputEventDraft,
    ) -> AgentOutputEvent | None:
        row = await self._db.fetch_one(
            "SELECT * FROM ai_agent_run_events WHERE source_event_key = ?",
            [draft.source_event_key],
        )
        if row is None:
            return None
        expected = {
            "run_id": draft.run_id,
            "turn_id": draft.turn_id,
            "output_stream_id": draft.output_stream_id,
            "invocation_id": draft.invocation_id,
            "source": draft.source.value,
            "kind": draft.kind.value,
            "channel": draft.channel.value,
            "visibility": draft.visibility.value,
        }
        actual = {
            key: row.get(key)
            for key in expected
        }
        if actual != expected or _json_mapping(row.get("payload_json")) != dict(
            draft.payload
        ):
            raise ContractViolationError(
                f"source event key {draft.source_event_key!r} "
                "is already bound to a different event"
            )
        return _event(row)

    async def _project_final_conversation(self, spec: OutputStreamSpec) -> None:
        rows = await self._db.fetch_all(
            "SELECT kind, payload_json FROM ai_agent_run_events "
            "WHERE output_stream_id = ? AND kind IN (?, ?) AND source = ? "
            "AND visibility = ? ORDER BY sequence, id",
            [
                spec.output_stream_id,
                OutputEventKind.PROVIDER_CONTENT_DELTA.value,
                OutputEventKind.PROVIDER_DELTA_BATCH.value,
                OutputSource.PROVIDER.value,
                OutputVisibility.PUBLIC.value,
            ],
        )
        content = "".join(
            _provider_text(row)
            for row in rows
        )
        run = await self._db.fetch_one(
            "SELECT r.*, s.chapter_id AS authoritative_chapter_id "
            "FROM ai_agent_runs AS r "
            "LEFT JOIN ai_sessions AS s ON s.id = r.session_id "
            "WHERE r.id = ?",
            [spec.run_id],
        )
        if run is None:
            raise ContractViolationError(
                f"output stream run {spec.run_id!r} does not exist"
            )
        await self._db.execute(
            "UPDATE ai_agent_runs SET final_response = ?, "
            "update_time = CURRENT_TIMESTAMP WHERE id = ?",
            [content, spec.run_id],
        )
        if run.get("session_id") is None or not content:
            return
        if run.get("conversation_id") is not None:
            # A running proposal resolution can create a server-owned shell.
            # Canonical output completes that same row without touching the
            # product overlay stored in agent_process.
            await self._db.execute(
                "UPDATE ai_conversations SET chapter_id = ?, prompt = ?, "
                "response = ?, model = ? WHERE id = ? AND session_id = ?",
                [
                    run.get("authoritative_chapter_id"),
                    str(run.get("prompt") or ""),
                    content,
                    run.get("model_name"),
                    int(run["conversation_id"]),
                    int(run["session_id"]),
                ],
            )
            return
        conversation_id = await self._db.execute_and_get_id(
            "INSERT INTO ai_conversations "
            "(session_id, chapter_id, prompt, response, model) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                int(run["session_id"]),
                run.get("authoritative_chapter_id"),
                str(run.get("prompt") or ""),
                content,
                run.get("model_name"),
            ],
        )
        if conversation_id is None:
            raise RuntimeError("final output conversation was not created")
        await self._db.execute(
            "UPDATE ai_agent_runs SET conversation_id = ?, "
            "update_time = CURRENT_TIMESTAMP WHERE id = ?",
            [int(conversation_id), spec.run_id],
        )

    async def _require_stream(self, stream_id: str) -> dict[str, Any]:
        stream = await self._db.fetch_one(
            "SELECT * FROM ai_agent_output_streams WHERE id = ?",
            [stream_id],
        )
        if stream is None:
            raise ContractViolationError(
                f"output stream {stream_id!r} does not exist"
            )
        return stream

    async def _planning_operation_is_active(
        self,
        run_id: str,
        operation_id: str,
    ) -> bool:
        rows = await self._db.fetch_all(
            "SELECT kind, payload_json FROM ai_agent_run_events "
            "WHERE run_id = ? AND kind IN (?, ?) ORDER BY sequence, id",
            [
                run_id,
                OutputEventKind.OPERATION_STARTED.value,
                OutputEventKind.OPERATION_FINISHED.value,
            ],
        )
        scoped = [
            row
            for row in rows
            if _json_mapping(row.get("payload_json")).get("operationId")
            == operation_id
        ]
        if not scoped:
            return False
        payload = _json_mapping(scoped[-1].get("payload_json"))
        return (
            scoped[-1].get("kind") == OutputEventKind.OPERATION_STARTED.value
            and payload.get("kind") == "planning"
        )

    async def _validate_planning_projection(
        self,
        draft: AgentOutputEventDraft,
    ) -> None:
        stream = await self._require_stream(str(draft.output_stream_id or ""))
        spec = _stream_spec(stream)
        scope = spec.planning_scope
        payload = draft.payload
        if (
            spec.output_protocol != PLANNING_STREAM_SCHEMA
            or scope is None
            or payload.get("operationId") != scope.operation_id
            or payload.get("revision") != scope.revision
            or payload.get("attempt") != spec.planning_attempt
            or draft.source_event_key != (
                f"planning-delta:{draft.invocation_id}:"
                f"{payload.get('sourceChunkIndex')}:"
                f"{payload.get('sourcePartIndex')}"
                if draft.kind is OutputEventKind.PLANNING_DELTA
                else f"planning:{draft.invocation_id}:{payload.get('recordIndex')}"
            )
        ):
            raise ContractViolationError("planning projection scope mismatch")
        if not await self._planning_operation_is_active(
            draft.run_id,
            scope.operation_id,
        ):
            raise ContractViolationError("planning operation is not active")
        run = await self._db.fetch_one(
            "SELECT status FROM ai_agent_runs WHERE id = ?",
            [draft.run_id],
        )
        if run is None or run.get("status") != RunStatus.RUNNING.value:
            raise ContractViolationError(
                "terminal Run cannot accept planning progress"
            )
        rows = await self._db.fetch_all(
            "SELECT kind, payload_json FROM ai_agent_run_events "
            "WHERE run_id = ? AND invocation_id = ? AND source = ? "
            "ORDER BY sequence, id",
            [draft.run_id, draft.invocation_id, OutputSource.PROVIDER.value],
        )
        if draft.kind is OutputEventKind.PLANNING_DELTA:
            target_index = payload.get("sourceChunkIndex")
            target_part = payload.get("sourcePartIndex")
            if type(target_index) is not int or type(target_part) is not int:
                raise ContractViolationError(
                    "planning delta source identity is invalid"
                )
            by_index: dict[int, str] = {}
            for row in rows:
                for index, text in _provider_content_chunks(row):
                    by_index[index] = text
            parser = PlanningTextDeltaParser()
            expected = None
            for index in sorted(by_index):
                deltas = parser.feed(by_index[index])
                if index == target_index and 1 <= target_part <= len(deltas):
                    expected = deltas[target_part - 1]
                    break
            if (
                expected is None
                or expected.text != payload.get("textDelta")
                or expected.record_index != payload.get("recordIndex")
            ):
                raise ContractViolationError(
                    "planning delta does not match Provider source"
                )
            return
        raw = "".join(_provider_text(row) for row in rows).encode("utf-8")
        start = payload.get("sourceStart")
        end = payload.get("sourceEnd")
        if (
            type(start) is not int
            or type(end) is not int
            or not 0 <= start < end <= len(raw)
        ):
            raise ContractViolationError(
                "planning projection has invalid source span"
            )
        try:
            records = PlanningStreamParser().feed(raw[:end].decode("utf-8"))
            expected = next(
                record
                for record in records
                if record.record_index == payload.get("recordIndex")
            )
            if any(
                payload.get(key) != value
                for key, value in expected.to_mapping().items()
            ):
                raise ValueError("projection differs")
        except (
            PlanningStreamError,
            UnicodeDecodeError,
            ValueError,
            StopIteration,
        ) as error:
            raise ContractViolationError(
                "planning projection does not match Provider source"
            ) from error

    async def _validate_agent_progress_projection(
        self,
        draft: AgentOutputEventDraft,
    ) -> None:
        payload = thaw_json_mapping(draft.payload)
        chunk_index = payload.get("sourceChunkIndex")
        if (
            draft.source is not OutputSource.PROVIDER
            or draft.channel is not OutputChannel.COMMENTARY
            or draft.visibility is not OutputVisibility.PUBLIC
            or payload.get("schemaVersion") != AGENT_PROGRESS_SCHEMA
            or type(chunk_index) is not int
            or draft.source_event_key
            != f"agent-progress:{draft.invocation_id}:{chunk_index}"
        ):
            raise ContractViolationError("agent progress projection scope mismatch")
        run = await self._db.fetch_one(
            "SELECT status FROM ai_agent_runs WHERE id = ?",
            [draft.run_id],
        )
        if run is None or run.get("status") != RunStatus.RUNNING.value:
            raise ContractViolationError(
                "terminal Run cannot accept agent progress"
            )
        rows = await self._db.fetch_all(
            "SELECT kind, payload_json FROM ai_agent_run_events "
            "WHERE run_id = ? AND invocation_id = ? AND source = ? "
            "ORDER BY sequence, id",
            [draft.run_id, draft.invocation_id, OutputSource.PROVIDER.value],
        )
        expected = next(
            (
                _provider_progress_at_chunk(row, chunk_index)
                for row in rows
                if _provider_progress_at_chunk(row, chunk_index) is not None
            ),
            None,
        )
        if expected != payload.get("text"):
            raise ContractViolationError(
                "agent progress projection does not match Provider source"
            )

    async def _require_event_by_source_key(
        self,
        source_event_key: str,
    ) -> AgentOutputEvent:
        row = await self._db.fetch_one(
            "SELECT * FROM ai_agent_run_events WHERE source_event_key = ?",
            [source_event_key],
        )
        if row is None:
            raise ContractViolationError(
                f"canonical event {source_event_key!r} does not exist"
            )
        return _event(row)


def _stream_spec(row: dict[str, Any]) -> OutputStreamSpec:
    planning_operation_id = str(
        row.get("planning_operation_id") or ""
    ).strip()
    planning_run_id = str(row.get("planning_run_id") or "").strip()
    planning_scope = (
        PlanningScope(
            run_id=planning_run_id,
            operation_id=planning_operation_id,
            revision=int(row.get("planning_revision") or 0),
        )
        if planning_operation_id and planning_run_id
        else None
    )
    return OutputStreamSpec(
        output_stream_id=str(row["id"]),
        run_id=str(row["run_id"]),
        turn_id=str(row.get("turn_id") or "") or None,
        invocation_id=str(row["invocation_id"]),
        intent=AgentOutputIntent(str(row["intent"])),
        commit_mode=OutputCommitMode(str(row["commit_mode"])),
        output_protocol=str(row.get("output_protocol") or "") or None,
        planning_scope=planning_scope,
        planning_attempt=int(row.get("planning_attempt") or 0),
    )


def _terminal_stream_abort_draft(
    spec: OutputStreamSpec,
    terminal_status: RunStatus,
    occurred_at: datetime,
) -> AgentOutputEventDraft:
    if terminal_status is RunStatus.RUNNING:
        raise ValueError("terminal stream abort requires a terminal Run status")
    return AgentOutputEventDraft(
        run_id=spec.run_id,
        turn_id=spec.turn_id,
        output_stream_id=spec.output_stream_id,
        invocation_id=spec.invocation_id,
        source_event_key=f"stream:{spec.output_stream_id}:aborted",
        source=OutputSource.RUNTIME,
        kind=OutputEventKind.STREAM_ABORTED,
        channel=_stream_channel(spec),
        visibility=_stream_visibility(spec),
        payload={
            "errorCode": TERMINAL_STREAM_ABORT_ERROR_CODE,
            "cause": TERMINAL_STREAM_ABORT_CAUSE,
            "runStatus": terminal_status.value,
        },
        occurred_at=occurred_at,
    )


def _stream_channel(spec: OutputStreamSpec) -> OutputChannel:
    return (
        OutputChannel.COMMENTARY
        if spec.intent is AgentOutputIntent.EXECUTION_PUBLIC
        else OutputChannel.FINAL
        if spec.intent is AgentOutputIntent.FINAL_PUBLIC
        else OutputChannel.DIAGNOSTIC
    )


def _stream_visibility(spec: OutputStreamSpec) -> OutputVisibility:
    return (
        OutputVisibility.PUBLIC
        if spec.intent
        in {
            AgentOutputIntent.EXECUTION_PUBLIC,
            AgentOutputIntent.FINAL_PUBLIC,
        }
        else OutputVisibility.PRIVATE
    )


def _event(row: dict[str, Any]) -> AgentOutputEvent:
    return AgentOutputEvent(
        event_id=str(row["event_id"]),
        output_stream_id=str(row.get("output_stream_id") or "") or None,
        run_id=str(row["run_id"]),
        turn_id=str(row.get("turn_id") or "") or None,
        invocation_id=str(row.get("invocation_id") or "") or None,
        sequence=int(row["sequence"]),
        source=OutputSource(str(row["source"])),
        kind=OutputEventKind(str(row["kind"])),
        channel=OutputChannel(str(row["channel"])),
        visibility=OutputVisibility(str(row["visibility"])),
        payload=_json_mapping(row.get("payload_json")),
        occurred_at=datetime.fromisoformat(str(row["occurred_at"])),
        emitted_at=datetime.fromisoformat(str(row["emitted_at"])),
        root_run_id=str(row.get("root_run_id") or row["run_id"]),
        agent_id=str(row.get("agent_id") or row["run_id"]),
        parent_run_id=(
            str(row["parent_run_id"])
            if row.get("parent_run_id") is not None
            else None
        ),
        root_sequence=int(row.get("root_sequence") or row["sequence"]),
        source_event_key=str(row.get("source_event_key") or "") or None,
    )


def _json_mapping(value: object) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return dict(parsed) if isinstance(parsed, dict) else {}


_PROVIDER_OUTPUT_BUDGET_KINDS = frozenset({
    OutputEventKind.PROVIDER_CONTENT_DELTA,
    OutputEventKind.PROVIDER_REASONING_DELTA,
    OutputEventKind.PROVIDER_PROGRESS_DELTA,
    OutputEventKind.PROVIDER_TOOL_CALL_DELTA,
    OutputEventKind.PROVIDER_DELTA_BATCH,
})


def _provider_output_cost(
    draft: AgentOutputEventDraft,
) -> tuple[int, int]:
    if draft.kind not in _PROVIDER_OUTPUT_BUDGET_KINDS:
        return 0, 0
    payload = json.dumps(
        thaw_json_mapping(draft.payload),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return 1, len(payload)


def _provider_text(row: Mapping[str, Any]) -> str:
    payload = _json_mapping(row.get("payload_json"))
    if row.get("kind") == OutputEventKind.PROVIDER_CONTENT_DELTA.value:
        return str(payload.get("delta") or "")
    if row.get("kind") != OutputEventKind.PROVIDER_DELTA_BATCH.value:
        return ""
    entries = payload.get("entries")
    if not isinstance(entries, list):
        return ""
    return "".join(
        str((entry.get("payload") or {}).get("delta") or "")
        for entry in entries
        if isinstance(entry, dict)
        and entry.get("kind") == OutputEventKind.PROVIDER_CONTENT_DELTA.value
        and isinstance(entry.get("payload"), dict)
    )


def _provider_content_chunks(
    row: Mapping[str, Any],
) -> tuple[tuple[int, str], ...]:
    payload = _json_mapping(row.get("payload_json"))
    if row.get("kind") == OutputEventKind.PROVIDER_CONTENT_DELTA.value:
        index = payload.get("sourceChunkIndex")
        return (
            ((int(index), str(payload.get("delta") or "")),)
            if type(index) is int
            else ()
        )
    if row.get("kind") != OutputEventKind.PROVIDER_DELTA_BATCH.value:
        return ()
    entries = payload.get("entries")
    if not isinstance(entries, list):
        return ()
    return tuple(
        (int(entry["sourceChunkIndex"]), str(entry["payload"].get("delta") or ""))
        for entry in entries
        if isinstance(entry, dict)
        and entry.get("kind") == OutputEventKind.PROVIDER_CONTENT_DELTA.value
        and type(entry.get("sourceChunkIndex")) is int
        and isinstance(entry.get("payload"), dict)
    )


def _provider_progress_at_chunk(
    row: Mapping[str, Any],
    source_chunk_index: int,
) -> str | None:
    payload = _json_mapping(row.get("payload_json"))
    if row.get("kind") != OutputEventKind.PROVIDER_DELTA_BATCH.value:
        return None
    entries = payload.get("entries")
    if not isinstance(entries, list):
        return None
    for entry in entries:
        if (
            isinstance(entry, dict)
            and entry.get("kind")
            == OutputEventKind.PROVIDER_PROGRESS_DELTA.value
            and entry.get("sourceChunkIndex") == source_chunk_index
            and isinstance(entry.get("payload"), dict)
        ):
            value = str(entry["payload"].get("delta") or "")
            return value or None
    return None


def _row_has_tool_call_delta(row: Mapping[str, Any]) -> bool:
    if row.get("kind") == OutputEventKind.PROVIDER_TOOL_CALL_DELTA.value:
        return True
    if row.get("kind") != OutputEventKind.PROVIDER_DELTA_BATCH.value:
        return False
    entries = _json_mapping(row.get("payload_json")).get("entries")
    return isinstance(entries, list) and any(
        isinstance(entry, dict)
        and entry.get("kind") == OutputEventKind.PROVIDER_TOOL_CALL_DELTA.value
        for entry in entries
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)


__all__ = ["SqliteAgentOutputRepository"]
