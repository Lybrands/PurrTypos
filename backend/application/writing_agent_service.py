"""Writing-owned entry point into the generic Agent Run service."""

from __future__ import annotations

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
    return AgentRunService(composition).run(
        request=request,
        api_key=api_key,
        options=writing_run_options(
            request,
            provider_options,
            provenance=provenance or build_chat_run_provenance(body),
        ),
        signal=signal,
        run_binding_lifecycle=run_binding_lifecycle,
    )


__all__ = ["start_writing_agent_run"]
