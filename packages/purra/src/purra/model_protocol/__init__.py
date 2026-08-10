"""Provider-neutral model response protocol classification."""

from purra.model_protocol.capabilities import (
    AssistantContentWithToolCalls,
    FeatureSupport,
    ModelProtocolCapabilities,
    ReasoningControl,
    ReasoningReplayPolicy,
)

from purra.model_protocol.termination import (
    ModelTermination,
    classify_model_termination,
)

__all__ = [
    "AssistantContentWithToolCalls",
    "FeatureSupport",
    "ModelProtocolCapabilities",
    "ModelTermination",
    "ReasoningControl",
    "ReasoningReplayPolicy",
    "classify_model_termination",
]
