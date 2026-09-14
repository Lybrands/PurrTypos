"""Project explicitly public Provider text into the canonical commentary stream."""
import asyncio
from datetime import datetime, timezone
from contextlib import contextmanager
from contextvars import ContextVar

_private_operation = ContextVar("private_model_operation", default=False)

@contextmanager
def private_model_operation():
    token = _private_operation.set(True)
    try:
        yield
    finally:
        _private_operation.reset(token)


from purra.output import AgentOutputProcessor, AgentOutputIntent, AgentOutputEventDraft, OutputChannel, OutputSource

from constants import AGENT_PUBLIC_COMMENTARY_OPEN as OPEN_COMMENTARY, AGENT_PUBLIC_COMMENTARY_CLOSE as CLOSE_COMMENTARY


class PublicCommentaryParser:
    def __init__(self):
        self.pending = ""
        self.opened = False
        self.closed = False

    def feed(self, text, *, terminal=False):
        if self.closed:
            return ""
        self.pending += text
        if not self.opened:
            if OPEN_COMMENTARY.startswith(self.pending) and not terminal:
                return ""
            if not self.pending.startswith(OPEN_COMMENTARY):
                self.closed = True
                self.pending = ""
                return ""
            self.opened = True
            self.pending = self.pending[len(OPEN_COMMENTARY):]
        end = self.pending.find(CLOSE_COMMENTARY)
        if end >= 0:
            value = self.pending[:end]
            self.pending = ""
            self.closed = True
            return value
        if terminal:
            # An incomplete closing marker is framing, never public prose.
            keep = self._suffix_size()
            value = self.pending[:-keep] if keep else self.pending
            self.pending = ""
            self.closed = True
            return value
        keep = self._suffix_size()
        value = self.pending[:-keep] if keep else self.pending
        self.pending = self.pending[-keep:] if keep else ""
        return value

    def _suffix_size(self):
        return next((size for size in range(min(len(self.pending), len(CLOSE_COMMENTARY) - 1), 0, -1)
                     if CLOSE_COMMENTARY.startswith(self.pending[-size:])), 0)


class RecordPlanningProgressPolicy:
    async def authorize_provider_chunk(self, spec, chunk):
        return chunk

    async def authorize_planning_delta(self, spec, chunk):
        return chunk


class PublicCommentaryOutputProcessor(AgentOutputProcessor):
    """Use PurrA's journal/publisher; never promote reasoning or tool arguments."""
    def __init__(self, repository, publisher, *, is_child_run=None):
        super().__init__(repository, publisher, policy=RecordPlanningProgressPolicy())
        self._commentary_streams = {}
        self._public_stream_locks = {}
        self._held_public_streams = {}
        self._is_child_run = is_child_run

    async def open_model_stream(self, receipt, spec):
        lock = None
        if spec.intent in {AgentOutputIntent.EXECUTION_PUBLIC, AgentOutputIntent.FINAL_PUBLIC}:
            lock = self._public_stream_locks.setdefault(spec.run_id, asyncio.Lock())
            await lock.acquire()
        try:
            opened = await super().open_model_stream(receipt, spec)
        except BaseException:
            if lock is not None:
                lock.release()
            raise
        if lock is not None:
            self._held_public_streams[spec.output_stream_id] = lock
        is_child = (
            bool(await self._is_child_run(spec.run_id))
            if self._is_child_run is not None
            else False
        )
        if (
            spec.intent is AgentOutputIntent.STRUCTURED_PRIVATE
            and spec.output_protocol is None
            and not is_child
            and not _private_operation.get()
        ):
            self._commentary_streams[spec.output_stream_id] = (spec, PublicCommentaryParser(), 0)
        return opened

    async def accept_provider_chunk(self, output_stream_id, chunk):
        events = await super().accept_provider_chunk(output_stream_id, chunk)
        spec = self._require_stream(output_stream_id)
        if spec.intent in {AgentOutputIntent.FINAL_PUBLIC, AgentOutputIntent.EXECUTION_PUBLIC}:
            events = (*events, *await self.flush_model_stream(output_stream_id))
        state = self._commentary_streams.get(output_stream_id)
        if state is None:
            return events
        spec, parser, index = state
        value = parser.feed(chunk.content_delta or "", terminal=bool(chunk.tool_call_deltas or chunk.finish_reason))
        if value:
            # Flush the source bytes before publishing their public projection.
            await self.flush_model_stream(output_stream_id)
            index += 1
            event = await self._append(AgentOutputEventDraft.public_text(
                run_id=spec.run_id, turn_id=spec.turn_id,
                output_stream_id=output_stream_id, invocation_id=spec.invocation_id,
                source_event_key=f"public-commentary:{spec.invocation_id}:{index}",
                source=OutputSource.PROVIDER, channel=OutputChannel.COMMENTARY,
                delta=value, occurred_at=datetime.now(timezone.utc),
            ))
            events = (*events, event)
        self._commentary_streams[output_stream_id] = (spec, parser, index)
        return events

    async def finish_model_stream(self, output_stream_id, finish_reason):
        try:
            return await super().finish_model_stream(output_stream_id, finish_reason)
        finally:
            self._commentary_streams.pop(output_stream_id, None)
            self._release_public_stream(output_stream_id)

    async def abort_model_stream(self, output_stream_id, error_code):
        try:
            return await super().abort_model_stream(output_stream_id, error_code)
        finally:
            self._commentary_streams.pop(output_stream_id, None)
            self._release_public_stream(output_stream_id)

    def _release_public_stream(self, output_stream_id):
        lock = self._held_public_streams.pop(output_stream_id, None)
        if lock is not None:
            lock.release()

    async def accept_run_lifecycle_event(self, commit, event):
        outputs = await super().accept_run_lifecycle_event(commit, event)
        if commit.terminal_status is not None:
            run_ids = {item.run_id for item in commit.events}
            for run_id in run_ids:
                self._public_stream_locks.pop(run_id, None)
            self._commentary_streams = {
                key: state for key, state in self._commentary_streams.items()
                if state[0].run_id not in run_ids
            }
        return outputs
