"""Provider-neutral model response protocol classification."""

from purra.model_protocol.termination import (
    ModelTermination,
    classify_model_termination,
)

__all__ = ["ModelTermination", "classify_model_termination"]
