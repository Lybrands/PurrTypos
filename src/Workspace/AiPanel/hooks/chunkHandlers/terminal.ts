import { mergeAssistantErrorNotice } from "../../rendering";
import { type AiTaskPlan, type ChatMessage } from "../chat.types";
import { synthesizeAssistantTextFromToolSegments } from "../chatHistory";
import { finalizeThinkingBlock } from "./streaming";
import type { ChunkHandler } from "./types";

/**
 * 错误终态：把错误注释合并到当前助手轮，关 loading，cleanup 订阅与 refs。
 */
export const handleError: ChunkHandler = (chunk, ctx) => {
  if (!chunk.error) return;
  const { acc } = ctx;
  const durationMs = Math.max(0, Math.round(performance.now() - acc.turnStartedAt));
  acc.toolCallSegments = finalizeToolDurations(acc.toolCallSegments);
  ctx.flushCommits();

  if (ctx.isVisibleSession()) {
    ctx.setConversations((prev) => {
      const next = [...prev];
      const last = next[next.length - 1];
      if (!last || last.role !== "assistant") return prev;
      const merged = mergeAssistantErrorNotice(
        {
          content: acc.response || (last as ChatMessage).content || "",
          contentAfterToolCalls:
            acc.toolCallSegments?.length
              ? (acc.contentAfterToolCalls ??
                (last as ChatMessage).contentAfterToolCalls)
              : (last as ChatMessage).contentAfterToolCalls,
          toolCallSegments:
            acc.toolCallSegments ?? (last as ChatMessage).toolCallSegments,
        },
        chunk.error,
      );
      next[next.length - 1] = {
        ...(last as ChatMessage),
        content: merged.content ?? (last as ChatMessage).content,
        contentAfterToolCalls: merged.contentAfterToolCalls,
        taskPlan: acc.taskPlan ?? (last as ChatMessage).taskPlan,
        durationMs,
        turnStartedAt: undefined,
        toolCalling: false,
        ...(merged.isError ? { isError: true } : {}),
      };
      return next;
    });
    ctx.setLoading(false);
  }
  ctx.cleanup();
  return true;
};

/**
 * 正常终态：合并最终内容与 thinking 收尾，cleanup，落库，按需生成会话标题。
 */
export const handleDone: ChunkHandler = (chunk, ctx) => {
  if (!chunk.done) return;
  const { acc } = ctx;
  const durationMs = Math.max(0, Math.round(performance.now() - acc.turnStartedAt));
  acc.toolCallSegments = finalizeToolDurations(acc.toolCallSegments);
  ctx.flushCommits();
  if (chunk.model) acc.model = chunk.model;
  if (chunk.aborted) {
    acc.taskPlan = markTaskPlanAborted(acc.taskPlan);
  }

  const finalThinking = (acc.thinking || "").trim();
  let savedThinkingBlocks = acc.thinkingBlocks ?? [];
  let savedThinkingDurations = acc.thinkingDurationsMs ?? [];
  if (finalThinking) {
    const finalized = finalizeThinkingBlock(ctx, finalThinking);
    savedThinkingBlocks = finalized.blocks;
    savedThinkingDurations = finalized.durations;
  }

  let resolvedAssistantContent =
    acc.response ||
    synthesizeAssistantTextFromToolSegments({
      role: "assistant",
      content: "",
      toolCallSegments: acc.toolCallSegments,
    } as ChatMessage).trim();

  if (ctx.isVisibleSession()) {
    ctx.setConversations((prev) => {
      const next = [...prev];
      const last = next[next.length - 1];
      if (last?.role === "assistant") {
        const cm = last as ChatMessage;
        const thinkingBlocks = savedThinkingBlocks.length
          ? savedThinkingBlocks
          : cm.thinkingBlocks;
        const thinkingDurationsMs = savedThinkingDurations.length
          ? savedThinkingDurations
          : cm.thinkingDurationsMs;
        const currentContent = String(last.content || "");
        const accContent = (acc.response || "").trim();
        let finalContent = currentContent;
        if (!currentContent.trim()) {
          if (accContent) {
            finalContent = acc.response!;
          } else {
            const synthesized =
              synthesizeAssistantTextFromToolSegments(cm).trim();
            if (synthesized) {
              finalContent = synthesized;
            } else {
              finalContent = "内容同步中。";
            }
          }
        }
        resolvedAssistantContent = finalContent;
        next[next.length - 1] = {
          ...last,
          content: finalContent,
          model: acc.model || undefined,
          durationMs,
          turnStartedAt: undefined,
          thinking: "",
          thinkingStartedAt: undefined,
          thinkingBlocks: thinkingBlocks?.length ? thinkingBlocks : undefined,
          thinkingDurationsMs: thinkingDurationsMs?.length
            ? thinkingDurationsMs
            : undefined,
          taskPlan:
            acc.taskPlan ??
            (chunk.aborted ? markTaskPlanAborted(cm.taskPlan) : cm.taskPlan),
          toolCalling: false,
        };
      }
      return next;
    });
    ctx.setLoading(false);
  }

  ctx.cleanup();
  acc.response = resolvedAssistantContent;

  saveConversationIfNeeded(ctx, savedThinkingBlocks, savedThinkingDurations);
  maybeGenerateSessionTitle(ctx);

  return true;
};

function markTaskPlanAborted(plan: AiTaskPlan | undefined): AiTaskPlan | undefined {
  if (!plan) return plan;
  return {
    ...plan,
    status: "canceled",
    steps: plan.steps.map((step) => {
      if (step.status !== "running") return step;
      return {
        ...step,
        status: "blocked" as const,
        resultSummary: step.resultSummary || "本轮已由你手动停止。",
      };
    }),
  };
}

function saveConversationIfNeeded(
  ctx: Parameters<ChunkHandler>[1],
  savedThinkingBlocks: string[],
  savedThinkingDurations: number[],
): void {
  const { acc } = ctx;
  const respTrim = (acc.response || "").trim();
  const thinkTrim = (acc.thinking || "").trim();
  const shouldSave =
    Boolean(acc.sessionId) &&
    Boolean(
      respTrim ||
        thinkTrim ||
        acc.taskPlan ||
        savedThinkingBlocks.length > 0 ||
        (acc.toolCallSegments?.length ?? 0) > 0,
    );
  if (!shouldSave) return;

  void window.electronAPI
    .saveConversation({
      sessionId: acc.sessionId,
      bookId: acc.bookId ?? undefined,
      chapterId: acc.chapterId ?? null,
      prompt: acc.userText,
      response: acc.response || "",
      model: acc.model || undefined,
      thinking: acc.thinking || undefined,
      thinkingBlocks: savedThinkingBlocks.length
        ? savedThinkingBlocks
        : undefined,
      thinkingDurationsMs: savedThinkingDurations.length
        ? savedThinkingDurations
        : undefined,
      durationMs: Math.max(0, Math.round(performance.now() - acc.turnStartedAt)),
      toolCallSegments: acc.toolCallSegments?.length
        ? acc.toolCallSegments
        : undefined,
      taskPlan: acc.taskPlan ?? undefined,
      contextCompaction: acc.contextCompaction,
      contextBudget: acc.contextBudget,
      agentRunId: acc.agentRunId,
    })
    .then((res) => {
      if (res && res.success) {
        const conversationId = res.data?.id;
        if (typeof conversationId === "number" && ctx.isVisibleSession()) {
          ctx.setConversations((prev) => {
            const idx = findConversationMessageIndex(
              prev,
              acc.userText,
              acc.response || "",
            );
            if (idx < 0) {
              return prev;
            }
            const next = [...prev];
            next[idx] = { ...next[idx], conversationId };
            return next;
          });
        }
        return;
      }
      const detail = (res as { detail?: unknown })?.detail;
      const msg =
        typeof (res as { error?: string })?.error === "string"
          ? (res as { error: string }).error
          : Array.isArray(detail)
            ? detail
                .map(
                  (d: { msg?: string }) =>
                    String(d?.msg ?? d ?? "").trim(),
                )
                .filter(Boolean)
                .join("；")
            : "";
      ctx.appMessage.warning(
        msg
          ? `本轮对话未能写入本地库：${msg}`
          : "本轮对话未能写入本地库（保存接口异常）。",
      );
    });
}

function finalizeToolDurations(
  segments: ChatMessage["toolCallSegments"],
): ChatMessage["toolCallSegments"] {
  if (!segments?.length) return segments;
  const now = performance.now();
  return segments.map((segment) => {
    if (segment.durationMs != null || segment.startedAt == null) return segment;
    const { startedAt, ...rest } = segment;
    return {
      ...rest,
      durationMs: Math.max(0, Math.round(now - startedAt)),
    };
  });
}

function findConversationMessageIndex(
  messages: Array<{ role: string; content: string; conversationId?: number }>,
  userText: string,
  assistantContent: string,
): number {
  for (let idx = messages.length - 1; idx >= 1; idx -= 1) {
    const assistant = messages[idx];
    const user = messages[idx - 1];
    if (
      assistant.role === "assistant" &&
      !assistant.conversationId &&
      assistant.content === assistantContent &&
      user?.role === "user" &&
      user.content === userText
    ) {
      return idx;
    }
  }
  return -1;
}

function maybeGenerateSessionTitle(
  ctx: Parameters<ChunkHandler>[1],
): void {
  const { acc, cfg, apiModelName } = ctx;
  const titleSource = (acc.response || acc.thinking || "").trim();
  if (!acc.needsTitle || !acc.sessionId || !titleSource) return;

  console.log("[AI 对话] 请求生成标题", {
    sessionId: acc.sessionId,
    apiProvider: cfg.apiProvider === "anthropic" ? "anthropic" : "openai",
    model: apiModelName,
  });
  window.electronAPI
    .generateSessionTitle({
      apiKey: cfg.apiKey,
      baseURL: cfg.baseUrl || undefined,
      prompt:
        `User:\n${acc.userText}\n\nAssistant:\n${titleSource}`.trim(),
      apiProvider:
        cfg.apiProvider === "anthropic" ? "anthropic" : "openai",
      model: apiModelName,
    })
    .then(async (titleRes) => {
      if (!titleRes.success || !titleRes.data?.trim()) {
        console.warn("[AI 对话] 标题生成失败或为空", {
          success: titleRes.success,
          error: titleRes.error ?? null,
          hasData: Boolean(titleRes.data),
          dataPreview:
            typeof titleRes.data === "string"
              ? titleRes.data.slice(0, 40)
              : null,
        });
        return;
      }
      const nextTitle = titleRes.data.trim();
      await window.electronAPI.updateSessionTitle({
        sessionId: acc.sessionId,
        title: nextTitle,
      });
      ctx.setSessions((prev) =>
        prev.map((s) =>
          s.id === acc.sessionId ? { ...s, title: nextTitle } : s,
        ),
      );
    })
    .catch((err) => {
      console.warn("[AI 对话] 标题生成请求异常：", err);
    });
}
