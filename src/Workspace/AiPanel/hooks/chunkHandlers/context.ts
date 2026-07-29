import type {
  AiContextBudgetState,
  AiContextCompactionState,
} from "../../../../types";
import type { ChatMessage } from "../chat.types";
import type { ChunkHandler } from "./types";

function updateLastAssistant(
  ctx: Parameters<ChunkHandler>[1],
  patch: Partial<ChatMessage>,
): void {
  if (!ctx.isVisibleSession()) return;
  ctx.scheduleCommit((prev) => {
    const next = [...prev];
    const last = next.at(-1);
    if (!last || last.role !== "assistant") return prev;
    next[next.length - 1] = { ...last, ...patch };
    return next;
  });
}

export const handleContextCompaction: ChunkHandler = (chunk, ctx) => {
  const value = chunk.contextCompaction;
  if (!value) return;
  const state: AiContextCompactionState = {
    status: value.status,
    outcome: value.outcome,
    selectedTurnCount: value.selectedTurnCount,
    compactedTurnCount: value.compactedTurnCount,
    retainedRawTurnCount: value.retainedRawTurnCount,
    previousSummaryVersion: value.previousSummaryVersion,
    summaryVersion: value.summaryVersion,
  };
  ctx.acc.contextCompaction = state;
  updateLastAssistant(ctx, { contextCompaction: state });
};

export const handleContextBudget: ChunkHandler = (chunk, ctx) => {
  if (!chunk.contextBudget) return;
  if (
    !ctx.acc.contextBudget
    && chunk.contextBudget.windowTokens === undefined
  ) {
    return;
  }
  const budget = {
    ...(ctx.acc.contextBudget ?? {}),
    ...chunk.contextBudget,
  } as AiContextBudgetState;
  ctx.acc.contextBudget = budget;
  updateLastAssistant(ctx, { contextBudget: budget });
};
