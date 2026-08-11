"""Provider-neutral guidance shared by Core model recovery paths."""

EMPTY_RESPONSE_RETRY_GUIDANCE = (
    "Your preceding model round ended after internal reasoning without any "
    "official response content. Continue the same request now and return the "
    "complete response through the requested output protocol. Do not return "
    "reasoning alone."
)

__all__ = ["EMPTY_RESPONSE_RETRY_GUIDANCE"]
