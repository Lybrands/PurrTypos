"""Product composition adapter for Core-owned child Run coordination."""

from __future__ import annotations

import json
from dataclasses import replace

from purra.contracts import (
    AgentMessage,
    AgentRunRequest,
    AgentRunResult,
    DelegationClaim,
    MessageOrigin,
    MessageRole,
    ResponseConstraints,
)
from purra.output import ResponseTransactionPolicy
from purra.api import AgentCoreRunOptions, AgentRunHandle
from application.agent_run_input import AgentRunInput
from application.run_provenance import build_chat_run_provenance
from domains.agent_roles import AgentRoleRegistry


class ApplicationDelegationAdapter:
    """Build role-scoped requests and submit them through stable PurrA APIs."""

    def __init__(
        self,
        *,
        composition,
        body: AgentRunInput,
        api_key: str,
        parent_request: AgentRunRequest,
        parent_options: AgentCoreRunOptions,
        role_registry: AgentRoleRegistry,
    ) -> None:
        self._composition = composition
        self._body = body
        self._api_key = api_key
        self._parent_request = parent_request
        self._parent_options = parent_options
        self._roles = role_registry

    async def build(
        self,
        claim: DelegationClaim,
    ) -> tuple[AgentRunRequest, AgentCoreRunOptions]:
        role = claim.delegation.agent_role
        definition = self._roles.require(role)
        objective = claim.delegation.objective
        input_payload = dict(claim.delegation.input_payload)
        child_prompt = objective
        if input_payload:
            child_prompt += "\n\nStructured input data:\n" + json.dumps(
                input_payload,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        child_body = self._body.model_copy(update={
            "messages": [{"role": "user", "content": child_prompt}],
            "sessionId": None,
            "chatAgentMode": "agent",
        })
        child_request = replace(
            self._parent_request,
            messages=(
                AgentMessage(
                    role=MessageRole.SYSTEM,
                    content=definition.instruction,
                    origin=MessageOrigin.HOST_CONTEXT,
                ),
                AgentMessage(role=MessageRole.USER, content=child_prompt),
            ),
            session_id=None,
            mode="agent",
            metadata={
                **self._parent_request.metadata,
                "agentRole": role,
            },
        )
        child_options = replace(
            self._parent_options,
            provenance=build_chat_run_provenance(child_body),
            lineage=claim.lineage,
            binding=None,
            response_constraints=ResponseConstraints(),
            response_validators=(),
            response_judges=(),
            response_judge_policies=(),
            response_transaction_policy=None,
            committed_result_facts_provider=None,
        )
        return child_request, child_options

    async def submit(
        self,
        request: AgentRunRequest,
        *,
        options: object | None = None,
    ) -> AgentRunHandle:
        if not isinstance(options, AgentCoreRunOptions):
            raise TypeError("child run requires AgentCoreRunOptions")
        role = str(request.metadata.get("agentRole") or "").strip()
        definition = self._roles.require(role)
        kwargs = {"allowed_tool_modes": definition.allowed_tool_modes}
        core = self._composition.create_core_for_request(
            request,
            self._api_key,
            **kwargs,
        )
        try:
            handle = await core.submit(request, options=options)
        except BaseException:
            release_core = getattr(self._composition, "release_core", None)
            if callable(release_core):
                release_core(core)
            raise
        return _ReleasingChildHandle(
            handle,
            composition=self._composition,
            core=core,
        )


class _ReleasingChildHandle:
    def __init__(self, handle: AgentRunHandle, *, composition, core) -> None:
        self._handle = handle
        self._composition = composition
        self._core = core
        self._released = False

    @property
    def run_id(self):
        return self._handle.run_id

    def subscribe(self, after_sequence: int = 0):
        return self._handle.subscribe(after_sequence=after_sequence)

    async def wait(self) -> AgentRunResult:
        try:
            return await self._handle.wait()
        finally:
            self._release()

    async def cancel(self, reason: str) -> None:
        await self._handle.cancel(reason)

    def _release(self) -> None:
        if self._released:
            return
        self._released = True
        release_core = getattr(self._composition, "release_core", None)
        if callable(release_core):
            release_core(self._core)


__all__ = ["ApplicationDelegationAdapter"]
