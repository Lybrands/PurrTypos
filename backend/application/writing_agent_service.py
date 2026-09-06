"""Writing-owned entry point into the generic Agent Run service."""

from __future__ import annotations

from dataclasses import replace

from application.agent_run_service import AgentRunService
from application.request_mapping import (
    to_writing_agent_request,
    writing_run_options,
)
from application.run_provenance import build_chat_run_provenance


def start_writing_agent_run(
    *,
    composition,
    body,
    api_key: str,
    provider_options: dict,
    signal,
    provenance=None,
    run_binding_lifecycle=None,
):
    request = to_writing_agent_request(body, provider_options)
    run_options = writing_run_options(
        request,
        provider_options,
        provenance=None,
    )
    normalized_provenance = build_chat_run_provenance(
        body,
        request.model,
        result_capacity_target_tokens=(
            run_options.result_capacity_target_tokens
        ),
    )
    if provenance is not None and provenance != normalized_provenance:
        raise ValueError(
            "supplied Run provenance does not match the normalized ModelRequest"
        )
    run_options = replace(run_options, provenance=normalized_provenance)
    return AgentRunService(composition).run(
        request=request,
        api_key=api_key,
        options=run_options,
        signal=signal,
        run_binding_lifecycle=run_binding_lifecycle,
    )


__all__ = ["start_writing_agent_run"]
