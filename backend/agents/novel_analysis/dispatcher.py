"""PurrA Recipe dispatcher wiring for replacement Novel Analysis Tasks."""

from __future__ import annotations

from purra.long_tasks import (
    BudgetExhaustionDisposition,
    DurableExecutorRegistry,
    DurableTaskDescriptor,
    LongTaskBudgetLimits,
    RecipeLongTaskDispatcher,
)

from agents.novel_analysis.domain import (
    NOVEL_ANALYSIS_REPLACEMENT_DOMAIN_NAMESPACE,
)


class NovelAnalysisReplacementDescriptorResolver:
    async def resolve(self, request, plan, decision):
        del request, plan
        metadata = dict(decision.metadata)
        return DurableTaskDescriptor(
            namespace=NOVEL_ANALYSIS_REPLACEMENT_DOMAIN_NAMESPACE,
            owner_id=str(metadata["sourceRevisionId"]),
            idempotency_key=str(metadata["commandId"]),
            message="来源分析已进入 replacement 可恢复任务。",
            budget_limits=LongTaskBudgetLimits(
                max_invocation_attempts=int(metadata["modelAttemptBudget"]),
            ),
            budget_exhaustion_disposition=(
                BudgetExhaustionDisposition.FAIL_PERMANENT
            ),
            metadata=metadata,
        )


def create_novel_analysis_replacement_dispatcher(
    *,
    long_task_repository,
    executor,
    worker_id: str,
    executor_id: str = "novel_analysis.purra-native",
) -> RecipeLongTaskDispatcher:
    if long_task_repository is None:
        raise ValueError("replacement analysis long task repository is required")
    if executor is None:
        raise ValueError("replacement analysis Unit executor is required")
    return RecipeLongTaskDispatcher(
        long_task_repository=long_task_repository,
        descriptor_resolver=NovelAnalysisReplacementDescriptorResolver(),
        executor_registry=DurableExecutorRegistry({
            executor_id: executor,
        }),
        worker_id=worker_id,
        retry_backoff_ms=(),
    )


__all__ = [
    "NovelAnalysisReplacementDescriptorResolver",
    "create_novel_analysis_replacement_dispatcher",
]
