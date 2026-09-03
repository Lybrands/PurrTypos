"""Product-owned model limits for screenplay workflow units."""

from purra.model_protocol import (
    InvocationOutputLimit,
    ModelCapabilitySnapshot,
    resolve_invocation_output_limit,
)


def screenplay_output_limit(
    snapshot: ModelCapabilitySnapshot,
    explicit_user_override: int | None,
) -> InvocationOutputLimit:
    return resolve_invocation_output_limit(snapshot, explicit_user_override)


__all__ = ["screenplay_output_limit"]
