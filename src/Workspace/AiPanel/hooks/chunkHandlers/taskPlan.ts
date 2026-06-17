import type { AiTaskPlan, ChatMessage } from "../chat.types";
import { startTaskPlanRun } from "../taskPlanRun";
import type { ChunkHandler } from "./types";

function isTaskPlanLike(value: unknown): value is AiTaskPlan {
  if (!value || typeof value !== "object") return false;
  const plan = value as Partial<AiTaskPlan>;
  return (
    typeof plan.title === "string" &&
    typeof plan.status === "string" &&
    Array.isArray(plan.steps)
  );
}

export const handleTaskPlan: ChunkHandler = (chunk, ctx) => {
  const taskPlan = (chunk as { taskPlan?: unknown }).taskPlan;
  if (!isTaskPlanLike(taskPlan)) return;
  const runningPlan = startTaskPlanRun(taskPlan);
  ctx.acc.taskPlan = runningPlan;
  if (!ctx.isVisibleSession()) return;

  ctx.scheduleCommit((prev) => {
    const next = [...prev];
    const last = next[next.length - 1];
    if (!last || last.role !== "assistant") return prev;
    next[next.length - 1] = {
      ...(last as ChatMessage),
      taskPlan: runningPlan,
    };
    return next;
  });
};
