import type { AiContextBudgetState } from "../../types";
import type { ChatMessage } from "./hooks/chat.types";

export type ContextUsageSource = "provider" | "estimate";

export interface ContextUsage {
  usedTokens: number;
  windowTokens: number;
  inputCapacityTokens: number;
  outputReserveTokens: number;
  ratio: number;
  /** provider 表示估算基线已由最近一次供应商输入量校准。 */
  source: ContextUsageSource;
}

function nonNegativeInteger(value: unknown): number | null {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) && number >= 0
    ? Math.round(number)
    : null;
}

function normalizeIdentity(value: unknown): string {
  return String(value ?? "").trim().toLowerCase();
}

function matchesSelectedModel(params: {
  message: ChatMessage;
  budget: AiContextBudgetState;
  modelConfigId?: string;
  modelName?: string;
}): boolean {
  const expectedConfigId = normalizeIdentity(params.modelConfigId);
  const budgetConfigId = normalizeIdentity(params.budget.modelConfigId);
  if (expectedConfigId && budgetConfigId && expectedConfigId !== budgetConfigId) {
    return false;
  }

  const expectedModelName = normalizeIdentity(params.modelName);
  const budgetModelName = normalizeIdentity(
    params.budget.modelName || params.message.model,
  );
  if (expectedModelName && budgetModelName && expectedModelName !== budgetModelName) {
    return false;
  }

  if (expectedConfigId && !budgetConfigId && !budgetModelName) return false;
  if (expectedModelName && !budgetModelName && !budgetConfigId) return false;
  return true;
}

function estimateUnits(value: string, asciiDivisor: number): number {
  let asciiCount = 0;
  let nonAsciiCount = 0;
  for (const character of value) {
    if (character.codePointAt(0)! < 128) asciiCount += 1;
    else nonAsciiCount += 1;
  }
  return nonAsciiCount + Math.ceil(asciiCount / Math.max(1, asciiDivisor));
}

function estimateMessageTokens(
  message: Pick<ChatMessage, "role" | "content" | "streamingContent">,
): number {
  const content = String(message.content || message.streamingContent || "").trim();
  if (!content) return 0;
  const encoded = JSON.stringify({ role: message.role, content });
  return estimateUnits(encoded, 2) + 4;
}

function estimateConversationTokens(messages: ChatMessage[]): number {
  return messages.reduce(
    (total, message) => total + estimateMessageTokens(message),
    0,
  );
}

function latestBudgetSnapshot(messages: ChatMessage[]): {
  index: number;
  message: ChatMessage;
  budget: AiContextBudgetState;
  baseTokens: number;
  providerCalibrated: boolean;
} | null {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    const budget = message.contextBudget;
    if (!budget) continue;
    const actualInputTokens = nonNegativeInteger(budget.actualInputTokens);
    const estimatedInputTokens = nonNegativeInteger(budget.estimatedInputTokens);
    const toolSchemaTokens = nonNegativeInteger(budget.toolSchemaTokens) ?? 0;
    const baseTokens = actualInputTokens
      ?? (estimatedInputTokens === null ? null : estimatedInputTokens + toolSchemaTokens);
    if (baseTokens === null) continue;
    return {
      index,
      message,
      budget,
      baseTokens,
      providerCalibrated: actualInputTokens !== null,
    };
  }
  return null;
}

export function calculateContextUsage(params: {
  messages: ChatMessage[];
  windowTokens: number;
  modelConfigId?: string;
  modelName?: string;
}): ContextUsage {
  const windowTokens = Math.max(1, Math.round(params.windowTokens));
  const snapshot = latestBudgetSnapshot(params.messages);
  const selectedModelMatchesSnapshot = snapshot !== null && matchesSelectedModel({
    message: snapshot.message,
    budget: snapshot.budget,
    modelConfigId: params.modelConfigId,
    modelName: params.modelName,
  });
  const snapshotWindowTokens = snapshot
    ? nonNegativeInteger(snapshot.budget.windowTokens)
    : null;
  const canReuseOutputReserve = selectedModelMatchesSnapshot
    && snapshotWindowTokens === windowTokens;
  const outputReserveTokens = canReuseOutputReserve
    ? Math.min(
        windowTokens - 1,
        nonNegativeInteger(snapshot?.budget.outputReserveTokens) ?? 0,
      )
    : 0;
  const inputCapacityTokens = Math.max(1, windowTokens - outputReserveTokens);
  const visibleConversationTokens = estimateConversationTokens(params.messages);
  const snapshotBasedTokens = snapshot === null
    ? 0
    : snapshot.baseTokens
      + estimateConversationTokens(params.messages.slice(snapshot.index));
  const usedTokens = Math.max(visibleConversationTokens, snapshotBasedTokens);
  const providerCalibrated = snapshot?.providerCalibrated === true
    && selectedModelMatchesSnapshot;

  return {
    usedTokens,
    windowTokens,
    inputCapacityTokens,
    outputReserveTokens,
    ratio: usedTokens / windowTokens,
    source: providerCalibrated ? "provider" : "estimate",
  };
}

export function formatContextTokens(value: number): string {
  if (value >= 1_000_000) {
    return `${(value / 1_000_000).toFixed(value >= 10_000_000 ? 0 : 1)}M`;
  }
  if (value >= 1_000) {
    return `${(value / 1_000).toFixed(value >= 100_000 ? 0 : 1)}K`;
  }
  return String(Math.max(0, Math.round(value)));
}
