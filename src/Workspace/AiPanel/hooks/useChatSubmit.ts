import { services } from '@/services'
import React from "react";
import { usePurrToast } from '@/purr-components';
import {
  type ChatMessage,
  type ToolCallLabelOutcome,
  type ToolCallSegment,
  type UseChatSubmitParams,
} from "./chat.types";
import {
  buildHistoryConverter,
  createCommitScheduler,
  dispatchAgentChunk,
  initialAgentAccumulator,
  type AgentChunkRuntimeContext,
} from "../../../agent-runtime";
import { buildStreamOptions } from "../../../agent-runtime/streamOptions";
import { normalizeApiProvider } from "../../../modelCatalog";
import {
  countQueuedForSession,
  getSettledSessionActivity,
  type QueuedChatSubmission,
} from "./chatQueue";
import {
  getChatRuntimeVersion,
  getChatRuntimeQueue,
  getChatSessionActivities,
  getChatSessionRuntime,
  replaceChatRuntimeMessages,
  replaceChatRuntimeQueue,
  setChatRuntimeActivity,
  setChatRuntimeLoading,
  setChatRuntimeStreamId,
  subscribeChatRuntime,
  updateChatRuntimeMessages,
} from "./chatRuntimeStore";
import { createAiStreamId } from "../../../utils/aiStream";
import { createBookChunkHost } from './bookChunkHost'

export {
  type ChatMessage,
  type ToolCallLabelOutcome,
  type ToolCallSegment,
  type UseChatSubmitParams,
};

type ChatSubmitOverride = {
  editIndex?: number;
  content: string;
  queuedContext?: QueuedChatSubmission;
  preservePrompt?: boolean;
};

type SubmitResult = "started" | "queued" | "rejected";

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
    currentChapterTitle,
    selectedModel,
    agentEnabled,
    selectedMemoryIds,
    selectedForeshadowingIds,
    sessionScope = "chapter",
  } = params;

  const appMessage = usePurrToast();
  const runtimeVersion = React.useSyncExternalStore(
    subscribeChatRuntime,
    getChatRuntimeVersion,
    getChatRuntimeVersion,
  );
  const sessionActivities = React.useMemo(
    () => getChatSessionActivities(),
    [runtimeVersion],
  );
  const queuedSubmissions = getChatRuntimeQueue();
  const dequeueInProgressRef = React.useRef(false);
  const handleSubmitRef = React.useRef<
    ((override: ChatSubmitOverride) => SubmitResult) | null
  >(null);

  const replaceQueuedSubmissions = React.useCallback(
    (next: QueuedChatSubmission[]) => {
      replaceChatRuntimeQueue(next);
    },
    [],
  );

  React.useEffect(() => {
    dequeueInProgressRef.current = false;
  }, [bookId, chapterId, sessionScope]);

  React.useEffect(() => {
    const runtime = getChatSessionRuntime(activeSessionId);
    if (!runtime) {
      setLoading(false);
      return;
    }
    setConversations(runtime.messages);
    setLoading(runtime.loading);
  }, [
    activeSessionId,
    runtimeVersion,
    setConversations,
    setLoading,
  ]);

  const handleAbort = React.useCallback(() => {
    const runtime = getChatSessionRuntime(activeSessionId);
    services.ai.abortAiStream(runtime?.streamId);
    // 不在此处 unsubscribe：须等主进程发来 done（含 aborted），才能合并状态并入库
  }, [activeSessionId]);

  /** 可选：从某条用户消息重新编辑并发送，或直接发送指定内容 */
  const handleSubmit = React.useCallback(
    (submitOverride?: ChatSubmitOverride): SubmitResult => {
      const isResend = typeof submitOverride?.editIndex === "number";
      const rawUserText = (submitOverride?.content ?? prompt).trim();
      const queuedContext = submitOverride?.queuedContext;
      const targetSessionId = queuedContext?.sessionId ?? activeSessionId;
      const targetRuntime = getChatSessionRuntime(targetSessionId);
      const sessionLoading =
        targetRuntime?.loading ??
        (targetSessionId === activeSessionId ? loading : false);
      if (!rawUserText || (sessionLoading && isResend)) return "rejected";

      const userText = rawUserText;
      if (!userText) {
        appMessage.warning("请输入有效内容");
        return "rejected";
      }

      const cfg = queuedContext?.selectedModelConfig ?? selectedModelConfig;
      const requestSelectedModel =
        queuedContext?.selectedModel ?? selectedModel;
      const requestAgentEnabled =
        queuedContext?.agentEnabled ?? agentEnabled;
      const turnStartedAt = performance.now();
      const userSentAt = new Date().toISOString();
      const assistantPlaceholder = {
        role: "assistant" as const,
        content: "",
        clientTurnId: createAiStreamId("turn"),
        turnStartedAt,
        commentary: "",
      };

      if (!cfg?.apiKey?.trim()) {
        if (sessionLoading || submitOverride?.queuedContext) {
          appMessage.warning("请先在设置中添加模型并填写 API Key");
          return "rejected";
        }
        if (isResend) {
          setConversations((prev) => [
            ...prev.slice(0, submitOverride.editIndex!),
            { role: "user", content: submitOverride.content.trim(), sentAt: userSentAt },
            {
              role: "assistant",
              content: "请先在设置中添加模型并填写 API Key",
              isError: true,
            },
          ]);
        } else {
          setConversations((prev) => [
            ...prev,
            { role: "user", content: userText, sentAt: userSentAt },
            {
              role: "assistant",
              content: "请先在设置中添加模型并填写 API Key",
              isError: true,
            },
          ]);
        }
        if (!submitOverride?.preservePrompt) setPrompt("");
        return "rejected";
      }
      if (bookId == null || (sessionScope !== "setting" && chapterId == null)) {
        appMessage.warning("请先选择一个章节，再开始对话");
        return "rejected";
      }
      if (targetSessionId == null) {
        appMessage.warning("请先点击上方「+」新建对话，或从历史记录打开会话");
        return "rejected";
      }
      const sessionId = targetSessionId;
      if (sessionLoading) {
        const queuedItem: QueuedChatSubmission = {
          content: userText,
          sessionId,
          selectedModel,
          selectedModelConfig: cfg,
          agentEnabled,
          associatedChapterIds: [...associatedChapterIds],
          associatedOutlineIds: [...associatedOutlineIds],
          selectedMemoryIds: [...(selectedMemoryIds ?? [])],
          selectedForeshadowingIds: [...(selectedForeshadowingIds ?? [])],
        };
        const nextQueue = [...getChatRuntimeQueue(), queuedItem];
        const queuedCount = countQueuedForSession(nextQueue, sessionId);
        replaceQueuedSubmissions(nextQueue);
        setChatRuntimeActivity(sessionId, {
          state: "running",
          queuedCount,
        });
        if (!submitOverride?.preservePrompt) setPrompt("");
        appMessage.info(`已加入发送队列 · ${queuedCount} 条等待中`);
        return "queued";
      }

      const requestAssociatedChapterIds =
        queuedContext?.associatedChapterIds ?? associatedChapterIds;
      const requestAssociatedOutlineIds =
        queuedContext?.associatedOutlineIds ?? associatedOutlineIds;
      const requestSelectedMemoryIds =
        queuedContext?.selectedMemoryIds ?? selectedMemoryIds;
      const requestSelectedForeshadowingIds =
        queuedContext?.selectedForeshadowingIds ?? selectedForeshadowingIds;

    const baseConversations =
      getChatSessionRuntime(sessionId)?.messages ?? conversations;

    // 立刻把用户消息 + 助手占位推到运行存储，并进入 loading
    let nextConversations: ChatMessage[];
    if (isResend) {
      nextConversations = [
        ...baseConversations.slice(0, submitOverride.editIndex!),
        { role: "user" as const, content: submitOverride.content.trim(), sentAt: userSentAt },
        assistantPlaceholder,
      ];
      if (!submitOverride?.preservePrompt) setPrompt("");
    } else {
      nextConversations = [
        ...baseConversations,
        { role: "user", content: userText, sentAt: userSentAt },
        assistantPlaceholder,
      ];
      if (!submitOverride?.preservePrompt) setPrompt("");
    }
    replaceChatRuntimeMessages(sessionId, nextConversations);
    setChatRuntimeLoading(sessionId, true);
    setChatRuntimeActivity(sessionId, {
      state: "running",
      queuedCount: countQueuedForSession(
        getChatRuntimeQueue(),
        sessionId,
      ),
    });

    // 系统提示（会话绑定说明、关联章节/大纲内容、勾选记忆）统一由后端组装注入；
    // 前端只传结构化字段（ids / 模式），不再拼接任何 prompt 文案。
    const toHistoryApiMessage = buildHistoryConverter();

    let historyMessages: { role: string; content: string }[];
    if (isResend) {
      historyMessages = nextConversations
        .slice(0, -1)
        .map(toHistoryApiMessage)
        .filter((row): row is { role: string; content: string } => row != null);
      // 从数据库删除「该条之后」的对话记录，与界面截断一致
      const keepTurnCount = Math.floor(submitOverride.editIndex! / 2);
      if (sessionId != null && keepTurnCount >= 0) {
        services.conversations.deleteConversationsAfterTurn({ sessionId, keepTurnCount }).catch(() => {});
      }
    } else {
      historyMessages = baseConversations
        .map(toHistoryApiMessage)
        .filter((row): row is { role: string; content: string } => row != null);
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
    const acc = initialAgentAccumulator({
      sessionId,
      userText,
      turnStartedAt,
    });
    const { options: streamOptions, apiModelName } = buildStreamOptions({
      cfg,
      selectedModel: requestSelectedModel,
    });

    const streamId = createAiStreamId(`chat-${sessionId}`);
    setChatRuntimeStreamId(sessionId, streamId);
    const runtimeSetConversations: React.Dispatch<
      React.SetStateAction<ChatMessage[]>
    > = (next) => {
      if (typeof next === "function") {
        updateChatRuntimeMessages(sessionId, next);
      } else {
        replaceChatRuntimeMessages(sessionId, next);
      }
    };
    let unsubscribe = (): void => {};
    const { scheduleCommit, flushCommits } = createCommitScheduler(
      (updater) => runtimeSetConversations(updater),
    );
    const host = createBookChunkHost({
      sessionId,
      bookId,
      chapterId,
      needsTitle,
      modelConfig: cfg,
      apiModelName,
      readMessages: () => getChatSessionRuntime(sessionId)?.messages ?? [],
      replaceMessages: (messages) => replaceChatRuntimeMessages(sessionId, messages),
      scheduleCommit,
      flushCommits,
      setRunning: (running) => setChatRuntimeLoading(sessionId, running),
      isVisible: () => true,
      setSessions: (updater) => setSessions(updater),
      appMessage,
      unsubscribe: () => unsubscribe(),
      clearStream: () => setChatRuntimeStreamId(sessionId, undefined),
      getQueue: getChatRuntimeQueue,
      replaceQueue: replaceChatRuntimeQueue,
      setActivity: (activity) => setChatRuntimeActivity(sessionId, activity),
      isSessionRunning: () => Boolean(getChatSessionRuntime(sessionId)?.loading),
      submitQueued: (submission) => {
        handleSubmitRef.current?.({
          content: submission.content,
          queuedContext: submission,
          preservePrompt: true,
        })
      },
    });
    const ctx: AgentChunkRuntimeContext = {
      acc,
      sessionId,
      modelIdentity: {
        configId: cfg.id,
        name: apiModelName,
      },
      host,
      now: () => performance.now(),
    };
    unsubscribe = services.ai.onAiChunk(
      (chunk) => {
        dispatchAgentChunk(chunk, ctx);
      },
      streamId,
    );

    const hasBookContext = bookId != null;
    const enableAgentTools = Boolean(hasBookContext && requestAgentEnabled);

    if (import.meta.env.DEV) {
      console.log('[AI 对话] 传入内容:', { messages: newMessages, options: streamOptions, enableAgentTools });
    }

    services.ai.aiChatStream({
      streamId,
      apiKey: cfg.apiKey,
      baseURL: cfg.baseUrl || undefined,
      apiProvider: normalizeApiProvider(cfg.apiProvider),
      locale: document.documentElement.lang || "zh-CN",
      sessionId,
      messages: newMessages,
      options: streamOptions,
      enableAgentTools,
      bookId: bookId ?? undefined,
      chapterId: chapterId ?? undefined,
      currentChapterTitle: currentChapterTitle ?? undefined,
      associatedChapterIds:
        requestAssociatedChapterIds.length > 0
          ? requestAssociatedChapterIds
          : undefined,
      associatedOutlineIds:
        requestAssociatedOutlineIds.length > 0
          ? requestAssociatedOutlineIds
          : undefined,
      selectedMemoryIds:
        requestSelectedMemoryIds && requestSelectedMemoryIds.length > 0
          ? requestSelectedMemoryIds
          : undefined,
      selectedForeshadowingIds:
        requestSelectedForeshadowingIds &&
        requestSelectedForeshadowingIds.length > 0
          ? requestSelectedForeshadowingIds
          : undefined,
      chatAgentMode: requestAgentEnabled ? "agent" : "ask",
      contextWindow: streamOptions.context_window,
    });
    return "started";
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
    sessions,
    selectedModel,
    agentEnabled,
    setPrompt,
    setLoading,
    setConversations,
    setActiveSessionId,
    setSessions,
    selectedMemoryIds,
    selectedForeshadowingIds,
    sessionScope,
    appMessage,
    replaceQueuedSubmissions,
  ]);
  handleSubmitRef.current = handleSubmit;

  React.useEffect(() => {
    if (dequeueInProgressRef.current || queuedSubmissions.length === 0) return;

    const readyIndex = queuedSubmissions.findIndex(
      (submission) =>
        !getChatSessionRuntime(submission.sessionId)?.loading,
    );
    if (readyIndex < 0) return;

    const nextSubmission = queuedSubmissions[readyIndex];
    const remainingQueue = queuedSubmissions.filter(
      (_submission, index) => index !== readyIndex,
    );
    dequeueInProgressRef.current = true;
    replaceQueuedSubmissions(remainingQueue);
    const result = handleSubmit({
      content: nextSubmission.content,
      queuedContext: nextSubmission,
      preservePrompt: true,
    });
    queueMicrotask(() => {
      dequeueInProgressRef.current = false;
    });
    if (result !== "started") {
      const queuedCount = countQueuedForSession(
        getChatRuntimeQueue(),
        nextSubmission.sessionId,
      );
      setChatRuntimeActivity(
        nextSubmission.sessionId,
        getSettledSessionActivity(
          "failed",
          queuedCount,
        ),
      );
    }
  }, [
    handleSubmit,
    queuedSubmissions,
    replaceQueuedSubmissions,
    runtimeVersion,
  ]);

  const activeQueuedSubmissions = queuedSubmissions.filter(
    (submission) => submission.sessionId === activeSessionId,
  );
  return {
    handleSubmit,
    handleAbort,
    queuedCount: activeQueuedSubmissions.length,
    queuedMessages: activeQueuedSubmissions.map((item) => item.content),
    sessionActivities,
  };
}
