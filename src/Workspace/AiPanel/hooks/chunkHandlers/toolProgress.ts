import type { ChatMessage } from "../chat.types";
import type { ChunkHandler } from "./types";

const onlyCacheLikeChunk = (chunk: {
  delta?: string;
  thinkingDelta?: string;
  done?: boolean;
  error?: string;
}): boolean =>
  !chunk.delta && !chunk.thinkingDelta && !chunk.done && !chunk.error;

export const handleToolIndexCompleted: ChunkHandler = (chunk, ctx) => {
  if (typeof chunk.toolIndexCompleted !== "number") return;
  const idx = chunk.toolIndexCompleted;
  const fromCache = chunk.toolFromCache === true;
  const onlySelf = onlyCacheLikeChunk(chunk);

  const completeSegment = <T extends {
    labels: string[];
    cachedFlags?: boolean[];
    completedToolCount?: number;
    startedAt?: number;
    durationMs?: number;
  }>(segment: T): T => {
    const n = segment.labels.length;
    const nextCount = Math.min(idx + 1, n);
    const flags = [...(segment.cachedFlags ?? new Array(n).fill(false))];
    if (fromCache && idx >= 0 && idx < flags.length) flags[idx] = true;
    const finished = nextCount >= n;
    return {
      ...segment,
      completedToolCount: nextCount,
      cachedFlags: flags,
      ...(finished && segment.startedAt != null
        ? {
            durationMs: Math.max(0, Math.round(performance.now() - segment.startedAt)),
            startedAt: undefined,
          }
        : {}),
    };
  };

  const accSegments = ctx.acc.toolCallSegments ?? [];
  if (accSegments.length > 0) {
    ctx.acc.toolCallSegments = [
      ...accSegments.slice(0, -1),
      completeSegment(accSegments[accSegments.length - 1]),
    ];
  }

  if (!ctx.isVisibleSession() && onlySelf) {
    return true;
  }

  ctx.scheduleCommit((prev) => {
    const next = [...prev];
    const lastMsg = next[next.length - 1];
    if (lastMsg?.role !== "assistant") return prev;
    const segs = (lastMsg as ChatMessage).toolCallSegments ?? [];
    if (segs.length === 0) return prev;
    const lastSeg = segs[segs.length - 1];
    const nextSegs = [
      ...segs.slice(0, -1),
      completeSegment(lastSeg),
    ];
    next[next.length - 1] = {
      ...(lastMsg as ChatMessage),
      toolCallSegments: nextSegs,
      taskPlan: ctx.acc.taskPlan ?? (lastMsg as ChatMessage).taskPlan,
    };
    return next;
  });

  if (onlySelf) return true;
};
