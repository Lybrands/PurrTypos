"""Provider-neutral model response protocol classification."""

from agent_core.model_protocol.termination import (
    ModelTermination,
    classify_model_termination,
)

__all__ = ["ModelTermination", "classify_model_termination"]
