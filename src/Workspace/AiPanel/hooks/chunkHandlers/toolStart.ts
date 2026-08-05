import {
  type ChatMessage,
  type ToolCallLabelOutcome,
  type ToolCallSegment,
} from "../chat.types";
import {
  resolveLocalizedToolDisplayName,
  toolCallDisplayRow,
} from "../toolCallLabels";
import { finalizeThinkingBlock } from "./streaming";
import type { ChunkHandler } from "./types";

function latestUnassignedThinkingBlock(
  blockCount: number,
  segments: ToolCallSegment[],
  excludedIndex?: number,
): number | null {
  const assigned = new Set(
    segments
      .map((segment) => segment.thinkingBlockIndex)
      .filter((index): index is number => typeof index === "number"),
  );
  for (let index = blockCount - 1; index >= 0; index -= 1) {
    if (index !== excludedIndex && !assigned.has(index)) return index;
  }
  return null;
}

/**
 * 工具批次开始：把后端的 toolCalls 数组转成"工具气泡 + 进度区段"，并把流式正文挂到段头。
 */
export const handleToolCallsInProgress: ChunkHandler = (chunk, ctx) => {
  if (!chunk.toolCalls?.length || !chunk.toolCallsInProgress) return;

  const partialContent = chunk.partialContent ?? "";
  const partialThinking = chunk.partialThinking ?? "";
  const visibleToolCalls = chunk.toolCalls;

  if (
    visibleToolCalls.length === 0 &&
    !partialContent.trim() &&
    !partialThinking.trim()
  ) {
    return true;
  }

  const rows = visibleToolCalls.map(
    (tc: {
      function?: { name?: string; arguments?: string };
      id?: string;
      displayNames?: Record<string, string>;
    }) => {
      const fn = tc.function?.name;
      if (!fn) {
        return { label: "（未识别工具）", outcome: "ok" as ToolCallLabelOutcome };
      }
      const displayName = resolveLocalizedToolDisplayName(tc.displayNames);
      try {
        const args = JSON.parse(tc.function?.arguments || "{}") as Record<
          string,
          unknown
        >;
        return toolCallDisplayRow(
          fn,
          args,
          ctx.writingChapters || [],
          ctx.availableOutlines || [],
          displayName,
        );
      } catch {
        if (fn === "queryOutline") {
          return {
            label: "查看大纲详情",
            outcome: "ok" as ToolCallLabelOutcome,
          };
        }
        return toolCallDisplayRow(
          fn,
          {},
          ctx.writingChapters || [],
          ctx.availableOutlines || [],
          displayName,
        );
      }
    },
  );

  const labels = rows.map((row) => row.label);
  const labelOutcomes = rows.map((row) => row.outcome);

  const buildSegment = (
    textBefore: string,
    cachedFlags: boolean[],
    thinkingBlockIndex: number | null,
  ): ToolCallSegment => ({
    textBefore,
    labels,
    thinkingBlockIndex,
    labelOutcomes,
    cachedFlags,
    startedAt: performance.now(),
  });

  const initialCachedFlags = visibleToolCalls.map(() => false);

  let rebuiltAssistantText = "";
  const { acc } = ctx;

  const applyThinkingFinalize = (currentThinking: string) => {
    if (!currentThinking.trim()) {
      return {
        blocks: acc.thinkingBlocks ?? [],
        durations: acc.thinkingDurationsMs ?? [],
      };
    }
    return finalizeThinkingBlock(ctx, currentThinking);
  };

  if (ctx.isVisibleSession()) {
    ctx.scheduleCommit((prev) => {
      const next = [...prev];
      const lastMsg = next[next.length - 1];
      if (lastMsg?.role !== "assistant") return next;

      const prevSeg = (lastMsg as ChatMessage).toolCallSegments ?? [];
      const currentThinking = (lastMsg.thinking || "").trim();
      const { blocks: nextBlocks, durations: nextDurations } =
        applyThinkingFinalize(currentThinking);
      const currentThinkingBlockIndex = currentThinking
        ? nextBlocks.length - 1
        : null;
      const tailAcc =
        acc.contentAfterToolCalls ?? lastMsg.contentAfterToolCalls ?? "";
      const hasPartial = Boolean(partialContent && partialContent.trim());
      const tailThinkingBlockIndex = latestUnassignedThinkingBlock(
        nextBlocks.length,
        prevSeg,
        currentThinkingBlockIndex ?? undefined,
      );
      const segmentThinkingBlockIndex =
        currentThinkingBlockIndex ??
        (hasPartial ? tailThinkingBlockIndex : null);
      const flushTailSegments: ToolCallSegment[] =
        !hasPartial && tailAcc.trim().length > 0
          ? [{
              textBefore: tailAcc,
              labels: [],
              thinkingBlockIndex: tailThinkingBlockIndex,
              cachedFlags: [],
            }]
          : [];
      const baseSegs = [...prevSeg, ...flushTailSegments];
      const hasPriorToolRound = prevSeg.some((s) => s.labels.length > 0);
      const textBefore =
        partialContent && partialContent.trim()
          ? partialContent
          : !hasPriorToolRound
            ? acc.response || lastMsg.content || ""
            : "";
      const newSegment = buildSegment(
        textBefore,
        initialCachedFlags,
        segmentThinkingBlockIndex,
      );
      const nextSegments = [...baseSegs, newSegment];
      let afterToolCalls =
        flushTailSegments.length > 0
          ? ""
          : (acc.contentAfterToolCalls ?? lastMsg.contentAfterToolCalls ?? "");
      if (
        flushTailSegments.length === 0 &&
        textBefore &&
        afterToolCalls.startsWith(textBefore)
      ) {
        afterToolCalls = afterToolCalls.slice(textBefore.length);
      }
      rebuiltAssistantText =
        nextSegments.map((s) => s.textBefore).join("") + afterToolCalls;
      acc.toolCallSegments = nextSegments;
      acc.contentAfterToolCalls = afterToolCalls;
      acc.thinkingBlocks = nextBlocks;
      acc.thinkingDurationsMs = nextDurations;
      next[next.length - 1] = {
        ...lastMsg,
        content: rebuiltAssistantText,
        thinking: "",
        thinkingStartedAt: undefined,
        thinkingBlocks: nextBlocks.length ? nextBlocks : undefined,
        thinkingDurationsMs: nextDurations.length ? nextDurations : undefined,
        toolCalling: true,
        toolCallSegments: nextSegments,
        contentAfterToolCalls: afterToolCalls,
        taskPlan: acc.taskPlan,
      };
      return next;
    });
  } else {
    const prevSeg = acc.toolCallSegments ?? [];
    const currentThinking = (acc.thinking || "").trim();
    const { blocks: nextBlocks, durations: nextDurations } =
      applyThinkingFinalize(currentThinking);
    const currentThinkingBlockIndex = currentThinking
      ? nextBlocks.length - 1
      : null;
    const tailBg = acc.contentAfterToolCalls ?? "";
    const hasPartialBg = Boolean(partialContent && partialContent.trim());
    const tailThinkingBlockIndex = latestUnassignedThinkingBlock(
      nextBlocks.length,
      prevSeg,
      currentThinkingBlockIndex ?? undefined,
    );
    const segmentThinkingBlockIndex =
      currentThinkingBlockIndex ??
      (hasPartialBg ? tailThinkingBlockIndex : null);
    const flushTailSegments: ToolCallSegment[] =
      !hasPartialBg && tailBg.trim().length > 0
        ? [{
            textBefore: tailBg,
            labels: [],
            thinkingBlockIndex: tailThinkingBlockIndex,
            cachedFlags: [],
          }]
        : [];
    const baseSegs = [...prevSeg, ...flushTailSegments];
    const hasPriorToolRound = prevSeg.some((s) => s.labels.length > 0);
    const textBefore =
      partialContent && partialContent.trim()
        ? partialContent
        : !hasPriorToolRound
          ? acc.response || ""
          : "";
    const newSegment = buildSegment(
      textBefore,
      initialCachedFlags,
      segmentThinkingBlockIndex,
    );
    const nextSegments = [...baseSegs, newSegment];
    let afterToolCallsBg = flushTailSegments.length > 0 ? "" : tailBg;
    if (
      flushTailSegments.length === 0 &&
      textBefore &&
      afterToolCallsBg.startsWith(textBefore)
    ) {
      afterToolCallsBg = afterToolCallsBg.slice(textBefore.length);
    }
    rebuiltAssistantText =
      nextSegments.map((s) => s.textBefore).join("") + afterToolCallsBg;
    acc.toolCallSegments = nextSegments;
    acc.contentAfterToolCalls = afterToolCallsBg;
    acc.thinkingBlocks = nextBlocks;
    acc.thinkingDurationsMs = nextDurations;
  }

  acc.response =
    rebuiltAssistantText ||
    (partialContent && partialContent.trim() ? partialContent : acc.response);
  acc.thinking = partialThinking;
  return true;
};
