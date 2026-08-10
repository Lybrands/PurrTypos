import type { ChunkHandler } from "./types";

export function ensureCommentaryBlockStarted(
  ctx: Parameters<ChunkHandler>[1],
): number {
  if (ctx.acc.commentaryBlockStartedAt == null) {
    ctx.acc.commentaryBlockStartedAt = performance.now();
  }
  return ctx.acc.commentaryBlockStartedAt;
}

export function finalizeCommentaryBlock(
  ctx: Parameters<ChunkHandler>[1],
  blockText: string,
): { blocks: string[]; durations: number[] } {
  const blocks = [...(ctx.acc.commentaryBlocks ?? [])];
  const durations = [...(ctx.acc.commentaryDurationsMs ?? [])];
  const trimmed = blockText.trim();
  if (trimmed) {
    blocks.push(trimmed);
    const started = ctx.acc.commentaryBlockStartedAt;
    durations.push(
      started != null ? Math.max(0, Math.round(performance.now() - started)) : 0,
    );
  }
  ctx.acc.commentaryBlocks = blocks;
  ctx.acc.commentaryDurationsMs = durations;
  ctx.acc.commentaryBlockStartedAt = undefined;
  return { blocks, durations };
}

export const handleCommentaryDelta: ChunkHandler = (chunk, ctx) => {
  if (!chunk.commentaryDelta) return;
  const commentaryStartedAt = ensureCommentaryBlockStarted(ctx);
  const delta = chunk.commentaryDelta;
  ctx.acc.commentary += delta;
  if (!ctx.isVisibleSession()) return;
  ctx.scheduleCommit((prev) => {
    const next = [...prev];
    const last = next[next.length - 1];
    if (!last || last.role !== "assistant") return prev;
    next[next.length - 1] = {
      ...last,
      commentary: (last.commentary || "") + delta,
      commentaryStartedAt,
      toolCalling: false,
    };
    return next;
  });
};

export const handleDelta: ChunkHandler = (chunk, ctx) => {
  if (!chunk.delta) return;
  // `delta` is a candidate final answer, not a UI commit boundary. A model
  // round can finish before the root Run closes its remaining plan steps, so
  // rendering here lets answer text appear between later process updates.
  // Keep it private until the root `done` chunk commits the turn atomically.
  ctx.acc.response += chunk.delta;
};
