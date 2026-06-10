import React from "react";
import { App as AntdApp } from "antd";
import type { WritingSubagentRole } from "../pipelineStages";
import { parseWritingSlashCommand } from "../pipelineStages";
import {
  isWritingExpertPipeline,
  type ChatMessage,
  type ToolCallLabelOutcome,
  type ToolCallSegment,
  type UseChatSubmitParams,
} from "./chat.types";
import { buildHistoryConverter } from "./chatHistory";
import { buildStreamOptions } from "./streamOptions";
import {
  dispatchChunk,
  type AccState,
  type ChunkCtx,
} from "./chunkHandlers";

export {
  isWritingExpertPipeline,
  type ChatMessage,
  type ToolCallLabelOutcome,
  type ToolCallSegment,
  type UseChatSubmitParams,
};

export function useChatSubmit(params: UseChatSubmitParams) {
  const {
    selectedModelConfig,
    prompt,
    setPrompt,
    loading,
    setLoading,
    conversations,
    setConversations,
    bookId,
    chapterId,
    activeSessionId,
    setActiveSessionId,
    sessions,
    setSessions,
    associatedChapterIds,
    associatedOutlineIds,
    writingChapters,
    availableOutlines,
    currentChapterTitle,
    selectedModel,
    thinkingEnabled,
    agentEnabled,
    modelConfigs,
    selectedMemoryIds,
    selectedForeshadowingIds,
    agentMode = "legacy",
    writingMode = "default",
    pendingSubagentRole = null,
    onPendingSubagentRoleConsumed,
  } = params;

  const { message: appMessage } = AntdApp.useApp();
  const unsubscribeRef = React.useRef<(() => void) | null>(null);
  const visibleSessionIdRef = React.useRef<number | null>(activeSessionId);
  const runningSessionIdRef = React.useRef<number | null>(null);
  const runningAccRef = React.useRef<{
    response: string;
    thinking: string;
    userText: string;
    toolCallSegments?: ToolCallSegment[];
    thinkingBlocks?: string[];
    /** 与 React 消息 state 一致：本轮工具批次之后的流式正文后缀 */
    contentAfterToolCalls?: string;
  } | null>(null);

  React.useEffect(() => {
    visibleSessionIdRef.current = activeSessionId;
  }, [activeSessionId]);

  const handleAbort = React.useCallback(() => {
    window.electronAPI.abortAiStream();
    // 不在此处 unsubscribe：须等主进程发来 done（含 aborted），才能合并状态并入库
    setLoading(false);
  }, [setLoading]);

  /** 可选：从某条用户消息重新编辑并发送，会丢弃该条之后的所有消息 */
  const handleSubmit = React.useCallback(
    async (resend?: { editIndex: number; content: string }) => {
      const rawUserText = (resend?.content ?? prompt).trim();
      if (!rawUserText || loading) return;

      const slash = parseWritingSlashCommand(rawUserText);
      const effectiveUserText = slash.stripped || rawUserText;
      const effSubagentRole: WritingSubagentRole | null =
        slash.role ?? pendingSubagentRole ?? null;
      onPendingSubagentRoleConsumed?.();

      const userText = effectiveUserText;
      if (!userText) {
        appMessage.warning("请输入有效内容");
        return;
      }

      const cfg = selectedModelConfig;
      const forceNoThinking = isWritingExpertPipeline(agentMode);
      const expectThinking = forceNoThinking
        ? false
        : cfg
          ? cfg.thinkingOnly || (cfg.supportsThinking && thinkingEnabled)
          : thinkingEnabled;
      const assistantPlaceholder = {
        role: "assistant" as const,
        content: "",
        ...(expectThinking ? { thinking: "" } : {}),
      };

      if (!cfg?.apiKey?.trim()) {
        if (resend != null) {
          setConversations((prev) => [
            ...prev.slice(0, resend.editIndex),
            { role: "user", content: resend.content.trim() },
            {
              role: "assistant",
              content: "请先在设置中添加模型并填写 API Key",
              isError: true,
            },
          ]);
        } else {
          setConversations((prev) => [
            ...prev,
            { role: "user", content: userText },
            {
              role: "assistant",
              content: "请先在设置中添加模型并填写 API Key",
              isError: true,
            },
          ]);
        }
        setPrompt("");
        return;
      }
      if (bookId == null || chapterId == null) {
        appMessage.warning("请先选择一个章节，再开始对话");
        return;
      }
      if (activeSessionId == null) {
        appMessage.warning("请先点击上方「+」新建对话，或从历史记录打开会话");
        return;
      }
      const sessionId = activeSessionId;

    // 立刻把用户消息 + 助手占位推到 UI，并进入 loading
    if (resend != null) {
      const nextConversations = [
        ...conversations.slice(0, resend.editIndex),
        { role: "user" as const, content: resend.content.trim() },
        assistantPlaceholder,
      ];
      setConversations(nextConversations);
      setPrompt("");
      setLoading(true);
    } else {
      setConversations((prev) => [
        ...prev,
        { role: "user", content: userText },
        assistantPlaceholder,
      ]);
      setPrompt("");
      setLoading(true);
    }

    // 系统提示（会话绑定说明、关联章节/大纲内容、勾选记忆）统一由后端组装注入；
    // 前端只传结构化字段（ids / 模式），不再拼接任何 prompt 文案。
    const toHistoryApiMessage = buildHistoryConverter(agentMode);

    let historyMessages: { role: string; content: string }[];
    if (resend != null) {
      const nextConversations = [
        ...conversations.slice(0, resend.editIndex),
        { role: "user" as const, content: resend.content.trim() },
        assistantPlaceholder,
      ];
      historyMessages = nextConversations
        .slice(0, -1)
        .map(toHistoryApiMessage)
        .filter((row): row is { role: string; content: string } => row != null)
        .slice(-50);
      // 从数据库删除「该条之后」的对话记录，与界面截断一致
      const keepTurnCount = Math.floor(resend.editIndex / 2);
      if (sessionId != null && keepTurnCount >= 0) {
        window.electronAPI.deleteConversationsAfterTurn({ sessionId, keepTurnCount }).catch(() => {});
      }
    } else {
      historyMessages = conversations
        .map(toHistoryApiMessage)
        .filter((row): row is { role: string; content: string } => row != null)
        .slice(-50);
    }

    const newMessages = [
      ...historyMessages,
      { role: "user", content: userText },
    ];

    const currentSessionTitle =
      sessions.find((s) => s.id === sessionId)?.title ?? "";
    const hasHistoryBeforeThisQuestion = historyMessages.length > 0;
    const isUntitledSession =
      currentSessionTitle.trim() === "" || currentSessionTitle === "新对话";
    const needsTitle =
      !hasHistoryBeforeThisQuestion && isUntitledSession;
    const acc: AccState = {
      response: "",
      thinking: "",
      sessionId,
      chapterId,
      needsTitle,
      userText,
      model: "",
      toolCallSegments: undefined,
      thinkingBlocks: [],
      contentAfterToolCalls: "",
      subagentPipelineDigest: "",
      subagentResult: undefined,
    };
    runningSessionIdRef.current = sessionId;
    runningAccRef.current = acc;

    const { options: streamOptions, apiModelName } = buildStreamOptions({
      cfg,
      modelConfigs,
      selectedModel,
      agentMode,
      thinkingEnabled,
    });

    let unsubscribe = (): void => {};
    const ctx: ChunkCtx = {
      acc,
      sessionId,
      agentMode,
      cfg,
      apiModelName,
      writingChapters,
      availableOutlines,
      setConversations,
      setLoading,
      setSessions,
      appMessage,
      isVisibleSession: () => visibleSessionIdRef.current === sessionId,
      cleanup: () => {
        unsubscribe();
        unsubscribeRef.current = null;
        runningSessionIdRef.current = null;
        runningAccRef.current = null;
      },
    };
    unsubscribe = window.electronAPI.onAiChunk((chunk) => {
      dispatchChunk(chunk, ctx);
    });

    unsubscribeRef.current = unsubscribe;

    const hasBookContext = bookId != null;
    const enableAgentTools = Boolean(hasBookContext && agentEnabled);

    if (import.meta.env.DEV) {
      console.log('[AI 对话] 传入内容:', { messages: newMessages, options: streamOptions, enableAgentTools });
    }

    window.electronAPI.aiChatStream({
      apiKey: cfg.apiKey,
      baseURL: cfg.baseUrl || undefined,
      apiProvider:
        cfg.apiProvider === "anthropic" ? "anthropic" : "openai",
      messages: newMessages,
      options: streamOptions,
      tools: [],
      enableAgentTools,
      bookId: bookId ?? undefined,
      chapterId: chapterId ?? undefined,
      currentChapterTitle: currentChapterTitle ?? undefined,
      writingChapters,
      availableOutlines,
      associatedChapterIds:
        associatedChapterIds.length > 0 ? associatedChapterIds : undefined,
      associatedOutlineIds:
        associatedOutlineIds.length > 0 ? associatedOutlineIds : undefined,
      selectedMemoryIds:
        selectedMemoryIds && selectedMemoryIds.length > 0
          ? selectedMemoryIds
          : undefined,
      selectedForeshadowingIds:
        selectedForeshadowingIds && selectedForeshadowingIds.length > 0
          ? selectedForeshadowingIds
          : undefined,
      agentMode,
      chatAgentMode:
        writingMode === "collab"
          ? "collab"
          : agentMode === "subagent"
            ? "expert"
            : agentEnabled
              ? "agent"
              : "ask",
      ...(writingMode === "collab" ? { writingMode: "collab" as const } : {}),
      ...(effSubagentRole ? { subagentRole: effSubagentRole } : {}),
    });
  }, [
    prompt,
    loading,
    selectedModelConfig,
    conversations,
    bookId,
    chapterId,
    currentChapterTitle,
    activeSessionId,
    associatedChapterIds,
    associatedOutlineIds,
    writingChapters,
    availableOutlines,
    sessions,
    selectedModel,
    thinkingEnabled,
    agentEnabled,
    modelConfigs,
    setPrompt,
    setLoading,
    setConversations,
    setActiveSessionId,
    setSessions,
    selectedMemoryIds,
    selectedForeshadowingIds,
    agentMode,
    writingMode,
    pendingSubagentRole,
    onPendingSubagentRoleConsumed,
    appMessage,
  ]);

  return { handleSubmit, handleAbort, runningSessionIdRef, runningAccRef };
}
