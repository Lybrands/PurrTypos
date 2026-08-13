import { services } from '@/services'
import React from "react";
import { usePurrToast } from '@/purr-components';
import {
  type AgentConversationMessage,
} from "../../../agent-runtime/contracts";
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
  setChatRuntimeStopping,
  setChatRuntimeStreamId,
  subscribeChatRuntime,
  updateChatRuntimeMessages,
} from "./chatRuntimeStore";
import { createAiStreamId } from "../../../utils/aiStream";
import { createBookChunkHost } from './bookChunkHost'
import type {
  AiModelConfig,
  AiSession,
  EntityId,
  SettingDiffCardState,
} from '../../../types'
import {
  createDurableBookRunControl,
  type DurableBookRunControl,
} from './bookRunControl'
import {
  createBookProposalProjectionReconciler,
  shouldRefreshBookProposalProjection,
} from '../bookProposalProjection'
import type { BookSettingDiffAttachmentOwner } from './bookSettingDiff'

export interface UseChatSubmitParams {
  /** 当前选中的模型配置（含 apiKey、baseUrl、name）；为空时无法发送 */
  selectedModelConfig: AiModelConfig | null
  prompt: string
  setPrompt: React.Dispatch<React.SetStateAction<string>>
  loading: boolean
  setLoading: React.Dispatch<React.SetStateAction<boolean>>
  conversations: AgentConversationMessage[]
  setConversations: React.Dispatch<React.SetStateAction<AgentConversationMessage[]>>
  bookId: EntityId | null | undefined
  chapterId: EntityId | null | undefined
  activeSessionId: number | null
  sessions: AiSession[]
  setSessions: React.Dispatch<React.SetStateAction<AiSession[]>>
  associatedChapterIds: EntityId[]
  associatedOutlineIds: EntityId[]
  currentChapterTitle?: string
  selectedModel: string
  agentEnabled: boolean
  selectedMemoryIds?: (number | string)[]
  selectedForeshadowingIds?: (number | string)[]
  /**
   * 会话作用域：setting = 全局会话（不绑章节），不要求选中章节即可发送；
   * 默认 chapter（必须先选章节）。
   */
  sessionScope?: "chapter" | "setting"
  onAssistantAttachment?: (
    message: AgentConversationMessage,
    card: SettingDiffCardState,
    owner: BookSettingDiffAttachmentOwner,
  ) => void
  associateAssistantIdentities?: (
    source: AgentConversationMessage,
    target: AgentConversationMessage,
  ) => void
  productAgentProcess?: (
    message: AgentConversationMessage,
  ) => Record<string, unknown> | undefined
  onPersistenceConflict?(sessionId: number): void
}

type ChatSubmitOverride = {
  editIndex?: number;
  content: string;
  queuedContext?: QueuedChatSubmission;
  preservePrompt?: boolean;
  truncationCommitted?: boolean;
  resendBaseMessages?: AgentConversationMessage[];
};

type SubmitResult = "started" | "queued" | "rejected";

const durableControls = new Map<number, {
  streamId: string
  control: DurableBookRunControl
}>()

const pendingTruncations = new Map<number, { canceled: boolean }>()
const pendingTerminalPersistence = new Map<number, { canceled: boolean }>()

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
    onAssistantAttachment,
    associateAssistantIdentities,
    productAgentProcess,
    onPersistenceConflict,
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
    // Settled runtime messages are only an in-process cache. A fresh Run
    // journal hydrate owns settled state and must not be overwritten here.
    if (runtime.loading || runtime.stopping || runtime.streamId) {
      setConversations(runtime.messages);
    }
    setLoading(runtime.loading);
  }, [
    activeSessionId,
    runtimeVersion,
    setConversations,
    setLoading,
  ]);

  const createDurableControl = React.useCallback((
    sessionId: number,
    streamId: string,
  ): DurableBookRunControl => {
    const control = createDurableBookRunControl({
      requestId: streamId.startsWith('recovered-run:') ? undefined : streamId,
      cancelRequest: async (requestId) => {
        const result = await services.ai.cancelWritingChatRequest({ requestId })
        if (!result.success) {
          throw new Error(result.error || '停止 Agent 请求失败')
        }
      },
      cancelRun: async (runId) => {
        const result = await services.ai.cancelAgentRun({ runId })
        if (!result.success) throw new Error(result.error || '停止 Agent 失败')
      },
      setStopping: (stopping) => setChatRuntimeStopping(sessionId, stopping),
      onCancelError: (error) => {
        const message = error instanceof Error ? error.message : String(error)
        appMessage.error(message || '停止 Agent 失败')
      },
    })
    durableControls.set(sessionId, { streamId, control })
    return control
  }, [appMessage])

  const handleAbort = React.useCallback(async () => {
    const runtime = getChatSessionRuntime(activeSessionId);
    if (activeSessionId == null) return
    const pendingTruncation = pendingTruncations.get(activeSessionId)
    if (pendingTruncation) {
      pendingTruncation.canceled = true
      setChatRuntimeActivity(
        activeSessionId,
        getSettledSessionActivity(
          'canceled',
          countQueuedForSession(getChatRuntimeQueue(), activeSessionId),
        ),
      )
      return
    }
    if (!runtime?.streamId) {
      const persistence = pendingTerminalPersistence.get(activeSessionId)
      if (!persistence) return
      persistence.canceled = true
      pendingTerminalPersistence.delete(activeSessionId)
      replaceChatRuntimeQueue(
        getChatRuntimeQueue().filter(
          (submission) => submission.sessionId !== activeSessionId,
        ),
      )
      setChatRuntimeLoading(activeSessionId, false)
      setChatRuntimeActivity(
        activeSessionId,
        getSettledSessionActivity('canceled', 0),
      )
      return
    }
    let entry = durableControls.get(activeSessionId)
    if (entry?.streamId !== runtime.streamId) entry = undefined
    const lastMessage = runtime.messages.at(-1)
    const runId = lastMessage?.role === 'assistant'
      ? lastMessage.agentRunId
      : undefined
    if (!entry && runId) {
      const control = createDurableControl(activeSessionId, runtime.streamId)
      entry = { streamId: runtime.streamId, control }
    }
    if (entry) {
      entry.control.observeRunId(runId)
      entry.control.requestStop()
      await entry.control.cancelRequest()
      return
    }
    // Ask mode has no durable Run control plane and may stop its transport.
    services.ai.abortAiStream(runtime.streamId)
  }, [activeSessionId, createDurableControl]);

  /** 可选：从某条用户消息重新编辑并发送，或直接发送指定内容 */
  const handleSubmit = React.useCallback(
    (submitOverride?: ChatSubmitOverride): SubmitResult => {
      const isResend = typeof submitOverride?.editIndex === "number";
      const rawUserText = (submitOverride?.content ?? prompt).trim();
      const queuedContext = submitOverride?.queuedContext;
      const targetSessionId = queuedContext?.sessionId ?? activeSessionId;
      const targetRuntime = getChatSessionRuntime(targetSessionId);
      if (
        targetSessionId != null
        && pendingTruncations.has(targetSessionId)
        && !submitOverride?.truncationCommitted
      ) {
        return "rejected";
      }
      const sessionLoading =
        targetRuntime?.loading ??
        (targetSessionId === activeSessionId ? loading : false);
      if (
        !rawUserText
        || (sessionLoading && isResend && !submitOverride?.truncationCommitted)
      ) return "rejected";

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
      const clientTurnId = createAiStreamId("turn");
      const assistantPlaceholder = {
        role: "assistant" as const,
        content: "",
        clientTurnId,
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
            {
              role: "user",
              content: submitOverride.content.trim(),
              clientTurnId,
              sentAt: userSentAt,
            },
            {
              role: "assistant",
              content: "",
              clientTurnId,
              error: "请先在设置中添加模型并填写 API Key",
              isError: true,
            },
          ]);
        } else {
          setConversations((prev) => [
            ...prev,
            { role: "user", content: userText, clientTurnId, sentAt: userSentAt },
            {
              role: "assistant",
              content: "",
              clientTurnId,
              error: "请先在设置中添加模型并填写 API Key",
              isError: true,
            },
          ]);
        }
        if (!submitOverride?.preservePrompt) setPrompt("");
        return "rejected";
      }
      const requestBookId = queuedContext?.bookId ?? bookId
      const requestChapterId = queuedContext?.chapterId ?? chapterId ?? null
      const requestSessionScope = queuedContext?.sessionScope ?? sessionScope
      const requestCurrentChapterTitle = queuedContext?.currentChapterTitle
        ?? currentChapterTitle
      const requestLocale = queuedContext?.locale
        ?? document.documentElement.lang
        ?? 'zh-CN'
      if (
        requestBookId == null
        || (requestSessionScope !== "setting" && requestChapterId == null)
      ) {
        appMessage.warning("请先选择一个章节，再开始对话");
        return "rejected";
      }
      if (targetSessionId == null) {
        appMessage.warning("请先点击上方「+」新建对话，或从历史记录打开会话");
        return "rejected";
      }
      const sessionId = targetSessionId;
      if (sessionLoading && !submitOverride?.truncationCommitted) {
        const currentSessionTitle =
          sessions.find((session) => session.id === sessionId)?.title ?? ''
        const queuedItem: QueuedChatSubmission = {
          content: userText,
          sessionId,
          bookId: requestBookId,
          chapterId: requestChapterId,
          sessionScope: requestSessionScope,
          currentChapterTitle: requestCurrentChapterTitle,
          locale: requestLocale,
          needsTitle: currentSessionTitle.trim() === '' || currentSessionTitle === '新对话',
          selectedModel: requestSelectedModel,
          selectedModelConfig: { ...cfg },
          agentEnabled: requestAgentEnabled,
          associatedChapterIds: [
            ...(queuedContext?.associatedChapterIds ?? associatedChapterIds),
          ],
          associatedOutlineIds: [
            ...(queuedContext?.associatedOutlineIds ?? associatedOutlineIds),
          ],
          selectedMemoryIds: [
            ...(queuedContext?.selectedMemoryIds ?? selectedMemoryIds ?? []),
          ],
          selectedForeshadowingIds: [
            ...(queuedContext?.selectedForeshadowingIds ?? selectedForeshadowingIds ?? []),
          ],
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

    const baseConversations = submitOverride?.resendBaseMessages
      ?? getChatSessionRuntime(sessionId)?.messages
      ?? conversations;

    if (isResend && !submitOverride.truncationCommitted) {
      const editIndex = submitOverride.editIndex!;
      const keepTurnCount = Math.floor(editIndex / 2);
      const currentSessionTitle =
        sessions.find((s) => s.id === sessionId)?.title ?? "";
      const prefixHasHistory = editIndex > 0;
      const frozenContext: QueuedChatSubmission = {
        content: userText,
        sessionId,
        bookId: requestBookId,
        chapterId: requestChapterId,
        sessionScope: requestSessionScope,
        currentChapterTitle: requestCurrentChapterTitle,
        locale: requestLocale,
        needsTitle: !prefixHasHistory && (
          currentSessionTitle.trim() === ""
          || currentSessionTitle === "新对话"
        ),
        selectedModel: requestSelectedModel,
        selectedModelConfig: { ...cfg },
        agentEnabled: requestAgentEnabled,
        associatedChapterIds: [...requestAssociatedChapterIds],
        associatedOutlineIds: [...requestAssociatedOutlineIds],
        selectedMemoryIds: [...(requestSelectedMemoryIds ?? [])],
        selectedForeshadowingIds: [
          ...(requestSelectedForeshadowingIds ?? []),
        ],
      };
      replaceChatRuntimeMessages(sessionId, baseConversations);
      setChatRuntimeLoading(sessionId, true);
      setChatRuntimeActivity(sessionId, {
        state: "running",
        queuedCount: countQueuedForSession(getChatRuntimeQueue(), sessionId),
      });
      const truncation = { canceled: false };
      pendingTruncations.set(sessionId, truncation);
      const tailMessages = baseConversations.slice(submitOverride.editIndex!);
      const retireConversationIds = [...new Set(
        tailMessages
          .map((message) => message.conversationId)
          .filter((id): id is number => Number.isInteger(id) && Number(id) > 0),
      )];
      const expectedConversationIds = [...new Set(
        baseConversations
          .map((message) => message.conversationId)
          .filter((id): id is number => Number.isInteger(id) && Number(id) > 0),
      )];
      const retireRunIds = [...new Set(
        tailMessages
          .map((message) => message.agentRunId)
          .filter((id): id is string => Boolean(id)),
      )];
      const expectedRunIds = [...new Set(
        baseConversations
          .map((message) => message.agentRunId)
          .filter((id): id is string => Boolean(id)),
      )];
      const retireClientTurnIds = [...new Set(
        tailMessages
          .map((message) => message.clientTurnId)
          .filter((id): id is string => Boolean(id)),
      )];
      void services.conversations.deleteConversationsAfterTurn({
        sessionId,
        keepTurnCount,
        expectedConversationIds,
        retireConversationIds,
        retireRunIds,
        expectedRunIds,
        retireClientTurnIds,
      }).then((result) => {
        if (!result.success) {
          if (
            typeof result.httpStatus === 'number'
            && result.httpStatus >= 400
            && result.httpStatus < 500
          ) {
            replaceChatRuntimeQueue(
              getChatRuntimeQueue().filter(
                (submission) => submission.sessionId !== sessionId,
              ),
            )
            onPersistenceConflict?.(sessionId)
          }
          throw new Error(result.error || "截断对话失败");
        }
        if (truncation.canceled) {
          const prefix = baseConversations.slice(0, submitOverride.editIndex!);
          replaceChatRuntimeMessages(sessionId, prefix);
          if (sessionId === activeSessionId) setConversations(prefix);
          setChatRuntimeLoading(sessionId, false);
          return;
        }
        setChatRuntimeLoading(sessionId, false);
        if (pendingTruncations.get(sessionId) === truncation) {
          pendingTruncations.delete(sessionId);
        }
        handleSubmitRef.current?.({
          ...submitOverride,
          queuedContext: frozenContext,
          preservePrompt: true,
          truncationCommitted: true,
          resendBaseMessages: [...baseConversations],
        });
      }).catch((error: unknown) => {
        if (truncation.canceled) {
          setChatRuntimeLoading(sessionId, false);
          setChatRuntimeActivity(
            sessionId,
            getSettledSessionActivity(
              "failed",
              countQueuedForSession(getChatRuntimeQueue(), sessionId),
            ),
          );
          return;
        }
        setChatRuntimeLoading(sessionId, false);
        setChatRuntimeActivity(
          sessionId,
          getSettledSessionActivity(
            "failed",
            countQueuedForSession(getChatRuntimeQueue(), sessionId),
          ),
        );
        appMessage.error(
          error instanceof Error ? error.message : "截断对话失败，请重试",
        );
      }).finally(() => {
        if (pendingTruncations.get(sessionId) === truncation) {
          pendingTruncations.delete(sessionId);
        }
      });
      return "started";
    }

    // 立刻把用户消息 + 助手占位推到运行存储，并进入 loading
    let nextConversations: AgentConversationMessage[];
    if (isResend) {
      nextConversations = [
        ...baseConversations.slice(0, submitOverride.editIndex!),
        {
          role: "user" as const,
          content: submitOverride.content.trim(),
          clientTurnId,
          sentAt: userSentAt,
        },
        assistantPlaceholder,
      ];
      if (!submitOverride?.preservePrompt) setPrompt("");
    } else {
      nextConversations = [
        ...baseConversations,
        { role: "user", content: userText, clientTurnId, sentAt: userSentAt },
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
      historyMessages = baseConversations
        .slice(0, submitOverride.editIndex!)
        .map(toHistoryApiMessage)
        .filter((row): row is { role: string; content: string } => row != null);
    } else {
      historyMessages = baseConversations
        .map(toHistoryApiMessage)
        .filter((row): row is { role: string; content: string } => row != null);
    }

    const newMessages = [
      ...historyMessages,
      { role: "user", content: userText },
    ];
    const requestHistory = isResend
      ? baseConversations.slice(0, submitOverride.editIndex!)
      : baseConversations
    const expectedConversationIds = [...new Set(
      requestHistory
        .map((message) => message.conversationId)
        .filter((id): id is number => Number.isInteger(id) && Number(id) > 0),
    )]
    const expectedRunIds = [...new Set(
      requestHistory
        .map((message) => message.agentRunId)
        .filter((id): id is string => Boolean(id)),
    )]

    const currentSessionTitle =
      sessions.find((s) => s.id === sessionId)?.title ?? "";
    const hasHistoryBeforeThisQuestion = historyMessages.length > 0;
    const isUntitledSession =
      currentSessionTitle.trim() === "" || currentSessionTitle === "新对话";
    const needsTitle = queuedContext?.needsTitle
      ?? (!hasHistoryBeforeThisQuestion && isUntitledSession);
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
      React.SetStateAction<AgentConversationMessage[]>
    > = (next) => {
      if (typeof next === "function") {
        updateChatRuntimeMessages(sessionId, next);
      } else {
        replaceChatRuntimeMessages(sessionId, next);
      }
    };
    let unsubscribe = (): void => {};
    const durableControl = requestAgentEnabled
      ? createDurableControl(sessionId, streamId)
      : undefined
    const { scheduleCommit, flushCommits } = createCommitScheduler(
      (updater) => runtimeSetConversations(updater),
    );
    const persistenceAttempt = { canceled: false }
    const host = createBookChunkHost({
      sessionId,
      bookId: requestBookId,
      chapterId: requestChapterId,
      needsTitle,
      modelConfig: cfg,
      apiModelName,
      expectedConversationIds,
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
      onAssistantAttachment,
      attachmentOwner: {
        sessionId,
        bookId: requestBookId,
        chapterId: requestChapterId,
        prompt: userText,
      },
      associateAssistantIdentities,
      productAgentProcess,
      afterSettled: () => {
        if (pendingTerminalPersistence.get(sessionId) === persistenceAttempt) {
          pendingTerminalPersistence.delete(sessionId)
        }
        const entry = durableControls.get(sessionId)
        if (entry?.streamId === streamId) durableControls.delete(sessionId)
      },
      onPersistenceBlocked: () => {
        setChatRuntimeActivity(sessionId, {
          state: 'failed',
          queuedCount: countQueuedForSession(getChatRuntimeQueue(), sessionId),
        })
      },
      onPersistenceConflict: () => onPersistenceConflict?.(sessionId),
      onPersistenceStarted: () => {
        pendingTerminalPersistence.set(sessionId, persistenceAttempt)
      },
      onPersistenceAborted: () => {
        if (pendingTerminalPersistence.get(sessionId) === persistenceAttempt) {
          pendingTerminalPersistence.delete(sessionId)
        }
      },
      shouldRetryPersistence: () => Boolean(
        !persistenceAttempt.canceled
        && getChatSessionRuntime(sessionId)?.loading,
      ),
    });
    const ctx: AgentChunkRuntimeContext = {
      acc,
      sessionId,
      turnId: streamId,
      modelIdentity: {
        configId: cfg.id,
        name: apiModelName,
      },
      host,
      now: () => performance.now(),
    };
    const proposalProjection = createBookProposalProjectionReconciler({
      getRunSnapshot: (input) => services.ai.getAgentRunSnapshot(input),
      dispatch: (chunk) => dispatchAgentChunk(chunk, ctx),
    })
    unsubscribe = services.ai.onAiChunk(
      (chunk) => {
        const runId = chunk.runId || chunk.runResult?.runId
        durableControl?.observeRunId(runId)
        const authoritativeStatus = chunk.runResult?.status
        const requestStatus = chunk.requestResult?.status
        const requestTerminal = requestStatus === 'canceled'
          || requestStatus === 'rejected'
        if (requestTerminal) {
          durableControl?.observeAuthoritativeTerminal(
            requestStatus === 'canceled' ? 'canceled' : 'failed',
          )
        }
        if (
          durableControl?.stopping()
          && !authoritativeStatus
          && !requestTerminal
          && (chunk.done || chunk.error)
        ) return
        if (authoritativeStatus) {
          durableControl?.observeAuthoritativeTerminal(authoritativeStatus)
          // The Run terminal owns control/persistence/queue settlement and
          // must never wait on an optional product projection read. Durable
          // proposal reconciliation can finish against the same assistant
          // occurrence after the terminal has been delivered.
          dispatchAgentChunk(chunk, ctx)
          if (requestAgentEnabled && runId) {
            void proposalProjection.refreshFinal(runId)
          }
          return
        }
        dispatchAgentChunk(chunk, ctx);
        if (
          requestAgentEnabled
          && runId
          && shouldRefreshBookProposalProjection(chunk)
        ) {
          void proposalProjection.refresh(runId)
        }
      },
      streamId,
    );

    const hasBookContext = requestBookId != null;
    const enableAgentTools = Boolean(hasBookContext && requestAgentEnabled);

    if (import.meta.env.DEV) {
      console.log('[AI 对话] 传入内容:', { messages: newMessages, options: streamOptions, enableAgentTools });
    }

    services.ai.aiChatStream({
      streamId,
      apiKey: cfg.apiKey,
      baseURL: cfg.baseUrl || undefined,
      apiProvider: normalizeApiProvider(cfg.apiProvider),
      locale: requestLocale || "zh-CN",
      sessionId,
      messages: newMessages,
      options: streamOptions,
      enableAgentTools,
      bookId: requestBookId,
      chapterId: requestChapterId ?? undefined,
      currentChapterTitle: requestCurrentChapterTitle ?? undefined,
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
      expectedConversationIds: requestAgentEnabled
        ? expectedConversationIds
        : undefined,
      expectedRunIds: requestAgentEnabled ? expectedRunIds : undefined,
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
    setSessions,
    selectedMemoryIds,
    selectedForeshadowingIds,
    sessionScope,
    appMessage,
    replaceQueuedSubmissions,
    onAssistantAttachment,
    associateAssistantIdentities,
    productAgentProcess,
    onPersistenceConflict,
    createDurableControl,
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
    stopping: getChatSessionRuntime(activeSessionId)?.stopping ?? false,
  };
}
