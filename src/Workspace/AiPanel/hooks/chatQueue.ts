import type { AiModelConfig, EntityId } from "../../../types";

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
  selectedMemoryIds: (number | string)[];
  selectedForeshadowingIds: (number | string)[];
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

export function getSessionActivityLabel(
  activity: ChatSessionActivity,
): string {
  if (activity.state === "running") {
    return activity.queuedCount > 0
      ? `生成中 · ${activity.queuedCount} 条排队`
      : "生成中";
  }
  if (activity.state === "queued") {
    return `等待发送 · ${activity.queuedCount} 条`;
  }
  if (activity.state === "paused") return "已暂停";
  if (activity.state === "completed") return "已完成";
  if (activity.state === "failed") return "生成失败";
  return "已终止";
}
