"""Product-owned model limits for screenplay workflow units."""

from purra.model_protocol import (
    InvocationOutputLimit,
    InvocationOutputLimitSource,
    ModelCapabilitySnapshot,
    resolve_invocation_output_limit,
)


SCREENPLAY_MAX_OUTPUT_TOKENS = 32_768


def screenplay_output_limit(
    snapshot: ModelCapabilitySnapshot,
    explicit_user_override: int | None,
) -> InvocationOutputLimit:
    resolved = resolve_invocation_output_limit(snapshot, explicit_user_override)
    if resolved.max_tokens <= SCREENPLAY_MAX_OUTPUT_TOKENS:
        return resolved
    return InvocationOutputLimit(
        max_tokens=SCREENPLAY_MAX_OUTPUT_TOKENS,
        source=InvocationOutputLimitSource.WORKFLOW_POLICY,
        profile_max_tokens=resolved.profile_max_tokens,
    )


__all__ = ["SCREENPLAY_MAX_OUTPUT_TOKENS", "screenplay_output_limit"]
