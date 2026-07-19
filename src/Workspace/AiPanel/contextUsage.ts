import type { AiContextBudgetState } from "../../types";
import type { ChatMessage } from "./hooks/chat.types";

export interface ContextUsage {
  usedTokens: number;
  windowTokens: number;
  ratio: number;
  source: "backend" | "estimate";
}

export function estimateConversationTextTokens(value: unknown): number {
  const text = String(value ?? "");
  let ascii = 0;
  for (const character of text) {
    if (character.codePointAt(0)! < 128) ascii += 1;
  }
  return text.length - ascii + Math.ceil(ascii / 4);
}

function messageText(message: ChatMessage): string {
  if (message.isError || message.role === "system") return "";
  if (message.content?.trim()) return message.content;
  if (message.role !== "assistant") return "";
  return (message.toolCallSegments ?? [])
    .flatMap((segment) => [segment.textBefore, ...segment.labels])
    .filter(Boolean)
    .join("\n");
}

function estimatedMessages(messages: ChatMessage[]): number {
  return messages.reduce((total, message) => {
    const text = messageText(message);
    return text ? total + estimateConversationTextTokens(text) + 6 : total;
  }, 0);
}

function latestBackendBudget(
  messages: ChatMessage[],
): { index: number; budget: AiContextBudgetState } | null {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const budget = messages[index].contextBudget;
    if (budget) return { index, budget };
  }
  return null;
}

export function calculateContextUsage(params: {
  messages: ChatMessage[];
  prompt: string;
  windowTokens: number;
  loading?: boolean;
}): ContextUsage {
  const windowTokens = Math.max(1, Math.round(params.windowTokens));
  const latestCandidate = latestBackendBudget(params.messages);
  const latest =
    latestCandidate?.budget.windowTokens === windowTokens
      ? latestCandidate
      : null;
  let usedTokens: number;
  let source: ContextUsage["source"];

  if (latest) {
    usedTokens =
      latest.budget.estimatedInputTokens + latest.budget.toolSchemaTokens;
    const budgetBelongsToActiveTurn =
      Boolean(params.loading) && latest.index === params.messages.length - 1;
    if (!budgetBelongsToActiveTurn) {
      usedTokens += estimatedMessages(params.messages.slice(latest.index));
      usedTokens += estimateConversationTextTokens(params.prompt);
    }
    source = "backend";
  } else {
    usedTokens =
      estimatedMessages(params.messages) +
      estimateConversationTextTokens(params.prompt);
    source = "estimate";
  }

  usedTokens = Math.max(0, Math.round(usedTokens));
  return {
    usedTokens,
    windowTokens,
    ratio: usedTokens / windowTokens,
    source,
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
