import type { ChatMessage } from "./chat.types";
import type {
  ChatSessionActivity,
  QueuedChatSubmission,
} from "./chatQueue";

export interface ChatSessionRuntime {
  sessionId: number;
  messages: ChatMessage[];
  loading: boolean;
  activity?: ChatSessionActivity;
  streamId?: string;
  updatedAt: number;
}

const runtimes = new Map<number, ChatSessionRuntime>();
let queuedSubmissions: QueuedChatSubmission[] = [];
const listeners = new Set<() => void>();
let version = 0;

function emitChange(): void {
  version += 1;
  listeners.forEach((listener) => listener());
}

export function subscribeChatRuntime(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function getChatRuntimeVersion(): number {
  return version;
}

export function getChatSessionRuntime(
  sessionId: number | null | undefined,
): ChatSessionRuntime | undefined {
  return sessionId == null ? undefined : runtimes.get(sessionId);
}

export function replaceChatRuntimeMessages(
  sessionId: number,
  messages: ChatMessage[],
): void {
  const current = runtimes.get(sessionId);
  runtimes.set(sessionId, {
    sessionId,
    messages,
    loading: current?.loading ?? false,
    activity: current?.activity,
    streamId: current?.streamId,
    updatedAt: Date.now(),
  });
  emitChange();
}

export function updateChatRuntimeMessages(
  sessionId: number,
  updater: (messages: ChatMessage[]) => ChatMessage[],
): void {
  const current = runtimes.get(sessionId);
  if (!current) return;
  const messages = updater(current.messages);
  if (messages === current.messages) return;
  runtimes.set(sessionId, {
    ...current,
    messages,
    updatedAt: Date.now(),
  });
  emitChange();
}

export function setChatRuntimeLoading(
  sessionId: number,
  next: boolean | ((current: boolean) => boolean),
): void {
  const current = runtimes.get(sessionId);
  if (!current) return;
  const loading =
    typeof next === "function" ? next(current.loading) : next;
  if (loading === current.loading) return;
  runtimes.set(sessionId, {
    ...current,
    loading,
    updatedAt: Date.now(),
  });
  emitChange();
}

export function setChatRuntimeActivity(
  sessionId: number,
  activity: ChatSessionActivity,
): void {
  const current = runtimes.get(sessionId);
  if (!current) return;
  runtimes.set(sessionId, {
    ...current,
    activity,
    updatedAt: Date.now(),
  });
  emitChange();
}

export function setChatRuntimeStreamId(
  sessionId: number,
  streamId: string | undefined,
): void {
  const current = runtimes.get(sessionId);
  if (!current) return;
  runtimes.set(sessionId, {
    ...current,
    streamId,
    updatedAt: Date.now(),
  });
  emitChange();
}

export function getChatSessionActivities(): Record<
  number,
  ChatSessionActivity
> {
  const activities: Record<number, ChatSessionActivity> = {};
  runtimes.forEach((runtime, sessionId) => {
    if (runtime.activity) activities[sessionId] = runtime.activity;
  });
  return activities;
}

export function getChatRuntimeQueue(): QueuedChatSubmission[] {
  return queuedSubmissions;
}

export function replaceChatRuntimeQueue(
  queue: QueuedChatSubmission[],
): void {
  queuedSubmissions = queue;
  emitChange();
}

export function clearChatRuntime(sessionId: number): void {
  const runtimeDeleted = runtimes.delete(sessionId);
  const nextQueue = queuedSubmissions.filter(
    (submission) => submission.sessionId !== sessionId,
  );
  const queueChanged = nextQueue.length !== queuedSubmissions.length;
  queuedSubmissions = nextQueue;
  if (!runtimeDeleted && !queueChanged) return;
  emitChange();
}
