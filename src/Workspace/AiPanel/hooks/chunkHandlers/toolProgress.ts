import type { ChatMessage } from "../chat.types";
import type { ChunkHandler } from "./types";

/**
 * 注意：原实现里"不可见会话仍可能调用 setConversations"是一个微妙行为，
 * 在切换会话过程中会把缓存标记意外写到当前打开会话的最后一条消息。本次重构保留原语义，
 * 仅做形式上的拆分，避免任何行为漂移；如需修复请单独提 PR。
 */

const onlyCacheLikeChunk = (chunk: {
  delta?: string;
  thinkingDelta?: string;
  done?: boolean;
  error?: string;
}): boolean =>
  !chunk.delta && !chunk.thinkingDelta && !chunk.done && !chunk.error;

export const handleToolReadCacheMask: ChunkHandler = (chunk, ctx) => {
  const mask = chunk.toolReadCacheMask;
  if (!Array.isArray(mask) || mask.length === 0) return;

  const onlySelf =
    onlyCacheLikeChunk(chunk) && typeof chunk.toolIndexCompleted !== "number";

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
    const n = lastSeg.labels.length;
    const flags = [
      ...(lastSeg.cachedFlags ?? new Array(n).fill(false)),
    ];
    const m = Math.min(n, mask.length);
    for (let i = 0; i < m; i++) {
      if (mask[i]) flags[i] = true;
    }
    const nextSegs = [
      ...segs.slice(0, -1),
      { ...lastSeg, cachedFlags: flags },
    ];
    next[next.length - 1] = {
      ...(lastMsg as ChatMessage),
      toolCallSegments: nextSegs,
    };
    return next;
  });

  if (onlySelf) return true;
};

export const handleToolIndexCompleted: ChunkHandler = (chunk, ctx) => {
  if (typeof chunk.toolIndexCompleted !== "number") return;
  const idx = chunk.toolIndexCompleted;
  const fromCache = chunk.toolFromCache === true;
  const onlySelf = onlyCacheLikeChunk(chunk);

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
    const n = lastSeg.labels.length;
    const nextCount = Math.min(idx + 1, n);
    const flags = [
      ...(lastSeg.cachedFlags ?? new Array(n).fill(false)),
    ];
    if (fromCache && idx >= 0 && idx < flags.length) flags[idx] = true;
    const nextSegs = [
      ...segs.slice(0, -1),
      {
        ...lastSeg,
        completedToolCount: nextCount,
        cachedFlags: flags,
      },
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

export const handleToolCallCachedIndex: ChunkHandler = (chunk, ctx) => {
  if (typeof chunk.toolCallCachedIndex !== "number") return;
  const idx = chunk.toolCallCachedIndex;
  const onlySelf = onlyCacheLikeChunk(chunk);

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
    const flags = [
      ...(lastSeg.cachedFlags ?? new Array(lastSeg.labels.length).fill(false)),
    ];
    if (idx >= 0 && idx < flags.length) flags[idx] = true;
    const nextSegs = [
      ...segs.slice(0, -1),
      { ...lastSeg, cachedFlags: flags },
    ];
    next[next.length - 1] = {
      ...(lastMsg as ChatMessage),
      toolCallSegments: nextSegs,
    };
    return next;
  });

  if (onlySelf) return true;
};
