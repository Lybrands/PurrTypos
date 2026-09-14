"""Context budget claims owned by the replacement Writing request."""

from __future__ import annotations

from purra.context_budget import estimate_json_tokens
from purra.contracts import AgentRunRequest, ContextBudgetClaim

from agents.writing.request_contract import WritingRequestContext


WRITING_RETRIEVAL_CONTEXT = "writing_retrieval"
CONTINUATION_CANON_CONTEXT = "continuation_canon"


def writing_context_claims(
    request: AgentRunRequest,
) -> tuple[ContextBudgetClaim, ...]:
    """Preserve current allocation semantics without the legacy provider."""

    context = WritingRequestContext.from_core_context(request.domain_context)
    window = int(request.context_window or 200_000)
    memory = min(40_000, max(6_000, window // 25)) if context.book_id else 0
    associated = (
        min(120_000, max(24_000, window // 5))
        if context.associated_chapter_ids or context.associated_outline_ids
        else 0
    )
    claims: list[ContextBudgetClaim] = []
    if memory + associated > 0:
        claims.append(
            ContextBudgetClaim(
                WRITING_RETRIEVAL_CONTEXT,
                memory + associated,
            )
        )

    payload = request.domain_context.payload
    technique_snapshot = dict(payload.get("writing_technique_snapshot") or {})
    manual = technique_snapshot.get("manual", [])
    required = sum(
        int(candidate.get("entryBytes", 0)) // 2
        + estimate_json_tokens(candidate.get("composition", ""))
        + 1024
        for candidate in manual
        if isinstance(candidate, dict)
    )
    desired_techniques = required + (
        12_000 if technique_snapshot.get("candidates") else 0
    )
    if desired_techniques:
        claims.append(
            ContextBudgetClaim(
                "writing_techniques",
                desired_techniques,
                minimum_tokens=required,
                maximum_tokens=desired_techniques,
                priority=100,
            )
        )

    inherited = payload.get("inherited_canon_records")
    if payload.get("creation_mode") == "continuation" and inherited:
        desired_canon = min(
            24_000,
            max(2_000, estimate_json_tokens(inherited) + 400),
        )
        claims.append(
            ContextBudgetClaim(
                CONTINUATION_CANON_CONTEXT,
                desired_canon,
                minimum_tokens=min(1_000, desired_canon),
                maximum_tokens=desired_canon,
                priority=110,
            )
        )
    return tuple(claims)


__all__ = ["writing_context_claims"]
