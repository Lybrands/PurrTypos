"""Dependency-inversion ports for canonical Agent output."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from purra.contracts import (
    AgentRunResult,
    ModelFinishReason,
    ModelStreamChunk,
    RunId,
)
from purra.output.contracts import (
    AgentOutputEvent,
    AgentOutputEventDraft,
    OutputStreamSpec,
    PublicFactBundle,
    RunLifecycleOutputDraft,
)

if TYPE_CHECKING:
    from purra.ports.run_lifecycle import RunCommit


@runtime_checkable
class AgentOutputRepository(Protocol):
    async def open_stream(self, spec: OutputStreamSpec) -> OutputStreamSpec: ...

    async def append_event(
        self,
        draft: AgentOutputEventDraft,
    ) -> AgentOutputEvent: ...

    async def commit_run_lifecycle(
        self,
        run_id: RunId,
        commit: RunCommit,
        draft: RunLifecycleOutputDraft,
    ) -> AgentOutputEvent: ...

    async def commit_stream(
        self,
        output_stream_id: str,
        finish_reason: ModelFinishReason,
    ) -> AgentOutputEvent: ...

    async def abort_stream(
        self,
        output_stream_id: str,
        error_code: str,
    ) -> AgentOutputEvent: ...

    async def list_events(
        self,
        run_id: RunId,
        *,
        after_sequence: int,
        limit: int = 200,
    ) -> tuple[AgentOutputEvent, ...]: ...


@runtime_checkable
class AgentOutputPublisher(Protocol):
    async def publish_committed(self, event: AgentOutputEvent) -> None: ...

    async def wait_for_sequence(
        self,
        run_id: RunId,
        *,
        after_sequence: int,
    ) -> None: ...


@runtime_checkable
class AgentOutputPolicy(Protocol):
    async def authorize_provider_chunk(
        self,
        spec: OutputStreamSpec,
        chunk: ModelStreamChunk,
    ) -> ModelStreamChunk | None: ...


@runtime_checkable
class CommittedResultFactsProvider(Protocol):
    async def facts_for(
        self,
        run_id: RunId,
        result: AgentRunResult,
    ) -> PublicFactBundle: ...


@runtime_checkable
class ValidatedResultCommitter(Protocol):
    async def commit_candidate(
        self,
        run_id: RunId,
        candidate: str,
    ) -> AgentRunResult: ...


__all__ = [name for name in globals() if not name.startswith("_")]
