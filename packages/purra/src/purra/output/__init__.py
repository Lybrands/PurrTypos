"""Canonical output contracts and ports."""

from purra.output.contracts import (
    AgentOutputEvent,
    AgentOutputEventDraft,
    AgentOutputIntent,
    DomainEffectOutput,
    DelegationOutputEvent,
    OutputChannel,
    OutputCommitMode,
    OutputEventKind,
    OutputSource,
    OutputStreamSpec,
    OutputVisibility,
    PublicFact,
    PublicFactBundle,
    PublicPresentationMode,
    ResponseTransactionMode,
    ResponseTransactionPolicy,
    RunLifecycleOutputDraft,
    RuntimeOutputEvent,
    TERMINAL_STREAM_ABORT_CAUSE,
    TERMINAL_STREAM_ABORT_ERROR_CODE,
    ToolOutputEvent,
)
from purra.output.ports import (
    AgentOutputPolicy,
    AgentOutputPublisher,
    AgentOutputJournalQuery,
    AgentOutputRepository,
    CommittedResultFactsProvider,
    ValidatedResultCommitter,
)

_LAZY_RESPONSE_TRANSACTION_EXPORTS = frozenset({
    "AgentResponseTransaction",
    "ResponseTransactionValidationError",
})


def __getattr__(name: str):
    if name in _LAZY_RESPONSE_TRANSACTION_EXPORTS:
        from purra.output import response_transaction

        return getattr(response_transaction, name)
    raise AttributeError(name)


__all__ = [
    name for name in globals() if not name.startswith("_")
] + sorted(_LAZY_RESPONSE_TRANSACTION_EXPORTS)
