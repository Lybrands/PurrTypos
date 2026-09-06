"""Offline probe for the installed PurrA response transaction; makes no SDK calls."""

import asyncio
from dataclasses import replace
from importlib.metadata import version

from purra.contracts import AgentMessage, AgentRunResult, MessageRole, ModelRequest, ReasoningMode, RunStatus
from purra.errors import ContractViolationError
from purra.model_invocation import AgentModelInvocationManager, ModelInvocationContext
from purra.output import PublicFact, PublicFactBundle, PublicPresentationMode, ResponseTransactionMode, ResponseTransactionPolicy
from purra.output.response_transaction import AgentResponseTransaction


class ProbeComplete(Exception):
    pass


class Probe:
    async def stream(self, messages, call, context, signal=None):
        print(f"  requested={context.requested_reasoning_mode.value}, call={call.reasoning_mode.value}")
        # Read-only diagnostic of the installed package's own admission check.
        AgentModelInvocationManager._validate_call(call, context, public_stream_allowed=True)
        raise ProbeComplete


class Facts:
    async def facts_for(self, run_id, result):
        return PublicFactBundle(facts=(PublicFact("result", "A review artifact exists."),))


async def main():
    print("Installed PurrA:", version("purra"))
    failures = 0
    for path in ("direct", "presentation"):
        for mode in ReasoningMode:
            print(f"{path}/{mode.value}")
            policy = ResponseTransactionPolicy(
                mode=ResponseTransactionMode.DIRECT_LIVE if path == "direct" else ResponseTransactionMode.VALIDATED_RESULT,
                public_presentation=PublicPresentationMode.NONE if path == "direct" else PublicPresentationMode.MODEL_LIVE,
            )
            transaction = AgentResponseTransaction(Probe(), policy=policy, facts_provider=Facts(), max_presentation_attempts=1)
            request = ModelRequest(provider="probe", model="offline", max_generation_tokens=256)
            request = replace(request, capability_snapshot=replace(request.capability_snapshot, max_generation_tokens=256))
            context = ModelInvocationContext(run_id="offline-probe", requested_reasoning_mode=mode)
            try:
                if path == "direct":
                    await transaction.execute_direct((AgentMessage(role=MessageRole.USER, content="Probe"),), request=request, context=context)
                else:
                    await transaction.present(AgentRunResult(run_id=context.run_id, status=RunStatus.DONE, final_response="artifact"), request=request, context=context)
            except ProbeComplete:
                print("  admission passed; transport intentionally not called")
            except ContractViolationError as error:
                failures += 1
                print(" ", error.code)
    print(f"Reasoning admission failures: {failures}/6; SDK calls: 0")
    return int(failures > 0)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
