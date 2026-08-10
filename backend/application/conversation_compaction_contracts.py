"""Application contracts for persistent semantic conversation summaries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from purra.contracts import ModelRequest, SessionId
from purra.json_values import freeze_json_mapping, thaw_json_mapping
from purra.ports import CancellationSignal


@dataclass(frozen=True, slots=True)
class ConversationTurn:
    id: int
    prompt: str
    response: str

    def __post_init__(self) -> None:
        turn_id = int(self.id)
        if turn_id <= 0:
            raise ValueError("conversation turn id must be positive")
        object.__setattr__(self, "id", turn_id)
        object.__setattr__(self, "prompt", str(self.prompt or ""))
        object.__setattr__(self, "response", str(self.response or ""))


@dataclass(frozen=True, slots=True)
class ConversationSummary:
    """One application-owned persistent semantic summary."""

    session_id: SessionId
    version: int
    covered_through_conversation_id: int
    covered_turn_count: int
    source_digest: str
    active_goal: str | None = None
    targets: tuple[Mapping[str, Any], ...] = ()
    decisions: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    unresolved_items: tuple[str, ...] = ()
    completed_actions: tuple[str, ...] = ()
    summary: str = ""

    def __post_init__(self) -> None:
        if isinstance(self.session_id, bool) or not str(self.session_id).strip():
            raise ValueError("conversation summary session_id is required")
        version = int(self.version)
        covered_id = int(self.covered_through_conversation_id)
        covered_count = int(self.covered_turn_count)
        if version <= 0:
            raise ValueError("conversation summary version must be positive")
        if covered_id <= 0 or covered_count <= 0:
            raise ValueError("conversation summary coverage must be positive")
        digest = str(self.source_digest or "").strip().lower()
        if len(digest) != 64 or any(
            char not in "0123456789abcdef" for char in digest
        ):
            raise ValueError("conversation summary source_digest must be sha256")
        object.__setattr__(self, "version", version)
        object.__setattr__(self, "covered_through_conversation_id", covered_id)
        object.__setattr__(self, "covered_turn_count", covered_count)
        object.__setattr__(self, "source_digest", digest)
        object.__setattr__(self, "active_goal", _optional_text(self.active_goal))
        object.__setattr__(
            self,
            "targets",
            tuple(freeze_json_mapping(value) for value in self.targets),
        )
        for name in (
            "decisions",
            "constraints",
            "unresolved_items",
            "completed_actions",
        ):
            object.__setattr__(
                self,
                name,
                tuple(dict.fromkeys(
                    str(value).strip()
                    for value in getattr(self, name)
                    if str(value).strip()
                )),
            )
        object.__setattr__(self, "summary", str(self.summary or "").strip())

    def to_mapping(self, *, include_persistence: bool = True) -> dict[str, Any]:
        value = {
            "activeGoal": self.active_goal,
            "targets": [thaw_json_mapping(item) for item in self.targets],
            "decisions": list(self.decisions),
            "constraints": list(self.constraints),
            "unresolvedItems": list(self.unresolved_items),
            "completedActions": list(self.completed_actions),
            "summary": self.summary,
        }
        if include_persistence:
            value = {
                "sessionId": self.session_id,
                "version": self.version,
                "coveredThroughConversationId": (
                    self.covered_through_conversation_id
                ),
                "coveredTurnCount": self.covered_turn_count,
                "sourceDigest": self.source_digest,
                **value,
            }
        return {
            key: item
            for key, item in value.items()
            if item not in (None, "", [], {})
        }


@runtime_checkable
class ConversationCompactionRepository(Protocol):
    """Application port for canonical turns and derived summary state."""

    async def load_summary(
        self,
        session_id: SessionId,
    ) -> ConversationSummary | None: ...

    async def list_turns(
        self,
        session_id: SessionId,
        *,
        after_conversation_id: int = 0,
    ) -> tuple[ConversationTurn, ...]: ...

    async def save_summary(
        self,
        summary: ConversationSummary,
        *,
        model: str | None = None,
    ) -> None: ...

    async def delete_summary(self, session_id: SessionId) -> None: ...


@runtime_checkable
class ConversationSummarizer(Protocol):
    """Application port for one semantic summary-generation strategy."""

    async def summarize(
        self,
        *,
        request: ModelRequest,
        max_output_tokens: int,
        existing: ConversationSummary | None,
        turns: Sequence[ConversationTurn],
        signal: CancellationSignal | None = None,
    ) -> Mapping[str, Any]: ...


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


__all__ = [
    "ConversationCompactionRepository",
    "ConversationSummarizer",
    "ConversationSummary",
    "ConversationTurn",
]
