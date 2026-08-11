import { mergeAssistantErrorNotice } from "../../rendering";
import { normalizeApiProvider } from "../../../../modelCatalog";
import { type AiTaskPlan, type ChatMessage } from "../chat.types";
import {
  EMPTY_RESPONSE_MESSAGE,
} from "../chatHistory";
import type { ChunkHandler } from "./types";
import { presentAgentRunError } from "../../../../agent-runtime/agentErrorPresentation";

const getServices = () => import('@/services').then((module) => module.services)

export const MANUAL_ABORT_MESSAGE = "本轮对话已由你手动终止。";
export { EMPTY_RESPONSE_MESSAGE } from "../chatHistory";

export const handleRunResultTerminal: ChunkHandler = (chunk, ctx) => {
  if (!chunk.done || !chunk.runResult) return;
  const { status, errorCode } = chunk.runResult;
  if (status === "failed" || status === "blocked") {
    return handleError({
      ...chunk,
      error: presentAgentRunError(status, errorCode),
    }, ctx);
  }
  if (status === "canceled") {
    return handleDone({ ...chunk, aborted: true }, ctx);
  }
};

/**
 * 错误终态：保留公开执行说明和工具过程，并附上终止原因。
 */
export const handleError: ChunkHandler = (chunk, ctx) => {
  if (!chunk.error) return;
  const { acc } = ctx;
  const durationMs = Math.max(0, Math.round(performance.now() - acc.turnStartedAt));
  const savedCommentaryBlocks = acc.commentaryBlocks ?? [];
  const savedCommentaryDurations = acc.commentaryDurationsMs ?? [];
  ctx.flushCommits();

  if (ctx.isVisibleSession()) {
    ctx.setConversations((prev) => {
      const next = [...prev];
      const last = next[next.length - 1];
      if (!last || last.role !== "assistant") return prev;
      const cm = last as ChatMessage;
      const hasInspectableProcess = Boolean(
        (acc.response || cm.content || "").trim() ||
          savedCommentaryBlocks.length ||
          (acc.toolCallSegments?.length ?? cm.toolCallSegments?.length ?? 0) ||
          acc.taskPlan ||
          cm.taskPlan ||
          acc.delegations?.length ||
          cm.delegations?.length ||
          cm.subAgentActivities?.length ||
          acc.contextCompaction ||
          cm.contextCompaction,
      );
      const merged = mergeAssistantErrorNotice(
        {
          content: acc.response || cm.content || "",
        },
        chunk.error,
      );
      next[next.length - 1] = {
        ...cm,
        content: hasInspectableProcess
          ? acc.response || cm.content || ""
          : merged.content ?? cm.content,
        streamingContent: undefined,
        commentary: "",
        commentaryStartedAt: undefined,
        commentaryBlocks: savedCommentaryBlocks.length
          ? savedCommentaryBlocks
          : cm.commentaryBlocks,
        commentaryDurationsMs: savedCommentaryDurations.length
          ? savedCommentaryDurations
          : cm.commentaryDurationsMs,
        toolCallSegments: acc.toolCallSegments ?? cm.toolCallSegments,
        taskPlan: acc.taskPlan ?? cm.taskPlan,
        durationMs,
        turnStartedAt: undefined,
        toolCalling: false,
        errorReport: chunk.errorReport ?? cm.errorReport,
        ...(hasInspectableProcess
          ? { error: chunk.error, isError: false }
          : { isError: true }),
      };
      return next;
    });
    ctx.setLoading(false);
  }
  ctx.cleanup("failed");
  return true;
};

/**
 * 正常终态：收口最终回答和公开执行说明，随后落库。
 */
export const handleDone: ChunkHandler = (chunk, ctx) => {
  if (!chunk.done) return;
  const { acc } = ctx;
  const durationMs = Math.max(0, Math.round(performance.now() - acc.turnStartedAt));
  ctx.flushCommits();
  if (chunk.model) acc.model = chunk.model;
  if (chunk.aborted) {
    acc.taskPlan = markTaskPlanAborted(acc.taskPlan);
  }
  const finalModelResponse = acc.response || "";
  const hasVisibleModelResponse = Boolean(finalModelResponse.trim());
  const finalResponseExpected = chunk.finalResponseExpected !== false;
  const emptyResponse = (
    finalResponseExpected && !chunk.aborted && !hasVisibleModelResponse
  );

  const savedCommentaryBlocks = acc.commentaryBlocks ?? [];
  const savedCommentaryDurations = acc.commentaryDurationsMs ?? [];
  let resolvedAssistantContent = finalModelResponse;

  if (ctx.isVisibleSession()) {
    ctx.setConversations((prev) => {
      const next = [...prev];
      const last = next[next.length - 1];
      if (last?.role === "assistant") {
        const cm = last as ChatMessage;
        const commentaryBlocks = savedCommentaryBlocks.length
          ? savedCommentaryBlocks
          : cm.commentaryBlocks;
        const commentaryDurationsMs = savedCommentaryDurations.length
          ? savedCommentaryDurations
          : cm.commentaryDurationsMs;
        const currentContent = String(last.content || "");
        const accContent = finalModelResponse.trim();
        let finalContent = currentContent;
        if (!currentContent.trim()) {
          if (accContent) {
            finalContent = finalModelResponse;
          }
        }
        resolvedAssistantContent = finalContent;
        next[next.length - 1] = {
          ...last,
          content: finalContent,
          streamingContent: undefined,
          model: acc.model || undefined,
          durationMs,
          turnStartedAt: undefined,
          commentary: "",
          commentaryStartedAt: undefined,
          commentaryBlocks: commentaryBlocks?.length ? commentaryBlocks : undefined,
          commentaryDurationsMs: commentaryDurationsMs?.length
            ? commentaryDurationsMs
            : undefined,
          toolCallSegments: acc.toolCallSegments ?? cm.toolCallSegments,
          taskPlan:
            acc.taskPlan ??
            (chunk.aborted ? markTaskPlanAborted(cm.taskPlan) : cm.taskPlan),
          longTaskId: acc.longTaskId ?? cm.longTaskId,
          termination:
            chunk.aborted
              ? MANUAL_ABORT_MESSAGE
              : undefined,
          toolCalling: false,
          errorReport: emptyResponse
            ? chunk.errorReport ?? cm.errorReport
            : cm.errorReport,
          ...(emptyResponse
            ? { error: EMPTY_RESPONSE_MESSAGE, isError: false }
            : {}),
        };
      }
      return next;
    });
    ctx.setLoading(false);
  }

  ctx.cleanup(
    !finalResponseExpected
      ? "paused"
      : chunk.aborted
        ? "canceled"
        : emptyResponse
          ? "failed"
          : "completed",
  );
  acc.response = resolvedAssistantContent;

  if (ctx.persistConversation !== false) {
    saveConversationIfNeeded(ctx, savedCommentaryBlocks, savedCommentaryDurations);
    if (!emptyResponse) maybeGenerateSessionTitle(ctx);
  }

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
        resultSummary: step.resultSummary || MANUAL_ABORT_MESSAGE,
      };
    }),
  };
}

function saveConversationIfNeeded(
  ctx: Parameters<ChunkHandler>[1],
  savedCommentaryBlocks: string[],
  savedCommentaryDurations: number[],
): void {
  const { acc } = ctx;
  const respTrim = (acc.response || "").trim();
  const commentaryTrim = (acc.commentary || "").trim();
  const shouldSave =
    Boolean(acc.sessionId) &&
    Boolean(
      respTrim ||
        commentaryTrim ||
        acc.taskPlan ||
        savedCommentaryBlocks.length > 0 ||
        (acc.toolCallSegments?.length ?? 0) > 0 ||
        acc.longTaskId ||
        acc.canonicalOutput,
    );
  if (!shouldSave) return;

  void getServices()
    .then((services) => services.conversations.saveConversation({
      sessionId: acc.sessionId,
      bookId: acc.bookId ?? undefined,
      chapterId: acc.chapterId ?? null,
      prompt: acc.userText,
      response: acc.response || "",
      model: acc.model || undefined,
      commentary: acc.commentary || undefined,
      commentaryBlocks: savedCommentaryBlocks.length
        ? savedCommentaryBlocks
        : undefined,
      commentaryDurationsMs: savedCommentaryDurations.length
        ? savedCommentaryDurations
        : undefined,
      durationMs: Math.max(0, Math.round(performance.now() - acc.turnStartedAt)),
      toolCallSegments: acc.toolCallSegments?.length
        ? acc.toolCallSegments
        : undefined,
      taskPlan: acc.taskPlan ?? undefined,
      contextCompaction: acc.contextCompaction,
      contextBudget: acc.contextBudget,
      agentProcess: (
        acc.delegations?.length || acc.subAgentActivities?.length
      ) ? {
          delegations: acc.delegations,
          subAgentActivities: acc.subAgentActivities,
        } : undefined,
      agentRunId: acc.conversationRunId ?? acc.agentRunId,
    }))
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
  const titleSource = (acc.response || acc.commentary || "").trim();
  if (!acc.needsTitle || !acc.sessionId || !titleSource) return;

  console.log("[AI 对话] 请求生成标题", {
    sessionId: acc.sessionId,
    apiProvider: normalizeApiProvider(cfg.apiProvider),
    model: apiModelName,
  });
  getServices()
    .then((services) => services.ai.generateSessionTitle({
      apiKey: cfg.apiKey,
      baseURL: cfg.baseUrl || undefined,
      prompt:
        `User:\n${acc.userText}\n\nAssistant:\n${titleSource}`.trim(),
      apiProvider: normalizeApiProvider(cfg.apiProvider),
      model: apiModelName,
    }))
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
      const services = await getServices()
      await services.sessions.updateSessionTitle({
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
