import { flushSync } from "react-dom";
import {
  isWritingExpertPipeline,
  type ChatMessage,
  type ToolCallLabelOutcome,
  type ToolCallSegment,
} from "../chat.types";
import { toolCallDisplayRow } from "../toolCallLabels";
import type { ChunkHandler } from "./types";

/**
 * 工具批次开始：把后端的 toolCalls 数组转成"工具气泡 + 进度区段"，并把流式正文挂到段头。
 *
 * 这是 onAiChunk 里最复杂的分支，且无条件 return（吞掉本 chunk 后续 if）。
 * 拆分后逻辑保持 1:1：可见会话走 setConversations 重建段；不可见会话只更新 acc 累加器。
 */
export const handleToolCallsInProgress: ChunkHandler = (chunk, ctx) => {
  if (!chunk.toolCalls?.length || !chunk.toolCallsInProgress) return;

  const partialContent = chunk.partialContent ?? "";
  const partialThinking = chunk.partialThinking ?? "";
  const insertedByDag = chunk.orchestratorInfo?.insertedByDag ?? 0;
  const repairReasons = (chunk.orchestratorRepair?.events || [])
    .map((x) => String(x?.reason || "").trim())
    .filter(Boolean);

  const visibleToolCalls = (chunk.toolCalls || []).filter((tc) => {
    const callId = String((tc as { id?: string })?.id || "");
    return !callId.startsWith("repair_") && !callId.startsWith("sys_");
  });

  if (
    visibleToolCalls.length === 0 &&
    !partialContent.trim() &&
    !partialThinking.trim()
  ) {
    return true;
  }

  const rows = visibleToolCalls.map(
    (tc: { function?: { name?: string; arguments?: string }; id?: string }) => {
      const fn = tc.function?.name;
      if (!fn) {
        return { label: "（未识别工具）", outcome: "ok" as ToolCallLabelOutcome };
      }
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
        );
      }
    },
  );

  const taggedLabels = rows.map((row, idx) => {
    const base = row.label;
    const tc = visibleToolCalls[idx];
    const callId = String(tc?.id || "");
    if (callId.startsWith("repair_")) return `${base}（自动修复）`;
    if (callId.startsWith("sys_")) return `${base}（自动补前置）`;
    return base;
  });
  const labelOutcomes = rows.map((row) => row.outcome);

  const buildSegment = (
    textBefore: string,
    cachedFlags: boolean[],
  ): ToolCallSegment => ({
    textBefore,
    labels: taggedLabels,
    labelOutcomes,
    cachedFlags,
    trace: {
      insertedByDag,
      insertedSkillNames: chunk.orchestratorInfo?.insertedSkillNames ?? [],
      plannedToolNames: chunk.orchestratorInfo?.plannedToolNames ?? [],
      repairedRounds: chunk.orchestratorRepair?.repairedRounds ?? 0,
      repairReasons,
      stage: chunk.subagentStage || undefined,
    },
  });

  const initialCachedFlags = visibleToolCalls.map((tc) =>
    Boolean((tc as { cached?: boolean }).cached),
  );

  let rebuiltAssistantText = "";
  const { acc, agentMode } = ctx;

  if (ctx.isVisibleSession()) {
    flushSync(() => {
      ctx.setConversations((prev) => {
        const next = [...prev];
        const lastMsg = next[next.length - 1];
        if (lastMsg?.role !== "assistant") return next;

        const prevSeg = (lastMsg as ChatMessage).toolCallSegments ?? [];
        const prevBlocks = (lastMsg as ChatMessage).thinkingBlocks ?? [];
        const currentThinking = (lastMsg.thinking || "").trim();
        const nextBlocks = currentThinking
          ? [...prevBlocks, currentThinking]
          : prevBlocks;
        /** 仅含「主稿专家过渡 / 最终答复」等流式尾稿；新工具批开始前须并入上方片段，
         *  否则渲染顺序会变成「新工具段在旧尾稿之上」 */
        const tailAcc =
          acc.contentAfterToolCalls ?? lastMsg.contentAfterToolCalls ?? "";
        const hasPartial = Boolean(partialContent && partialContent.trim());
        const flushTailSegments: ToolCallSegment[] =
          !hasPartial && tailAcc.trim().length > 0
            ? [{ textBefore: tailAcc, labels: [], cachedFlags: [] }]
            : [];
        const baseSegs = [...prevSeg, ...flushTailSegments];
        const hasPriorToolRound = prevSeg.some((s) => s.labels.length > 0);
        const textBefore =
          partialContent && partialContent.trim()
            ? partialContent
            : !hasPriorToolRound
              ? isWritingExpertPipeline(agentMode)
                ? ""
                : acc.response || lastMsg.content || ""
              : "";
        const newSegment = buildSegment(textBefore, initialCachedFlags);
        const nextSegments = [...baseSegs, newSegment];
        let afterToolCalls =
          flushTailSegments.length > 0
            ? ""
            : (acc.contentAfterToolCalls ?? lastMsg.contentAfterToolCalls ?? "");
        if (
          !isWritingExpertPipeline(agentMode) &&
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
        next[next.length - 1] = {
          ...lastMsg,
          content: rebuiltAssistantText,
          thinking: "",
          thinkingBlocks: nextBlocks,
          toolCalling: true,
          toolCallSegments: nextSegments,
          contentAfterToolCalls: afterToolCalls,
        };
        return next;
      });
    });
  } else {
    const prevSeg = acc.toolCallSegments ?? [];
    const prevBlocks = acc.thinkingBlocks ?? [];
    const currentThinking = (acc.thinking || "").trim();
    const nextBlocks = currentThinking
      ? [...prevBlocks, currentThinking]
      : prevBlocks;
    const tailBg = acc.contentAfterToolCalls ?? "";
    const hasPartialBg = Boolean(partialContent && partialContent.trim());
    const flushTailSegments: ToolCallSegment[] =
      !hasPartialBg && tailBg.trim().length > 0
        ? [{ textBefore: tailBg, labels: [], cachedFlags: [] }]
        : [];
    const baseSegs = [...prevSeg, ...flushTailSegments];
    const hasPriorToolRound = prevSeg.some((s) => s.labels.length > 0);
    const textBefore =
      partialContent && partialContent.trim()
        ? partialContent
        : !hasPriorToolRound
          ? isWritingExpertPipeline(agentMode)
            ? ""
            : acc.response || ""
          : "";
    const newSegment = buildSegment(textBefore, initialCachedFlags);
    const nextSegments = [...baseSegs, newSegment];
    let afterToolCallsBg = flushTailSegments.length > 0 ? "" : tailBg;
    if (
      !isWritingExpertPipeline(agentMode) &&
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
  }

  acc.response =
    rebuiltAssistantText ||
    (partialContent && partialContent.trim() ? partialContent : acc.response);
  acc.thinking = partialThinking;
  return true;
};
