"""Typed, provider-neutral model protocol capability snapshots."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from enum import StrEnum
from hashlib import sha256


class ReasoningControl(StrEnum):
    SELECTABLE = "selectable"
    ALWAYS_ENABLED = "always_enabled"
    UNAVAILABLE = "unavailable"


class ReasoningReplayPolicy(StrEnum):
    REQUIRED = "required"
    FORBIDDEN = "forbidden"
    IGNORED = "ignored"


class FeatureSupport(StrEnum):
    SUPPORTED = "supported"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class AssistantContentWithToolCalls(StrEnum):
    REQUIRED = "required"
    OPTIONAL = "optional"
    FORBIDDEN = "forbidden"


@dataclass(frozen=True, slots=True)
class ModelProtocolCapabilities:
    """Immutable facts used by Core without inspecting provider identities."""

    reasoning_control: ReasoningControl = ReasoningControl.SELECTABLE
    reasoning_replay: ReasoningReplayPolicy = ReasoningReplayPolicy.IGNORED
    tool_calling: FeatureSupport = FeatureSupport.SUPPORTED
    required_tool_choice: FeatureSupport = FeatureSupport.SUPPORTED
    parallel_tool_calls: FeatureSupport = FeatureSupport.SUPPORTED
    assistant_content_with_tool_calls: AssistantContentWithToolCalls = (
        AssistantContentWithToolCalls.OPTIONAL
    )
    json_schema_level: str = "unknown"
    stream_finish_semantics: str = "normalized"
    usage_semantics: str = "normalized"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "reasoning_control",
            ReasoningControl(self.reasoning_control),
        )
        object.__setattr__(
            self,
            "reasoning_replay",
            ReasoningReplayPolicy(self.reasoning_replay),
        )
        for name in (
            "tool_calling",
            "required_tool_choice",
            "parallel_tool_calls",
        ):
            object.__setattr__(self, name, FeatureSupport(getattr(self, name)))
        object.__setattr__(
            self,
            "assistant_content_with_tool_calls",
            AssistantContentWithToolCalls(
                self.assistant_content_with_tool_calls
            ),
        )
        for name in (
            "json_schema_level",
            "stream_finish_semantics",
            "usage_semantics",
        ):
            value = str(getattr(self, name) or "").strip()
            if not value:
                raise ValueError(f"model protocol {name} is required")
            object.__setattr__(self, name, value)

    @classmethod
    def conservative(cls) -> "ModelProtocolCapabilities":
        return cls(
            reasoning_control=ReasoningControl.UNAVAILABLE,
            reasoning_replay=ReasoningReplayPolicy.FORBIDDEN,
            tool_calling=FeatureSupport.UNKNOWN,
            required_tool_choice=FeatureSupport.UNKNOWN,
            parallel_tool_calls=FeatureSupport.UNKNOWN,
        )

    def to_mapping(self) -> dict[str, str]:
        return {
            key: value.value if isinstance(value, StrEnum) else str(value)
            for key, value in asdict(self).items()
        }

    def digest(self) -> str:
        payload = json.dumps(
            self.to_mapping(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return sha256(payload).hexdigest()

    def reasoning_mode_is_supported(self, mode: object) -> bool:
        value = str(getattr(mode, "value", mode) or "").strip().lower()
        enabled = value in {"default", "enabled"}
        if self.reasoning_control is ReasoningControl.SELECTABLE:
            return value in {"default", "enabled", "disabled"}
        if self.reasoning_control is ReasoningControl.ALWAYS_ENABLED:
            return enabled
        return value == "disabled"


__all__ = [
    "AssistantContentWithToolCalls",
    "FeatureSupport",
    "ModelProtocolCapabilities",
    "ReasoningControl",
    "ReasoningReplayPolicy",
]
