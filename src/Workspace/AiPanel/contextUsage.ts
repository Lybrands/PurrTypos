import type { ChatMessage } from "./hooks/chat.types";

export interface ContextUsage {
  usedTokens: number;
  windowTokens: number;
  ratio: number;
}

function latestActualInputTokens(
  messages: ChatMessage[],
  windowTokens: number,
): number | null {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const budget = messages[index].contextBudget;
    const actualInputTokens = budget?.actualInputTokens;
    if (
      budget?.windowTokens === windowTokens
      && typeof actualInputTokens === "number"
      && Number.isFinite(actualInputTokens)
      && actualInputTokens >= 0
    ) {
      return Math.round(actualInputTokens);
    }
  }
  return null;
}

export function calculateContextUsage(params: {
  messages: ChatMessage[];
  windowTokens: number;
}): ContextUsage | null {
  const windowTokens = Math.max(1, Math.round(params.windowTokens));
  const usedTokens = latestActualInputTokens(params.messages, windowTokens);
  if (usedTokens === null) return null;
  return {
    usedTokens,
    windowTokens,
    ratio: usedTokens / windowTokens,
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
