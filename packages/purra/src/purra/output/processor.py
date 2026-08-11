"""The only PurrA component allowed to create canonical outward output."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Protocol

from purra.contracts import ModelFinishReason, ModelStreamChunk
from purra.errors import (
    ContractViolationError,
    OutputPersistenceError,
)
from purra.json_values import thaw_json_mapping
from purra.model_invocation.contracts import ModelInvocationReceipt
from purra.operations import OperationFinished, OperationStarted
from purra.output.contracts import (
    AgentOutputEvent,
    AgentOutputEventDraft,
    AgentOutputIntent,
    DomainEffectOutput,
    FederatedOutputEvent,
    OutputChannel,
    OutputEventKind,
    OutputSource,
    OutputStreamSpec,
    OutputVisibility,
    RunLifecycleOutputDraft,
    ToolOutputEvent,
)
from purra.output.ports import (
    AgentOutputPolicy,
    AgentOutputPublisher,
    AgentOutputRepository,
)
from purra.ports.run_lifecycle import RunCommit


class OutputRecoveryObserver(Protocol):
    async def notify_output_failure(self, run_id: str, code: str) -> None: ...


class _AllowProviderChunks:
    async def authorize_provider_chunk(self, spec, chunk):
        del spec
        return chunk


class AgentOutputProcessor:
    """Normalize typed producers into persist-before-publish output events."""

    def __init__(
        self,
        repository: AgentOutputRepository,
        publisher: AgentOutputPublisher,
        *,
        policy: AgentOutputPolicy | None = None,
        recovery_observer: OutputRecoveryObserver | None = None,
    ) -> None:
        self._repository = repository
        self._publisher = publisher
        self._policy = policy or _AllowProviderChunks()
        self._recovery = recovery_observer
        self._streams: dict[str, OutputStreamSpec] = {}
        self._chunk_indices: dict[str, int] = {}

    async def open_model_stream(
        self,
        receipt: ModelInvocationReceipt,
        spec: OutputStreamSpec,
    ) -> OutputStreamSpec:
        if not isinstance(receipt, ModelInvocationReceipt):
            raise TypeError("output processor requires a ModelInvocationReceipt")
        if not isinstance(spec, OutputStreamSpec):
            raise TypeError("output processor requires an OutputStreamSpec")
        if (
            receipt.output_stream_id != spec.output_stream_id
            or receipt.invocation_id != spec.invocation_id
            or receipt.run_id != spec.run_id
            or receipt.turn_id != spec.turn_id
            or receipt.output_intent is not spec.intent
            or receipt.commit_mode is not spec.commit_mode
        ):
            raise ContractViolationError(
                "model invocation receipt does not match output stream"
            )
        opened = await self._persist_open(spec)
        self._streams[spec.output_stream_id] = opened
        self._chunk_indices.setdefault(spec.output_stream_id, 0)
        return opened

    async def accept_provider_chunk(
        self,
        output_stream_id: str,
        chunk: ModelStreamChunk,
    ) -> tuple[AgentOutputEvent, ...]:
        spec = self._require_stream(output_stream_id)
        if not isinstance(chunk, ModelStreamChunk):
            raise TypeError("output processor requires a ModelStreamChunk")
        authorized = await self._policy.authorize_provider_chunk(spec, chunk)
        if authorized is None:
            return ()
        if not isinstance(authorized, ModelStreamChunk):
            raise ContractViolationError(
                "output policy must return a ModelStreamChunk or None"
            )

        chunk_index = self._chunk_indices[output_stream_id] + 1
        self._chunk_indices[output_stream_id] = chunk_index
        occurred_at = datetime.now(timezone.utc)
        drafts: list[AgentOutputEventDraft] = []
        if authorized.content_delta:
            channel, visibility = _content_destination(spec)
            drafts.append(self._provider_draft(
                spec,
                chunk_index,
                "content",
                kind=OutputEventKind.PROVIDER_CONTENT_DELTA,
                channel=channel,
                visibility=visibility,
                payload={"delta": authorized.content_delta},
                occurred_at=occurred_at,
            ))
        if authorized.reasoning_delta:
            drafts.append(self._provider_draft(
                spec,
                chunk_index,
                "reasoning",
                kind=OutputEventKind.PROVIDER_REASONING_DELTA,
                channel=OutputChannel.DIAGNOSTIC,
                visibility=OutputVisibility.DIAGNOSTIC,
                payload={"delta": authorized.reasoning_delta},
                occurred_at=occurred_at,
            ))
        if authorized.tool_call_deltas:
            drafts.append(self._provider_draft(
                spec,
                chunk_index,
                "tools",
                kind=OutputEventKind.PROVIDER_TOOL_CALL_DELTA,
                channel=OutputChannel.DIAGNOSTIC,
                visibility=OutputVisibility.PRIVATE,
                payload={
                    "deltas": [
                        {
                            "index": delta.index,
                            "id": delta.id,
                            "type": delta.type,
                            "name": delta.name,
                            "argumentsFragment": delta.arguments_fragment,
                        }
                        for delta in authorized.tool_call_deltas
                    ]
                },
                occurred_at=occurred_at,
            ))
        if authorized.usage is not None:
            drafts.append(self._provider_draft(
                spec,
                chunk_index,
                "usage",
                kind=OutputEventKind.PROVIDER_USAGE,
                channel=OutputChannel.DIAGNOSTIC,
                visibility=OutputVisibility.PRIVATE,
                payload={
                    "inputTokens": authorized.usage.input_tokens,
                    "outputTokens": authorized.usage.output_tokens,
                    "totalTokens": authorized.usage.total_tokens,
                    "cachedInputTokens": authorized.usage.cached_input_tokens,
                    "reasoningOutputTokens": (
                        authorized.usage.reasoning_output_tokens
                    ),
                },
                occurred_at=occurred_at,
            ))

        events = []
        for draft in drafts:
            events.append(await self._append(draft))
        return tuple(events)

    async def finish_model_stream(
        self,
        output_stream_id: str,
        finish_reason: ModelFinishReason,
    ) -> AgentOutputEvent:
        spec = self._require_stream(output_stream_id)
        try:
            event = await self._repository.commit_stream(
                output_stream_id,
                finish_reason,
            )
        except ContractViolationError:
            raise
        except Exception as error:
            raise await self._persistence_error(spec.run_id, error) from error
        await self._publish_if_visible(event)
        return event

    async def abort_model_stream(
        self,
        output_stream_id: str,
        error_code: str,
    ) -> AgentOutputEvent:
        spec = self._require_stream(output_stream_id)
        try:
            event = await self._repository.abort_stream(
                output_stream_id,
                error_code,
            )
        except ContractViolationError:
            raise
        except Exception as error:
            raise await self._persistence_error(spec.run_id, error) from error
        await self._publish_if_visible(event)
        return event

    async def accept_operation_event(
        self,
        event: OperationStarted | OperationFinished,
    ) -> AgentOutputEvent:
        if isinstance(event, OperationStarted):
            draft = AgentOutputEventDraft(
                run_id=event.run_id,
                turn_id=None,
                output_stream_id=None,
                invocation_id=event.invocation_id,
                source_event_key=f"operation:{event.operation_id}:started",
                source=OutputSource.RUNTIME,
                kind=OutputEventKind.OPERATION_STARTED,
                channel=OutputChannel.OPERATION,
                visibility=OutputVisibility.PUBLIC,
                payload={
                    "operationId": event.operation_id,
                    "kind": event.kind.value,
                    "startedAt": event.started_at.isoformat(),
                    "display": thaw_json_mapping(event.display),
                },
                occurred_at=event.started_at,
            )
        elif isinstance(event, OperationFinished):
            draft = AgentOutputEventDraft(
                run_id=event.run_id,
                turn_id=None,
                output_stream_id=None,
                invocation_id=event.invocation_id,
                source_event_key=f"operation:{event.operation_id}:finished",
                source=OutputSource.RUNTIME,
                kind=OutputEventKind.OPERATION_FINISHED,
                channel=OutputChannel.OPERATION,
                visibility=OutputVisibility.PUBLIC,
                payload={
                    "operationId": event.operation_id,
                    "status": event.status.value,
                    "finishedAt": event.finished_at.isoformat(),
                    "durationMs": event.duration_ms,
                    "errorCode": event.error_code,
                    "display": thaw_json_mapping(event.display),
                },
                occurred_at=event.finished_at,
            )
        else:
            raise TypeError("output processor requires a typed operation event")
        return await self._append(draft)

    async def accept_run_lifecycle_event(
        self,
        commit: RunCommit,
        event: RunLifecycleOutputDraft,
    ) -> AgentOutputEvent:
        run_ids = {item.run_id for item in commit.events if item.run_id}
        if len(run_ids) != 1:
            raise ContractViolationError(
                "run lifecycle commit requires one bound run id"
            )
        run_id = next(iter(run_ids))
        try:
            committed = await self._repository.commit_run_lifecycle(
                run_id,
                commit,
                event,
            )
        except ContractViolationError:
            raise
        except Exception as error:
            raise await self._persistence_error(run_id, error) from error
        await self._publish_if_visible(committed)
        return committed

    async def accept_tool_event(self, event: ToolOutputEvent) -> AgentOutputEvent:
        if not isinstance(event, ToolOutputEvent):
            raise TypeError("output processor requires a ToolOutputEvent")
        return await self._append(AgentOutputEventDraft(
            run_id=event.run_id,
            turn_id=None,
            output_stream_id=None,
            invocation_id=event.invocation_id,
            source_event_key=(
                f"tool:{event.operation_id}:{event.tool_call_id}:{event.status}"
            ),
            source=OutputSource.TOOL,
            kind=OutputEventKind.TOOL,
            channel=OutputChannel.OPERATION,
            visibility=OutputVisibility.PUBLIC,
            payload={
                "operationId": event.operation_id,
                "toolCallId": event.tool_call_id,
                "toolName": event.tool_name,
                "status": event.status,
            },
            occurred_at=event.occurred_at,
        ))

    async def accept_domain_effect_event(
        self,
        event: DomainEffectOutput,
    ) -> AgentOutputEvent:
        if not isinstance(event, DomainEffectOutput):
            raise TypeError("output processor requires a DomainEffectOutput")
        return await self._append(AgentOutputEventDraft(
            run_id=event.run_id,
            turn_id=None,
            output_stream_id=None,
            invocation_id=None,
            source_event_key=f"domain:{event.effect_id}",
            source=OutputSource.DOMAIN,
            kind=OutputEventKind.DOMAIN_EFFECT,
            channel=OutputChannel.DIAGNOSTIC,
            visibility=OutputVisibility.PRIVATE,
            payload={
                "type": event.effect.type,
                "payload": thaw_json_mapping(event.effect.payload),
            },
            occurred_at=event.occurred_at,
        ))

    async def accept_federated_event(
        self,
        event: FederatedOutputEvent,
    ) -> AgentOutputEvent:
        if not isinstance(event, FederatedOutputEvent):
            raise TypeError("output processor requires a FederatedOutputEvent")
        source = event.source_event
        return await self._append(AgentOutputEventDraft(
            run_id=event.parent_run_id,
            turn_id=source.turn_id,
            output_stream_id=None,
            invocation_id=source.invocation_id,
            source_event_key=(
                f"delegation:{event.delegation_id}:{source.event_id}"
            ),
            source=OutputSource.RUNTIME,
            kind=OutputEventKind.DELEGATION,
            channel=OutputChannel.DELEGATION,
            visibility=source.visibility,
            payload={
                "delegationId": event.delegation_id,
                "parentRunId": event.parent_run_id,
                "sourceRunId": source.run_id,
                "sourceSequence": source.sequence,
                "event": {
                    "eventId": source.event_id,
                    "outputStreamId": source.output_stream_id,
                    "runId": source.run_id,
                    "turnId": source.turn_id,
                    "invocationId": source.invocation_id,
                    "sequence": source.sequence,
                    "source": source.source.value,
                    "kind": source.kind.value,
                    "channel": source.channel.value,
                    "visibility": source.visibility.value,
                    "payload": thaw_json_mapping(source.payload),
                    "occurredAt": source.occurred_at.isoformat(),
                    "emittedAt": source.emitted_at.isoformat(),
                },
            },
            occurred_at=source.occurred_at,
        ))

    def _provider_draft(
        self,
        spec: OutputStreamSpec,
        chunk_index: int,
        part: str,
        *,
        kind: OutputEventKind,
        channel: OutputChannel,
        visibility: OutputVisibility,
        payload: Mapping[str, object],
        occurred_at: datetime,
    ) -> AgentOutputEventDraft:
        return AgentOutputEventDraft(
            run_id=spec.run_id,
            turn_id=spec.turn_id,
            output_stream_id=spec.output_stream_id,
            invocation_id=spec.invocation_id,
            source_event_key=(
                f"provider:{spec.invocation_id}:{chunk_index}:{part}"
            ),
            source=OutputSource.PROVIDER,
            kind=kind,
            channel=channel,
            visibility=visibility,
            payload=payload,
            occurred_at=occurred_at,
        )

    async def _append(self, draft: AgentOutputEventDraft) -> AgentOutputEvent:
        try:
            event = await self._repository.append_event(draft)
        except ContractViolationError:
            raise
        except Exception as error:
            raise await self._persistence_error(draft.run_id, error) from error
        await self._publish_if_visible(event)
        return event

    async def _persist_open(self, spec: OutputStreamSpec) -> OutputStreamSpec:
        try:
            return await self._repository.open_stream(spec)
        except ContractViolationError:
            raise
        except Exception as error:
            raise await self._persistence_error(spec.run_id, error) from error

    async def _publish_if_visible(self, event: AgentOutputEvent) -> None:
        if event.visibility is OutputVisibility.PUBLIC:
            await self._publisher.publish_committed(event)

    async def _persistence_error(
        self,
        run_id: str,
        error: Exception,
    ) -> OutputPersistenceError:
        code = "output_persistence_failed"
        if self._recovery is not None:
            try:
                await self._recovery.notify_output_failure(run_id, code)
            except Exception:
                pass
        return OutputPersistenceError(
            "canonical output could not be persisted",
            code=code,
            details={"causeType": type(error).__name__},
        )

    def _require_stream(self, output_stream_id: str) -> OutputStreamSpec:
        stream_id = str(output_stream_id or "").strip()
        spec = self._streams.get(stream_id)
        if spec is None:
            raise ContractViolationError(
                f"output stream {stream_id!r} is not open"
            )
        return spec


def _content_destination(
    spec: OutputStreamSpec,
) -> tuple[OutputChannel, OutputVisibility]:
    if spec.intent is AgentOutputIntent.EXECUTION_PUBLIC:
        return OutputChannel.COMMENTARY, OutputVisibility.PUBLIC
    if spec.intent is AgentOutputIntent.FINAL_PUBLIC:
        return OutputChannel.FINAL, OutputVisibility.PUBLIC
    return OutputChannel.DIAGNOSTIC, OutputVisibility.PRIVATE


__all__ = ["AgentOutputProcessor", "OutputRecoveryObserver"]
