"""Composition-owned context policy applied to every PurrTypos Agent."""

from __future__ import annotations

from dataclasses import replace

from purra.context_budget import estimate_json_tokens
from purra.contracts import (
    AgentRunRequest,
    ContextBlock,
    ContextBudget,
    ContextBundle,
    TaskContextRequest,
)
from purra.errors import ContractViolationError
from purra.ports import CancellationSignal, ContextProvider

from domains.agent_policy import (
    build_agent_final_response_policy,
    build_agent_public_progress_policy,
)


AGENT_PUBLIC_PROGRESS_CONTEXT = "agent_public_progress"
AGENT_FINAL_RESPONSE_CONTEXT = "agent_final_response"

_RESERVED_CONTEXT_NAMES = frozenset({
    AGENT_PUBLIC_PROGRESS_CONTEXT,
    AGENT_FINAL_RESPONSE_CONTEXT,
})


class SharedAgentContextProvider:
    """Add PurrTypos-wide behavior without giving domains an opt-in switch."""

    def __init__(self, provider: ContextProvider) -> None:
        self.provider = provider

    async def build_context(
        self,
        request: AgentRunRequest,
        budget: ContextBudget,
        signal: CancellationSignal | None = None,
    ) -> ContextBundle:
        bundle = await self.provider.build_context(request, budget, signal)
        return _with_shared_policy(
            bundle,
            include_public_progress=request.tools_enabled and (
                _is_public_response(request)
                or request.metadata.get("progressAudience") == "public"
            ),
            include_final_response=_is_public_response(request),
        )

    async def build_planning_context(
        self,
        request: AgentRunRequest,
        budget: ContextBudget,
        signal: CancellationSignal | None = None,
    ) -> ContextBundle:
        build = getattr(self.provider, "build_planning_context", None)
        bundle = await (
            build(request, budget, signal)
            if callable(build)
            else self.provider.build_context(request, budget, signal)
        )
        return _with_shared_policy(
            bundle,
            include_public_progress=False,
            include_final_response=False,
        )

    async def build_task_context(
        self,
        request: AgentRunRequest,
        budget: ContextBudget,
        task: TaskContextRequest,
        signal: CancellationSignal | None = None,
    ) -> ContextBundle:
        build = getattr(self.provider, "build_task_context", None)
        bundle = await (
            build(request, budget, task, signal)
            if callable(build)
            else self.provider.build_context(request, budget, signal)
        )
        return _with_shared_policy(
            bundle,
            include_public_progress=request.tools_enabled and (
                _is_public_response(request)
                or request.metadata.get("progressAudience") == "public"
            ),
            include_final_response=_is_public_response(request),
        )


def _is_public_response(request: AgentRunRequest) -> bool:
    # Host-owned metadata: HTTP request mapping never forwards this field.
    # Internal artifact/text units must not be asked to produce a user summary.
    return request.metadata.get("responseAudience") != "internal"


def with_shared_agent_context(
    provider: ContextProvider,
) -> SharedAgentContextProvider:
    if isinstance(provider, SharedAgentContextProvider):
        return provider
    return SharedAgentContextProvider(provider)


def _with_shared_policy(
    bundle: ContextBundle,
    *,
    include_public_progress: bool,
    include_final_response: bool,
) -> ContextBundle:
    if not isinstance(bundle, ContextBundle):
        raise ContractViolationError("context provider must return ContextBundle")
    collisions = _RESERVED_CONTEXT_NAMES & {
        block.name for block in bundle.blocks
    }
    if collisions:
        raise ContractViolationError(
            "domain context cannot replace shared Agent policy: "
            + ", ".join(sorted(collisions))
        )
    contents = []
    if include_public_progress:
        contents.append((AGENT_PUBLIC_PROGRESS_CONTEXT, build_agent_public_progress_policy()))
    if include_final_response:
        contents.append((
            AGENT_FINAL_RESPONSE_CONTEXT,
            build_agent_final_response_policy(),
        ))
    shared = tuple(
        ContextBlock(
            name=name,
            content=content,
            token_count=estimate_json_tokens(content),
            untrusted=False,
        )
        for name, content in contents
    )
    return replace(bundle, blocks=(*shared, *bundle.blocks))


__all__ = [
    "AGENT_FINAL_RESPONSE_CONTEXT",
    "AGENT_PUBLIC_PROGRESS_CONTEXT",
    "SharedAgentContextProvider",
    "with_shared_agent_context",
]
