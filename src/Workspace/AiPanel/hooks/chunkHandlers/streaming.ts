import { flushSync } from "react-dom";
import { appendAssistantTailMarkdown } from "../../rendering";
import { isWritingExpertPipeline, type ChatMessage } from "../chat.types";
import type { ChunkHandler } from "./types";

export const handleToolRouterWarning: ChunkHandler = (chunk, ctx) => {
  if (chunk.toolRouterWarning) {
    ctx.appMessage.warning(chunk.toolRouterWarning);
  }
};

export const handleThinkingDelta: ChunkHandler = (chunk, ctx) => {
  if (!chunk.thinkingDelta) return;
  ctx.acc.thinking += chunk.thinkingDelta;
  if (!ctx.isVisibleSession()) return;
  const td = chunk.thinkingDelta;
  flushSync(() => {
    ctx.setConversations((prev) => {
      const next = [...prev];
      const last = next[next.length - 1];
      if (!last || last.role !== "assistant") return prev;
      next[next.length - 1] = {
        ...last,
        thinking: (last.thinking || "") + td,
        toolCalling: false,
      };
      return next;
    });
  });
};

export const handleDelta: ChunkHandler = (chunk, ctx) => {
  if (!chunk.delta) return;
  const delta = chunk.delta;
  const { acc } = ctx;
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
  flushSync(() => {
    ctx.setConversations((prev) => {
      const next = [...prev];
      const last = next[next.length - 1];
      if (!last || last.role !== "assistant") return prev;
      const segs = (last as ChatMessage).toolCallSegments;
      if (segs?.length) {
        next[next.length - 1] = {
          ...last,
          contentAfterToolCalls: acc.contentAfterToolCalls,
          content: acc.response,
          toolCalling: false,
        };
      } else {
        next[next.length - 1] = {
          ...last,
          content: (last.content || "") + delta,
          toolCalling: false,
        };
      }
      return next;
    });
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
  flushSync(() => {
    ctx.setConversations((prev) => {
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
  });
};
