import type {
  AiTaskPlan,
  AiTaskStep,
  ChatMessage,
} from "./hooks/chat.types";

export const MIN_VISIBLE_TASK_PLAN_STEPS = 3;

/** 后端状态机用于交付最终答复的固定收尾步骤，不属于用户任务。 */
export function isImplicitRespondStep(step: AiTaskStep): boolean {
  return (
    step.title.trim().toLowerCase() === "respond"
    && /^respond(?:-\d+)?$/.test(step.id.trim().toLowerCase())
  );
}

export function getVisibleTaskPlanSteps(plan: AiTaskPlan): AiTaskStep[] {
  return plan.steps.filter(
    (step) => !step.protocolPrivate && !isImplicitRespondStep(step),
  );
}

export function getTaskPlanProgress(plan: AiTaskPlan) {
  const steps = getVisibleTaskPlanSteps(plan);
  const total = steps.length;
  const completed = steps.filter((step) => step.status === "done").length;
  const runningSteps = steps.filter((step) => step.status === "running");
  const currentStep =
    runningSteps[0] ||
    steps.find(
      (step) => step.status === "blocked" || step.status === "failed",
    ) ||
    steps.find((step) => step.status === "pending");
  return {
    total,
    completed,
    runningSteps,
    currentStep,
    percent: total > 0 ? Math.round((completed / total) * 100) : 0,
  };
}

export function getTaskPlanCountLabel(plan: AiTaskPlan): string {
  const { completed, total, runningSteps } = getTaskPlanProgress(plan);
  const active = plan.status === "planned" || plan.status === "running";
  return active && runningSteps.length > 1
    ? `并行 ${runningSteps.length} 项 · 已完成 ${completed}/${total}`
    : `已完成 ${completed}/${total}`;
}

export function shouldShowTaskPlan(plan: AiTaskPlan): boolean {
  return getVisibleTaskPlanSteps(plan).length >= MIN_VISIBLE_TASK_PLAN_STEPS;
}

/**
 * The header reflects only the assistant message for the turn currently
 * streaming. Looking further back can leak a completed plan from a prior turn
 * while the new assistant placeholder is still waiting for its first chunks.
 */
export function getActiveTaskPlan(
  conversations: ChatMessage[],
  loading: boolean,
): AiTaskPlan | undefined {
  if (!loading) return undefined;
  const currentMessage = conversations[conversations.length - 1];
  if (currentMessage?.role !== "assistant") return undefined;
  const plan = currentMessage.taskPlan;
  return plan && shouldShowTaskPlan(plan) ? plan : undefined;
}
