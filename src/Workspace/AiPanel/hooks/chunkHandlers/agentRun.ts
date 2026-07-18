import type { AiAgentDelegation } from "../../../../types";
import type { AiTaskPlan, AiTaskStep, ChatMessage } from "../chat.types";
import type { ChunkHandler } from "./types";

function updateLastAssistant(
  ctx: Parameters<ChunkHandler>[1],
  updater: (message: ChatMessage) => ChatMessage,
): void {
  if (!ctx.isVisibleSession()) return;
  ctx.scheduleCommit((prev) => {
    const next = [...prev];
    const last = next[next.length - 1];
    if (!last || last.role !== "assistant") return prev;
    next[next.length - 1] = updater(last as ChatMessage);
    return next;
  });
}

function normalizePlan(payload: unknown): (AiTaskPlan & { runId?: string }) | null {
  if (!payload || typeof payload !== "object") return null;
  const raw = payload as {
    runId?: unknown;
    title?: unknown;
    goal?: unknown;
    status?: unknown;
    steps?: unknown;
  };
  if (!Array.isArray(raw.steps)) return null;
  return {
    runId: typeof raw.runId === "string" ? raw.runId : undefined,
    title: typeof raw.title === "string" && raw.title.trim() ? raw.title : "To-dos",
    goal: typeof raw.goal === "string" ? raw.goal : undefined,
    status: normalizePlanStatus(raw.status),
    steps: raw.steps.map(normalizeStep).filter((x): x is AiTaskStep => Boolean(x)),
  };
}

function normalizeStep(raw: unknown): AiTaskStep | null {
  if (!raw || typeof raw !== "object") return null;
  const step = raw as Partial<AiTaskStep>;
  if (!step.id || !step.title) return null;
  return {
    ...step,
    id: String(step.id),
    title: String(step.title),
    type: step.type || "analyze",
    status: step.status || "pending",
  };
}

function normalizePlanStatus(status: unknown): AiTaskPlan["status"] {
  const value = String(status || "running");
  if (
    value === "planned" ||
    value === "running" ||
    value === "paused" ||
    value === "done" ||
    value === "blocked" ||
    value === "failed" ||
    value === "canceled"
  ) {
    return value;
  }
  return "running";
}

export const handleAgentRunStarted: ChunkHandler = (chunk, ctx) => {
  const started = chunk.agentRunStarted;
  if (!started?.runId) return;
  ctx.acc.agentRunId = started.runId;
  updateLastAssistant(ctx, (message) => ({
    ...message,
    agentRunId: started.runId,
  }));
};

export const handleAgentRunTodosUpdated: ChunkHandler = (chunk, ctx) => {
  const plan = normalizePlan(chunk.agentRunTodosUpdated);
  if (!plan) return;
  const { runId, ...taskPlan } = plan;
  ctx.acc.agentRunId = runId || ctx.acc.agentRunId;
  ctx.acc.taskPlan = taskPlan;
  updateLastAssistant(ctx, (message) => ({
    ...message,
    agentRunId: runId || message.agentRunId,
    taskPlan,
  }));
};

export const handleAgentRunTodoUpdated: ChunkHandler = (chunk, ctx) => {
  const updated = chunk.agentRunTodoUpdated;
  if (!updated?.stepId || !updated.step) return;
  const nextStep = normalizeStep(updated.step);
  if (!nextStep) return;
  const apply = (plan: AiTaskPlan | undefined): AiTaskPlan | undefined => {
    if (!plan) return plan;
    return {
      ...plan,
      status: normalizePlanStatus(updated.status || plan.status),
      steps: plan.steps.map((step) =>
        step.id === updated.stepId ? { ...step, ...nextStep } : step,
      ),
    };
  };
  ctx.acc.agentRunId = updated.runId || ctx.acc.agentRunId;
  ctx.acc.taskPlan = apply(ctx.acc.taskPlan);
  updateLastAssistant(ctx, (message) => ({
    ...message,
    agentRunId: updated.runId || message.agentRunId,
    taskPlan: apply(message.taskPlan),
  }));
};

export const handleAgentRunTerminal: ChunkHandler = (chunk, ctx) => {
  const terminal =
    chunk.agentRunCompleted || chunk.agentRunFailed || chunk.agentRunBlocked || chunk.agentRunCanceled;
  if (!terminal?.runId) return;
  const status = normalizePlanStatus(terminal.status);
  ctx.acc.agentRunId = terminal.runId;
  if (ctx.acc.taskPlan) {
    ctx.acc.taskPlan = { ...ctx.acc.taskPlan, status };
  }
  updateLastAssistant(ctx, (message) => ({
    ...message,
    agentRunId: terminal.runId,
    taskPlan: message.taskPlan ? { ...message.taskPlan, status } : message.taskPlan,
  }));
};

function normalizeDelegation(payload: unknown): AiAgentDelegation | null {
  if (!payload || typeof payload !== "object") return null;
  const raw = payload as Partial<AiAgentDelegation>;
  const delegationId = String(raw.delegationId || "").trim();
  const agentRole = String(raw.agentRole || "").trim();
  if (!delegationId || !agentRole) return null;
  return {
    delegationId,
    parentRunId: String(raw.parentRunId || ""),
    rootRunId: String(raw.rootRunId || raw.parentRunId || ""),
    childRunId: raw.childRunId || null,
    agentRole,
    agentTitle: raw.agentTitle || null,
    objective: String(raw.objective || ""),
    status: raw.status || "queued",
    required: raw.required !== false,
    priority: Number(raw.priority || 0),
    resultSummary: raw.resultSummary || null,
    error: raw.error || null,
  };
}

function upsertDelegation(
  items: AiAgentDelegation[] | undefined,
  next: AiAgentDelegation,
): AiAgentDelegation[] {
  const current = items ?? [];
  const index = current.findIndex(
    (item) => item.delegationId === next.delegationId,
  );
  if (index < 0) return [...current, next];
  return current.map((item, itemIndex) =>
    itemIndex === index ? { ...item, ...next } : item,
  );
}

export const handleAgentDelegation: ChunkHandler = (chunk, ctx) => {
  const payload =
    chunk.agentDelegationCreated || chunk.agentDelegationUpdated;
  const delegation = normalizeDelegation(payload);
  if (!delegation) return;
  const runId = payload?.runId;
  ctx.acc.agentRunId = runId || ctx.acc.agentRunId;
  ctx.acc.delegations = upsertDelegation(ctx.acc.delegations, delegation);
  updateLastAssistant(ctx, (message) => ({
    ...message,
    agentRunId: runId || message.agentRunId,
    delegations: upsertDelegation(message.delegations, delegation),
  }));
};
