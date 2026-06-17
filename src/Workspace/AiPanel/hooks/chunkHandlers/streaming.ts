import { appendAssistantTailMarkdown } from "../../rendering";
import { isWritingExpertPipeline, type ChatMessage } from "../chat.types";
import type { ChunkHandler } from "./types";

export const handleToolRouterWarning: ChunkHandler = (chunk, ctx) => {
  if (chunk.toolRouterWarning) {
    ctx.appMessage.warning(chunk.toolRouterWarning);
  }
};

function ensureThinkingBlockStarted(ctx: Parameters<ChunkHandler>[1]): number {
  if (ctx.acc.thinkingBlockStartedAt == null) {
    ctx.acc.thinkingBlockStartedAt = performance.now();
  }
  return ctx.acc.thinkingBlockStartedAt;
}

function finalizeThinkingBlock(
  ctx: Parameters<ChunkHandler>[1],
  blockText: string,
): { blocks: string[]; durations: number[] } {
  const blocks = [...(ctx.acc.thinkingBlocks ?? [])];
  const durations = [...(ctx.acc.thinkingDurationsMs ?? [])];
  const trimmed = blockText.trim();
  if (trimmed) {
    blocks.push(trimmed);
    const started = ctx.acc.thinkingBlockStartedAt;
    durations.push(
      started != null ? Math.max(0, Math.round(performance.now() - started)) : 0,
    );
  }
  ctx.acc.thinkingBlocks = blocks;
  ctx.acc.thinkingDurationsMs = durations;
  ctx.acc.thinkingBlockStartedAt = undefined;
  return { blocks, durations };
}

export const handleThinkingDelta: ChunkHandler = (chunk, ctx) => {
  if (!chunk.thinkingDelta) return;
  const thinkingStartedAt = ensureThinkingBlockStarted(ctx);
  ctx.acc.thinking += chunk.thinkingDelta;
  if (!ctx.isVisibleSession()) return;
  const td = chunk.thinkingDelta;
  ctx.scheduleCommit((prev) => {
    const next = [...prev];
    const last = next[next.length - 1];
    if (!last || last.role !== "assistant") return prev;
    next[next.length - 1] = {
      ...last,
      thinking: (last.thinking || "") + td,
      thinkingStartedAt,
      toolCalling: false,
    };
    return next;
  });
};

export const handleDelta: ChunkHandler = (chunk, ctx) => {
  if (!chunk.delta) return;
  const delta = chunk.delta;
  const { acc } = ctx;
  const finalizedThinking = acc.thinking.trim()
    ? finalizeThinkingBlock(ctx, acc.thinking)
    : null;
  if (finalizedThinking) {
    acc.thinking = "";
  }

  if (acc.toolCallSegments?.length) {
    let after = (acc.contentAfterToolCalls ?? "") + delta;
    if (!isWritingExpertPipeline(ctx.agentMode)) {
      const lastS = acc.toolCallSegments[acc.toolCallSegments.length - 1];
      if (lastS?.textBefore && after.startsWith(lastS.textBefore)) {
        after = after.slice(lastS.textBefore.length);
      }
    }
    acc.contentAfterToolCalls = after;
    acc.response =
      acc.toolCallSegments.map((s) => s.textBefore).join("") + after;
  } else {
    acc.contentAfterToolCalls = "";
    acc.response += delta;
  }

  if (!ctx.isVisibleSession()) return;
  ctx.scheduleCommit((prev) => {
    const next = [...prev];
    const last = next[next.length - 1];
    if (!last || last.role !== "assistant") return prev;
    const thinkingBlocks =
      finalizedThinking?.blocks ?? (last as ChatMessage).thinkingBlocks;
    const thinkingDurationsMs =
      finalizedThinking?.durations ??
      (last as ChatMessage).thinkingDurationsMs;
    const segs = (last as ChatMessage).toolCallSegments;
    if (segs?.length) {
      next[next.length - 1] = {
        ...last,
        contentAfterToolCalls: acc.contentAfterToolCalls,
        content: acc.response,
        thinking: "",
        thinkingStartedAt: undefined,
        thinkingBlocks: thinkingBlocks?.length ? thinkingBlocks : undefined,
        thinkingDurationsMs: thinkingDurationsMs?.length
          ? thinkingDurationsMs
          : undefined,
        toolCalling: false,
        taskPlan: acc.taskPlan,
      };
    } else {
      next[next.length - 1] = {
        ...last,
        content: (last.content || "") + delta,
        thinking: "",
        thinkingStartedAt: undefined,
        thinkingBlocks: thinkingBlocks?.length ? thinkingBlocks : undefined,
        thinkingDurationsMs: thinkingDurationsMs?.length
          ? thinkingDurationsMs
          : undefined,
        toolCalling: false,
        taskPlan: acc.taskPlan,
      };
    }
    return next;
  });
};

export const handleCollabLatestParagraph: ChunkHandler = (chunk, ctx) => {
  const raw = chunk.collabLatestParagraph;
  if (typeof raw !== "string" || !raw.trim()) return;
  const para = raw.trim();
  const wrapped = `\n\n### 最新段落（已写入正文）\n\n\`\`\`text\n${para}\n\`\`\`\n`;
  const { acc } = ctx;
  const accTailPatched = appendAssistantTailMarkdown(
    {
      content: acc.response,
      contentAfterToolCalls: acc.contentAfterToolCalls,
      toolCallSegments: acc.toolCallSegments,
    },
    wrapped,
  );
  acc.response = accTailPatched.content ?? acc.response;
  if (acc.toolCallSegments?.length) {
    acc.contentAfterToolCalls = accTailPatched.contentAfterToolCalls ?? "";
  }
  ctx.scheduleCommit((prev) => {
    const next = [...prev];
    const last = next[next.length - 1];
    if (!last || last.role !== "assistant") return prev;
    const tailPatched = appendAssistantTailMarkdown(
      {
        content: (last as ChatMessage).content,
        contentAfterToolCalls: (last as ChatMessage).contentAfterToolCalls,
        toolCallSegments: (last as ChatMessage).toolCallSegments,
      },
      wrapped,
    );
    next[next.length - 1] = {
      ...(last as ChatMessage),
      content: tailPatched.content ?? (last.content || ""),
      contentAfterToolCalls: tailPatched.contentAfterToolCalls,
    };
    return next;
  });
};

export { finalizeThinkingBlock, ensureThinkingBlockStarted };
