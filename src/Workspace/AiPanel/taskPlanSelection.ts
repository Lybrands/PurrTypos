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
  return plan.steps.filter((step) => !isImplicitRespondStep(step));
}

/**
 * Keep completion progress and the active step ordinal separate.
 *
 * While step 3 is running only two steps are complete. The progress bar
 * should therefore remain at 2/total, but a current-step badge must display
 * 3/total instead of presenting the completed count as the current step.
 */
export function getTaskPlanProgress(plan: AiTaskPlan) {
  const steps = getVisibleTaskPlanSteps(plan);
  const total = steps.length;
  const completed = steps.filter((step) => step.status === "done").length;
  const currentStep =
    steps.find((step) => step.status === "running") ||
    steps.find(
      (step) => step.status === "blocked" || step.status === "failed",
    ) ||
    steps.find((step) => step.status === "pending");
  const currentStepIndex = currentStep ? steps.indexOf(currentStep) : -1;
  return {
    total,
    completed,
    currentStep,
    currentStepNumber: currentStepIndex >= 0 ? currentStepIndex + 1 : null,
    percent: total > 0 ? Math.round((completed / total) * 100) : 0,
  };
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
