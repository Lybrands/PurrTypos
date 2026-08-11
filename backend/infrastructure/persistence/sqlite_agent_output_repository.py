"""SQLite canonical output journal and output-stream state."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

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
)
from purra.ports import RunBeginResult, RunCommit
from purra.ports.projection import RunCommitProjector


class SqliteAgentOutputRepository:
    """Persist canonical events before any transport is allowed to publish."""

    def __init__(
        self,
        db,
        *,
        run_repository=None,
        domain_projector=None,
        run_commit_projector: RunCommitProjector | None = None,
    ) -> None:
        self._db = db
        self._runs = run_repository
        self._domain_projector = domain_projector
        self._run_commit_projector = run_commit_projector

    async def open_stream(self, spec: OutputStreamSpec) -> OutputStreamSpec:
        if not isinstance(spec, OutputStreamSpec):
            raise TypeError("output repository requires an OutputStreamSpec")
        async with self._db.transaction(cancellation_linearizable=True):
            run = await self._db.fetch_one(
                "SELECT id FROM ai_agent_runs WHERE id = ?",
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
                return spec
            await self._db.execute(
                "INSERT INTO ai_agent_output_streams "
                "(id, run_id, turn_id, invocation_id, intent, commit_mode) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [
                    spec.output_stream_id,
                    spec.run_id,
                    spec.turn_id,
                    spec.invocation_id,
                    spec.intent.value,
                    spec.commit_mode.value,
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
            output = await self._append_event_in_transaction(
                AgentOutputEventDraft(
                    run_id=begun.run_id,
                    turn_id=None,
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
            turn_id=None,
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
        async with self._runs.write_transaction():
            existing = await self._existing_event_for_draft(event_draft)
            if existing is not None:
                run = await self._db.fetch_one(
                    "SELECT status FROM ai_agent_runs WHERE id = ?",
                    [normalized_run_id],
                )
                if run is None or run["status"] != draft.status.value:
                    raise ContractViolationError(
                        "canonical lifecycle event does not match Run state"
                    )
                related = tuple(
                    await self._require_event_by_source_key(
                        item.source_event_key
                    )
                    for item in related_drafts
                )
                return (*related, existing)
            for item in related_drafts:
                if await self._existing_event_for_draft(item) is not None:
                    raise ContractViolationError(
                        "partial canonical lifecycle commit already exists"
                    )
            await self._runs.apply_commit_in_ambient_transaction(
                normalized_run_id,
                commit,
            )
            if self._run_commit_projector is not None:
                try:
                    projected = await self._run_commit_projector.project(
                        normalized_run_id,
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
            related = tuple(
                [
                    await self._append_event_in_transaction(item)
                    for item in related_drafts
                ]
            )
            lifecycle = await self._append_event_in_transaction(event_draft)
            return (*related, lifecycle)

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
                    channel=_stream_channel(spec),
                    visibility=_stream_visibility(spec),
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
            event = await self._append_event_in_transaction(
                AgentOutputEventDraft(
                    run_id=spec.run_id,
                    turn_id=spec.turn_id,
                    output_stream_id=spec.output_stream_id,
                    invocation_id=spec.invocation_id,
                    source_event_key=f"stream:{stream_id}:aborted",
                    source=OutputSource.RUNTIME,
                    kind=OutputEventKind.STREAM_ABORTED,
                    channel=_stream_channel(spec),
                    visibility=_stream_visibility(spec),
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

    async def _append_event_in_transaction(
        self,
        draft: AgentOutputEventDraft,
    ) -> AgentOutputEvent:
        existing = await self._existing_event_for_draft(draft)
        if existing is not None:
            return existing

        if draft.output_stream_id is not None:
            stream = await self._require_stream(draft.output_stream_id)
            if stream["status"] != "open":
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

        sequence_key = draft.turn_id or draft.run_id
        sequence_row = await self._db.fetch_one(
            "SELECT COALESCE(MAX(sequence), 0) AS value "
            "FROM ai_agent_run_events "
            "WHERE COALESCE(turn_id, run_id) = ?",
            [sequence_key],
        )
        sequence = int((sequence_row or {}).get("value") or 0) + 1
        emitted_at = _now()
        event_id = f"output-event-{uuid4().hex}"
        await self._db.execute(
            "INSERT INTO ai_agent_run_events "
            "(run_id, event_type, payload_json, event_id, turn_id, "
            "invocation_id, output_stream_id, sequence, source, kind, "
            "channel, visibility, occurred_at, emitted_at, source_event_key) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
        )

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
            "SELECT payload_json FROM ai_agent_run_events "
            "WHERE output_stream_id = ? AND kind = ? AND source = ? "
            "AND visibility = ? ORDER BY sequence, id",
            [
                spec.output_stream_id,
                OutputEventKind.PROVIDER_CONTENT_DELTA.value,
                OutputSource.PROVIDER.value,
                OutputVisibility.PUBLIC.value,
            ],
        )
        content = "".join(
            str(_json_mapping(row.get("payload_json")).get("delta") or "")
            for row in rows
        )
        run = await self._db.fetch_one(
            "SELECT * FROM ai_agent_runs WHERE id = ?",
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
            raise ContractViolationError(
                "final output stream already has a conversation projection"
            )
        conversation_id = await self._db.execute_and_get_id(
            "INSERT INTO ai_conversations "
            "(session_id, chapter_id, prompt, response, model) "
            "VALUES (?, NULL, ?, ?, ?)",
            [
                int(run["session_id"]),
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
    return OutputStreamSpec(
        output_stream_id=str(row["id"]),
        run_id=str(row["run_id"]),
        turn_id=str(row.get("turn_id") or "") or None,
        invocation_id=str(row["invocation_id"]),
        intent=AgentOutputIntent(str(row["intent"])),
        commit_mode=OutputCommitMode(str(row["commit_mode"])),
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
    )


def _json_mapping(value: object) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return dict(parsed) if isinstance(parsed, dict) else {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


__all__ = ["SqliteAgentOutputRepository"]
