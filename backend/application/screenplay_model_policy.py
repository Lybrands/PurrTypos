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
    *,
    part_cap: int | None = None,
) -> InvocationOutputLimit:
    if part_cap is not None and part_cap <= 0:
        raise ValueError("screenplay Part output cap must be positive")
    resolved = resolve_invocation_output_limit(snapshot, explicit_user_override)
    workflow_cap = min(
        SCREENPLAY_MAX_OUTPUT_TOKENS,
        part_cap if part_cap is not None else SCREENPLAY_MAX_OUTPUT_TOKENS,
    )
    if resolved.max_tokens <= workflow_cap:
        return resolved
    return InvocationOutputLimit(
        max_tokens=workflow_cap,
        source=InvocationOutputLimitSource.WORKFLOW_POLICY,
        profile_max_tokens=resolved.profile_max_tokens,
    )


__all__ = ["SCREENPLAY_MAX_OUTPUT_TOKENS", "screenplay_output_limit"]
