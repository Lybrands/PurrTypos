import type { AiTaskPlan, AiTaskStep } from "./chat.types";

function cloneSteps(plan: AiTaskPlan): AiTaskStep[] {
  return plan.steps.map((step) => ({ ...step }));
}

function firstStepIndex(
  steps: AiTaskStep[],
  predicate: (step: AiTaskStep) => boolean,
): number {
  return steps.findIndex(
    (step) =>
      step.status !== "done" &&
      step.status !== "failed" &&
      predicate(step),
  );
}

function firstRunnableIndex(steps: AiTaskStep[]): number {
  return firstStepIndex(steps, (step) => step.type !== "confirm");
}

function markNextRunnable(steps: AiTaskStep[]): void {
  if (steps.some((step) => step.status === "running")) return;
  const idx = firstRunnableIndex(steps);
  if (idx >= 0) {
    steps[idx] = { ...steps[idx], status: "running" };
  }
}

function doneRunningSteps(
  steps: AiTaskStep[],
  predicate: (step: AiTaskStep) => boolean,
  resultSummary: string,
): boolean {
  let changed = false;
  for (let i = 0; i < steps.length; i += 1) {
    const step = steps[i];
    if (step.status === "running" && predicate(step)) {
      steps[i] = {
        ...step,
        status: "done",
        resultSummary: step.resultSummary || resultSummary,
      };
      changed = true;
    }
  }
  return changed;
}

export function startTaskPlanRun(plan: AiTaskPlan): AiTaskPlan {
  const steps: AiTaskStep[] = cloneSteps(plan).map((step) => ({
    ...step,
    status: step.status === "done" ? step.status : ("pending" as const),
    resultSummary: step.resultSummary,
  }));
  markNextRunnable(steps);
  return {
    ...plan,
    status: steps.some((step) => step.status === "running")
      ? "running"
      : "done",
    steps,
  };
}

export function markTaskPlanToolRunning(plan: AiTaskPlan | undefined): AiTaskPlan | undefined {
  if (!plan) return plan;
  const steps = cloneSteps(plan);
  const hasRunningTool = steps.some(
    (step) =>
      step.status === "running" &&
      (step.executor === "tool" || step.type === "read"),
  );
  if (!hasRunningTool) {
    const idx = firstStepIndex(
      steps,
      (step) => step.executor === "tool" || step.type === "read",
    );
    if (idx >= 0) {
      steps[idx] = { ...steps[idx], status: "running" };
    }
  }
  return { ...plan, status: "running", steps };
}

export function completeTaskPlanToolStep(plan: AiTaskPlan | undefined): AiTaskPlan | undefined {
  if (!plan) return plan;
  const steps = cloneSteps(plan);
  const changed = doneRunningSteps(
    steps,
    (step) => step.executor === "tool" || step.type === "read",
    "已完成相关上下文读取。",
  );
  if (changed) markNextRunnable(steps);
  return { ...plan, status: "running", steps };
}

export function markTaskPlanModelRunning(plan: AiTaskPlan | undefined): AiTaskPlan | undefined {
  if (!plan) return plan;
  const steps = cloneSteps(plan);
  doneRunningSteps(
    steps,
    (step) => step.executor === "tool" || step.type === "read",
    "已完成相关上下文读取。",
  );
  const hasRunningModel = steps.some(
    (step) =>
      step.status === "running" &&
      step.type !== "read" &&
      step.type !== "confirm",
  );
  if (!hasRunningModel) {
    const idx = firstStepIndex(
      steps,
      (step) => step.type !== "read" && step.type !== "confirm",
    );
    if (idx >= 0) {
      steps[idx] = { ...steps[idx], status: "running" };
    }
  }
  return { ...plan, status: "running", steps };
}

export function finishTaskPlanRun(plan: AiTaskPlan | undefined): AiTaskPlan | undefined {
  if (!plan) return plan;
  const steps = cloneSteps(plan).map((step) => {
    if (step.status === "running") {
      return {
        ...step,
        status: "done" as const,
        resultSummary: step.resultSummary || "本轮已完成该步骤。",
      };
    }
    if (step.status === "pending" && step.type === "confirm") {
      return {
        ...step,
        status: "blocked" as const,
        resultSummary: step.resultSummary || "等待你确认提案或 diff 后再写入。",
      };
    }
    if (step.status === "pending") {
      return {
        ...step,
        status: "done" as const,
        resultSummary: step.resultSummary || "本轮已覆盖该步骤。",
      };
    }
    return step;
  });
  const blocked = steps.some((step) => step.status === "blocked");
  const failed = steps.some((step) => step.status === "failed");
  return {
    ...plan,
    status: failed ? "failed" : blocked ? "blocked" : "done",
    steps,
  };
}

export function failTaskPlanRun(
  plan: AiTaskPlan | undefined,
  error: string,
): AiTaskPlan | undefined {
  if (!plan) return plan;
  const steps = cloneSteps(plan).map((step) => {
    if (step.status === "running") {
      return {
        ...step,
        status: "failed" as const,
        error: step.error || error,
      };
    }
    return step;
  });
  return {
    ...plan,
    status: "failed",
    steps,
  };
}
