import type { AiModelConfig, EntityId, WritingMethodOverrides } from "../../../types";

export type ChatRunOutcome = "completed" | "paused" | "failed" | "canceled";

export interface QueuedChatSubmission {
  content: string;
  sessionId: number;
  bookId: EntityId;
  chapterId: EntityId | null;
  sessionScope: 'chapter' | 'setting';
  currentChapterTitle?: string;
  locale: string;
  needsTitle: boolean;
  selectedModel: string;
  selectedModelConfig: AiModelConfig;
  agentEnabled: boolean;
  associatedChapterIds: EntityId[];
  associatedOutlineIds: EntityId[];
  selectedLongTermMemoryIds: string[];
  selectedMemoryIds: (number | string)[];
  selectedForeshadowingIds: (number | string)[];
  writingMethodOverrides: WritingMethodOverrides;
}

export type ChatSessionActivityState =
  | "running"
  | "paused"
  | "queued"
  | ChatRunOutcome;

export interface ChatSessionActivity {
  state: ChatSessionActivityState;
  queuedCount: number;
}

export function countQueuedForSession(
  queue: QueuedChatSubmission[],
  sessionId: number,
): number {
  return queue.filter((item) => item.sessionId === sessionId).length;
}

export function getSettledSessionActivity(
  outcome: ChatRunOutcome,
  queuedCount: number,
): ChatSessionActivity {
  if (queuedCount > 0) {
    return { state: "queued", queuedCount };
  }
  return { state: outcome, queuedCount: 0 };
}
