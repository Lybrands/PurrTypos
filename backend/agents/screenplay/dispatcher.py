"""Durable dispatcher seam for Screenplay replacement recipes."""

from __future__ import annotations

from purra.long_tasks import (
    BudgetExhaustionDisposition,
    DurableExecutorRegistry,
    DurableTaskDescriptor,
    LongTaskBudgetLimits,
    RecipeLongTaskDispatcher,
)

from agents.screenplay.contracts import SCREENPLAY_REPLACEMENT_DOMAIN_NAMESPACE


class ScreenplayReplacementDescriptorResolver:
    async def resolve(self, request, plan, decision):
        del request, plan
        metadata = dict(decision.metadata)
        return DurableTaskDescriptor(
            namespace=SCREENPLAY_REPLACEMENT_DOMAIN_NAMESPACE,
            owner_id=str(metadata["projectId"]),
            idempotency_key=str(metadata["commandId"]),
            failed_resume_attempts=int(metadata.get("failedResumeAttempts") or 0),
            message="剧本任务已进入 replacement 可恢复执行。",
            budget_limits=LongTaskBudgetLimits(
                max_invocation_attempts=int(metadata["modelAttemptBudget"]),
            ),
            budget_exhaustion_disposition=(
                BudgetExhaustionDisposition.PAUSE_RECOVERABLE
            ),
            metadata=metadata,
        )


def create_screenplay_replacement_dispatcher(
    *,
    long_task_repository,
    executor,
    worker_id: str,
) -> RecipeLongTaskDispatcher:
    if long_task_repository is None:
        raise ValueError("Screenplay replacement task repository is required")
    if executor is None:
        raise ValueError("Screenplay replacement Unit executor is required")
    return RecipeLongTaskDispatcher(
        long_task_repository=long_task_repository,
        descriptor_resolver=ScreenplayReplacementDescriptorResolver(),
        executor_registry=DurableExecutorRegistry({
            "screenplay.purra-native": executor,
        }),
        worker_id=worker_id,
        retry_backoff_ms=(),
    )


__all__ = [
    "ScreenplayReplacementDescriptorResolver",
    "create_screenplay_replacement_dispatcher",
]
