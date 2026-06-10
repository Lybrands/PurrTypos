import { mergeAssistantErrorNotice } from "../../rendering";
import { isWritingExpertPipeline, type ChatMessage } from "../chat.types";
import {
  summarizeSubagentResult,
  synthesizeAssistantTextFromToolSegments,
} from "../chatHistory";
import type { ChunkHandler } from "./types";

/**
 * 错误终态：把错误注释合并到当前助手轮，关 loading，cleanup 订阅与 refs。
 */
export const handleError: ChunkHandler = (chunk, ctx) => {
  if (!chunk.error) return;
  const { acc } = ctx;

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
 * 正常终态：合并最终内容（含 thinking 收尾、subagent 阶段定稿、digest 拼接），
 * cleanup，落库，按需生成会话标题。
 */
export const handleDone: ChunkHandler = (chunk, ctx) => {
  if (!chunk.done) return;
  const { acc } = ctx;
  if (chunk.model) acc.model = chunk.model;

  const finalThinking = (acc.thinking || "").trim();
  const savedThinkingBlocks = finalThinking
    ? [...(acc.thinkingBlocks ?? []), finalThinking]
    : (acc.thinkingBlocks ?? []);

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
        const blocks = (last as ChatMessage).thinkingBlocks ?? [];
        const thinkingBlocks = finalThinking
          ? [...blocks, finalThinking]
          : blocks;
        const cm = last as ChatMessage;
        const stages = cm.subagentStages ?? [];
        const subagentStagesFinalized =
          isWritingExpertPipeline(ctx.agentMode) && stages.length > 0
            ? stages.map((s) =>
                s.status === "running"
                  ? { ...s, status: "done" as const }
                  : s,
              )
            : cm.subagentStages;
        const digestTrim = (cm.subagentPipelineDigest ?? "").trim();
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
              const subSummary = summarizeSubagentResult(cm);
              finalContent = subSummary || "内容同步中。";
            }
          }
        }
        if (digestTrim) {
          finalContent = finalContent.trim()
            ? `${digestTrim}\n\n${finalContent.trim()}`
            : digestTrim;
        }
        resolvedAssistantContent = finalContent;
        next[next.length - 1] = {
          ...last,
          content: finalContent,
          subagentPipelineDigest: undefined,
          model: acc.model || undefined,
          thinking: finalThinking || last.thinking,
          thinkingBlocks: thinkingBlocks.length ? thinkingBlocks : undefined,
          toolCalling: false,
          subagentStageWorking: false,
          ...(isWritingExpertPipeline(ctx.agentMode)
            ? {
                subagentBridging: false,
                ...(subagentStagesFinalized
                  ? { subagentStages: subagentStagesFinalized }
                  : {}),
              }
            : {}),
        };
      }
      return next;
    });
    ctx.setLoading(false);
  }

  ctx.cleanup();
  acc.response = resolvedAssistantContent;

  saveConversationIfNeeded(ctx, savedThinkingBlocks);
  maybeGenerateSessionTitle(ctx);

  return true;
};

function saveConversationIfNeeded(
  ctx: Parameters<ChunkHandler>[1],
  savedThinkingBlocks: string[],
): void {
  const { acc } = ctx;
  const respTrim = (acc.response || "").trim();
  const thinkTrim = (acc.thinking || "").trim();
  const shouldSave =
    Boolean(acc.sessionId) &&
    Boolean(
      respTrim ||
        thinkTrim ||
        savedThinkingBlocks.length > 0 ||
        (acc.toolCallSegments?.length ?? 0) > 0,
    );
  if (!shouldSave) return;

  void window.electronAPI
    .saveConversation({
      sessionId: acc.sessionId,
      chapterId: acc.chapterId ?? null,
      prompt: acc.userText,
      response: acc.response || "",
      model: acc.model || undefined,
      thinking: acc.thinking || undefined,
      thinkingBlocks: savedThinkingBlocks.length
        ? savedThinkingBlocks
        : undefined,
      toolCallSegments: acc.toolCallSegments?.length
        ? acc.toolCallSegments
        : undefined,
      subagentResult: acc.subagentResult ?? undefined,
    })
    .then((res) => {
      if (res && res.success) return;
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
