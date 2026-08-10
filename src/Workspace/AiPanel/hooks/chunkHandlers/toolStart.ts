import {
  type ChatMessage,
  type ToolCallLabelOutcome,
  type ToolCallSegment,
} from "../chat.types";
import {
  resolveLocalizedToolDisplayName,
  toolCallDisplayRow,
} from "../toolCallLabels";
import { finalizeCommentaryBlock } from "./streaming";
import type { ChunkHandler } from "./types";

/** 将一个明确的 Core 工具批次追加到公开执行时间线。 */
export const handleToolCallsInProgress: ChunkHandler = (chunk, ctx) => {
  if (!chunk.toolCalls?.length || !chunk.toolCallsInProgress) return;

  const rows = chunk.toolCalls.map((toolCall) => {
    const name = toolCall.function?.name;
    if (!name) {
      return { label: "（未识别工具）", outcome: "ok" as ToolCallLabelOutcome };
    }
    const displayName = resolveLocalizedToolDisplayName(toolCall.displayNames);
    try {
      return toolCallDisplayRow(
        name,
        JSON.parse(toolCall.function.arguments || "{}") as Record<string, unknown>,
        ctx.writingChapters,
        ctx.availableOutlines,
        displayName,
      );
    } catch {
      return toolCallDisplayRow(
        name,
        {},
        ctx.writingChapters,
        ctx.availableOutlines,
        displayName,
      );
    }
  });

  const currentCommentary = ctx.acc.commentary.trim();
  const finalized = currentCommentary
    ? finalizeCommentaryBlock(ctx, currentCommentary)
    : {
        blocks: ctx.acc.commentaryBlocks ?? [],
        durations: ctx.acc.commentaryDurationsMs ?? [],
      };
  if (currentCommentary) ctx.acc.commentary = "";

  const segment: ToolCallSegment = {
    labels: rows.map((row) => row.label),
    commentaryBlockIndex: currentCommentary
      ? finalized.blocks.length - 1
      : null,
    labelOutcomes: rows.map((row) => row.outcome),
    cachedFlags: chunk.toolCalls.map(() => false),
    startedAt: performance.now(),
  };
  ctx.acc.toolCallSegments = [...(ctx.acc.toolCallSegments ?? []), segment];

  if (ctx.isVisibleSession()) {
    ctx.scheduleCommit((prev) => {
      const next = [...prev];
      const last = next[next.length - 1];
      if (!last || last.role !== "assistant") return prev;
      const message = last as ChatMessage;
      next[next.length - 1] = {
        ...message,
        commentary: "",
        commentaryStartedAt: undefined,
        commentaryBlocks: finalized.blocks.length
          ? finalized.blocks
          : undefined,
        commentaryDurationsMs: finalized.durations.length
          ? finalized.durations
          : undefined,
        toolCalling: true,
        toolCallSegments: ctx.acc.toolCallSegments,
        taskPlan: ctx.acc.taskPlan,
      };
      return next;
    });
  }

  return true;
};
