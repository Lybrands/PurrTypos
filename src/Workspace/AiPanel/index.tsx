import { services } from '@/services'
import { loadCompleteAgentRunSnapshot, mergeAgentRunSnapshot } from '../../agent-runtime/runSnapshotHydration'
import { shouldRefreshBookProposalProjection } from './bookProposalProjection'
import { waitForAgentRetry } from '../../services/agentEventStream'
import type { CanonicalOperation } from '../../agent-runtime/canonicalOutput'
/// <reference path="../../vite-env.d.ts" />
import React from 'react'
import { usePurrToast, type PurrDropdownItem } from '@/purr-components'
import type {
  AiModelConfig,
  AiSession,
  Conversation,
  SettingDiffCardState,
} from '../../types'
import type { AgentConversationMessage } from '../../agent-runtime/contracts'
import { getActiveTaskPlan } from '../../agent-runtime/taskPlan'
import { buildUserQuotesPrefill } from '../../components/AgentConversation/userQuote'
import { AgentConversationPanel } from '../../components/AgentConversation'
import {
    useActiveChapterId,
    useActiveChapterTitle,
    useBookId,
    useBookTitle,
    useWritingChapters,
  } from '../../stores/workspaceStore'
import { useChatPrefillStore } from '../../stores/chatPrefillStore'
import { useQuoteStore } from '../../stores/quoteStore'
import { useSettingsInvalidationStore } from '../../stores/settingsInvalidationStore'
import {
  evictSettingDiffOwner,
  hydrateSettingDiffResolution,
  proposeSettingDiff,
  useAiProposalBridge,
} from '../../stores/aiProposalBridge'
import {
  useAssociatedContext,
  useAiModelPrefs,
  useAiSessions,
  useChatScopeMemory,
  useMemorySelection,
  usePromptTemplateContext,
  useChatSubmit,
} from './hooks'
import {
  getChatSessionRuntime,
  replaceChatRuntimeMessages,
  setChatRuntimeActivity,
  setChatRuntimeLoading,
  setChatRuntimeStopping,
  setChatRuntimeStreamId,
} from './hooks/chatRuntimeStore'
import {
  BookConversationHydrationError,
  hydrateBookConversationReadModel,
  hydrateLatestBookRun,
  mergeHydratedBookRun,
  type HydratedBookConversationReadModel,
} from './bookConversationHydration'
import {
  captureRecoveredRunProjectionOwner,
  canCommitRecoveredRunProjection,
  createConversationSessionLifecycle,
  readStableConversationProjection,
  recoveredRunProjectionControl,
  retryCurrentConversationRead,
} from './conversationSessionLifecycle'
import type { RecoveredRunProjectionOwner } from './conversationSessionLifecycle'
import { persistBookProposalResolution } from './proposalResolutionPersistence'
import FavoritesModal from './components/FavoritesModal'
import MemoryModal from './components/MemoryModal'
import AiPanelHeader from './components/AiPanelHeader'
import ComposerQuoteChip from './components/ComposerQuoteChip'
import './components/ComposerQuoteChip.scss'
import type { AiContextBarBindings } from './components/AiContextBar'
import {
  createBookAssistantAttachmentManager,
} from './bookAssistantAttachments'
import { useBookConversationController } from './useBookConversationController'
import { useBookConversationExtensions } from './BookConversationExtensions'
import { useWritingTechniqueSelection } from './hooks/useWritingTechniqueSelection'
import './index.scss'

interface AiPanelProps {
  modelConfigs: AiModelConfig[]
  onUpdateModelConfig?: (
    id: string,
    patch: Partial<Pick<AiModelConfig, 'contextWindow' | 'thinkingEnabled' | 'reasoningEffort'>>,
  ) => void
  onOpenModelSettings: () => void
  conversationSidebarOpen?: boolean
  onConversationSidebarOpenChange?: (open: boolean) => void
  onReady?: () => void
}

export default function AiPanel({
  modelConfigs = [],
  onUpdateModelConfig,
  onOpenModelSettings,
  conversationSidebarOpen = true,
  onConversationSidebarOpenChange,
  onReady,
}: AiPanelProps) {
  React.useEffect(() => {
    if (!onReady) return
    let secondFrame = 0
    let readyTimer = 0
    const firstFrame = window.requestAnimationFrame(() => {
      secondFrame = window.requestAnimationFrame(() => {
        readyTimer = window.setTimeout(onReady, 200)
      })
    })
    return () => {
      window.cancelAnimationFrame(firstFrame)
      if (secondFrame) window.cancelAnimationFrame(secondFrame)
      if (readyTimer) window.clearTimeout(readyTimer)
    }
  }, [onReady])

  const appMessage = usePurrToast()
  const chapterId = useActiveChapterId()
  const activeChapterTitle = useActiveChapterTitle()
  const bookId = useBookId()
  const bookTitle = useBookTitle()
  const writingChapters = useWritingChapters()
  const [prompt, setPromptState] = React.useState('')
  /** 人物目录：把 Agent 工具参数中的 characterIds 解析成人物名（工具行文案用） */
  const [bookCharacters, setBookCharacters] = React.useState<Array<{ id: number; name: string }>>([])
  const [characterCatalogRevision, bumpCharacterCatalog] = React.useReducer((value: number) => value + 1, 0)
  React.useEffect(() => {
    if (bookId == null) {
      setBookCharacters([])
      return
    }
    let cancelled = false
    services.characters.getCharacters({ bookId }).then((res) => {
      if (cancelled || !res.success || !Array.isArray(res.data)) return
      setBookCharacters(
        (res.data as Array<{ id: number; name: string }>).map((c) => ({ id: Number(c.id), name: String(c.name ?? '') })),
      )
    }).catch(() => undefined)
    return () => { cancelled = true }
  }, [bookId, characterCatalogRevision])
  // 设定数据变化（AI 写工具 / 设定 diff 提交）→ 刷新人物目录
  const settingsInvalidationSeq = useSettingsInvalidationStore((state) => state.seq)
  React.useEffect(() => {
    if (settingsInvalidationSeq === 0) return
    bumpCharacterCatalog()
  }, [settingsInvalidationSeq])
  const [conversations, setConversations] = React.useState<AgentConversationMessage[]>([])
  const [loading, setLoading] = React.useState(false)
  const [conversationInitializing, setConversationInitializing] = React.useState(false)
  const [conversationReloadRevision, requestConversationReload] = React.useReducer(
    (value: number) => value + 1,
    0,
  )
  // 对话作用域（章节/全局）：手动选择按书记忆，再次进入工作台时恢复；
  // 无章节时的自动全局不落盘，章节恢复即切回章节范围。
  const {
    chatScope,
    handleChatScopeChange,
    openGlobalChat,
  } = useChatScopeMemory(bookId, chapterId)
  const [favoritesModalOpen, setFavoritesModalOpen] = React.useState(false)
  const [memoryModalOpen, setMemoryModalOpen] = React.useState(false)
  const [contextPopoverOpen, setContextPopoverOpen] = React.useState(false)
  const pendingSettingSessionRef = React.useRef(false)
  const pendingSettingPromptRef = React.useRef<string>()
  const conversationLifecycleRef = React.useRef(
    createConversationSessionLifecycle<number>(),
  )
  const [conversationIdentity, setConversationIdentity] = React.useState('book-session:none:0')
  const effectiveChapterId = chatScope === 'setting' ? null : chapterId
  const scopeAvailable = bookId != null
    && (chatScope === 'setting' || chapterId != null)
  const {
    selectedModel,
    setSelectedModel,
    selectedModelConfig,
  } = useAiModelPrefs(bookId, modelConfigs)
  const {
    selectedLongTermMemoryIds,
    setSelectedLongTermMemoryIds,
    selectedMemoryIds,
    setSelectedMemoryIds,
    selectedForeshadowingIds,
    setSelectedForeshadowingIds,
  } = useMemorySelection(bookId)
  const {
    sessions,
    setSessions,
    sessionsLoaded,
    activeSessionId,
    setActiveSessionId,
    prependedHistory,
    handleNewSession,
    handleCloseTab,
    handleOpenFromHistory,
    handleDeleteFromHistory,
    currentSessionTitle,
    handleRenameSession,
    handleReorderSessions,
    handleToggleSessionPinned,
  } = useAiSessions({
    bookId,
    chapterId: effectiveChapterId,
    scope: chatScope,
    conversations,
    setConversations,
    setLoading,
  })
  const techniqueSelection = useWritingTechniqueSelection(bookId, activeSessionId)
  const activeSessionRef = React.useRef(activeSessionId)
  activeSessionRef.current = activeSessionId
  const attachmentManager = React.useMemo(
    () => createBookAssistantAttachmentManager(
      `book:${String(bookId ?? 'none')}`,
    ),
    [bookId],
  )
  const attachments = React.useSyncExternalStore(
    attachmentManager.subscribe,
    attachmentManager.getSnapshot,
    attachmentManager.getSnapshot,
  )
  const addAssistantAttachment = React.useCallback((
    message: AgentConversationMessage,
    card: SettingDiffCardState,
    owner: {
      sessionId: number
      bookId: string
      chapterId: string | null
      prompt: string
    },
  ) => {
    attachmentManager.add(message, card, {
      ...owner,
      message,
    })
  }, [attachmentManager])

  // 设定 diff 审阅完成（经 aiProposalBridge）：落附件并持久化提议解决状态
  const resolvedQueueLength = useAiProposalBridge((state) => state.resolvedQueue.length)
  React.useEffect(() => {
    if (resolvedQueueLength === 0) return
    for (const detail of useAiProposalBridge.getState().drainResolved()) {
      resolveSettingDiffBody(detail)
    }
  }, [resolvedQueueLength, appMessage, attachmentManager])

  const resolveSettingDiffBody = (detail: SettingDiffCardState | undefined) => {
    if (!detail?.proposalId) return
    attachmentManager.resolve(detail)
    const owner = attachmentManager.ownerForProposal(detail.proposalId)
    if (!owner) return
    const assistant = owner.message
    const runId = assistant?.agentRunId
    if (!runId) return
    void persistBookProposalResolution({
      wait: () => new Promise<void>((resolve) => window.setTimeout(resolve, 300)),
      save: () => services.conversations.saveConversation({
        sessionId: owner.sessionId,
        bookId: owner.bookId,
        chapterId: owner.chapterId,
        prompt: owner.prompt,
        response: '',
        agentRunId: runId,
        agentProcess: attachmentManager.productProjection(assistant),
      }),
    }).then((saved) => {
      if (!saved) appMessage.error('设定审阅状态保存失败，请稍后重试')
    })
  }
  const setPrompt = React.useCallback<React.Dispatch<React.SetStateAction<string>>>(
    (next) => {
      setPromptState((current) => {
        const value = typeof next === 'function' ? next(current) : next
        const sessionId = activeSessionRef.current
        if (sessionId != null) {
          conversationLifecycleRef.current.setDraft(sessionId, value)
        }
        return value
      })
    },
    [],
  )
  const activeLoadToken = conversationLifecycleRef.current.currentToken()
  const activeLoadMatches = activeSessionId == null
    || activeLoadToken?.sessionId === activeSessionId
  const activePrompt = activeSessionId != null && !activeLoadMatches
    ? conversationLifecycleRef.current.getDraft(activeSessionId)
    : prompt

  // 「与 AI 讨论设定」预填请求（大纲页入口经 chatPrefillStore 发起）
  const chatPrefillSeq = useChatPrefillStore((state) => state.seq)
  React.useEffect(() => {
    if (chatPrefillSeq === 0) return
    const { prefill } = useChatPrefillStore.getState()
    openGlobalChat()
    pendingSettingSessionRef.current = true
    if (prefill) {
      pendingSettingPromptRef.current = prefill
      setPromptState(prefill)
    }
  }, [chatPrefillSeq, openGlobalChat])

  // 正文选区「引用」在 quoteStore（编辑器写入，胶囊/发送共享）；切会话清空
  const pendingQuotes = useQuoteStore((state) => state.quotes)
  React.useEffect(() => {
    useQuoteStore.getState().removeAll()
  }, [activeSessionId])

  React.useEffect(() => {
    if (!pendingSettingSessionRef.current) return
    if (chatScope !== 'setting' || !sessionsLoaded) return
    pendingSettingSessionRef.current = false
    if (sessions.length === 0) void handleNewSession()
  }, [chatScope, handleNewSession, sessions.length, sessionsLoaded])

  const {
    associatedChapterIds,
    setAssociatedChapterIds,
    associatedOutlineIds,
    setAssociatedOutlineIds,
    availableOutlines,
    outlineSelectOptions,
    chapterSelectOptions,
    handleQuickAssociateChapter,
    handleQuickAssociateOutline,
  } = useAssociatedContext({ bookId, chapterId, writingChapters })

  const contextBar = React.useMemo<AiContextBarBindings>(() => ({
    associatedChapterIds,
    setAssociatedChapterIds,
    associatedOutlineIds,
    setAssociatedOutlineIds,
    chapterSelectOptions,
    outlineSelectOptions,
    onQuickAssociateChapter: handleQuickAssociateChapter,
    onQuickAssociateOutline: handleQuickAssociateOutline,
    selectedMemoryIds,
    selectedLongTermMemoryIds,
    selectedForeshadowingIds,
    onOpenMemoryModal: () => setMemoryModalOpen(true),
    contextPopoverOpen,
    onContextPopoverOpenChange: setContextPopoverOpen,
  }), [
    associatedChapterIds,
    associatedOutlineIds,
    chapterSelectOptions,
    contextPopoverOpen,
    handleQuickAssociateChapter,
    handleQuickAssociateOutline,
    outlineSelectOptions,
    selectedForeshadowingIds,
    selectedLongTermMemoryIds,
    selectedMemoryIds,
    setAssociatedChapterIds,
    setAssociatedOutlineIds,
  ])

  const promptTemplateContext = usePromptTemplateContext({
    chapterId,
    writingChapters,
    bookTitle,
    activeChapterTitle,
    associatedChapterIds,
    associatedOutlineIds,
    chapterSelectOptions,
    outlineSelectOptions,
  })
  const activeTaskPlan = React.useMemo(
    () => getActiveTaskPlan(conversations, loading),
    [conversations, loading],
  )

  const {
    handleSubmit: doSubmit,
    handleAbort,
    queuedSubmissions,
    updateQueuedSubmission,
    sessionActivities,
    stopping,
  } = useChatSubmit({
    selectedModelConfig,
    prompt: activePrompt,
    setPrompt,
    loading,
    setLoading,
    conversations,
    setConversations,
    bookId: bookId ?? undefined,
    chapterId: effectiveChapterId,
    activeSessionId,
    ensureSession: async () => {
      const createdId = await handleNewSession()
      if (createdId != null) onConversationSidebarOpenChange?.(true)
      return createdId
    },
    sessions,
    setSessions,
    associatedChapterIds,
    associatedOutlineIds,
    currentChapterTitle: chatScope === 'setting'
      ? undefined
      : activeChapterTitle || undefined,
    selectedModel,
    agentEnabled: true,
    selectedLongTermMemoryIds,
    selectedMemoryIds,
    selectedForeshadowingIds,
    writingTechniqueSelection: techniqueSelection.selection,
    writingTechniqueReady: techniqueSelection.ready,
    onWritingTechniqueAccepted: techniqueSelection.accepted,
    sessionScope: chatScope,
    onAssistantAttachment: addAssistantAttachment,
    associateAssistantIdentities: attachmentManager.associate,
    productAgentProcess: attachmentManager.productProjection,
    onPersistenceConflict: (sessionId) => {
      if (sessionId === activeSessionRef.current) {
        setConversationInitializing(true)
      }
      requestConversationReload()
    },
  })

  const clearSelectedContext = React.useCallback(() => {
    setSelectedLongTermMemoryIds([])
    setSelectedMemoryIds([])
    setSelectedForeshadowingIds([])
  }, [
    setSelectedForeshadowingIds,
    setSelectedLongTermMemoryIds,
    setSelectedMemoryIds,
  ])


  const handleSubmit = React.useCallback((content?: string) => {
    const token = conversationLifecycleRef.current.currentToken()
    // 零会话时放行：useChatSubmit 的 ensureSession 会先建会话再提交本轮
    // （composerPolicy 的 sessionlessSend 契约）；此时 lifecycle token 尚不存在，
    // 不能用它拦截，否则零会话发送会静默失效。
    if (
      activeSessionRef.current != null
      && (
        !token
        || token.sessionId !== activeSessionRef.current
        || !conversationLifecycleRef.current.canAct(token)
      )
    ) return
    // 引用状态拼在正文前（块引用约定，随消息持久化并在气泡里渲染成引用块）
    const quotePrefix = buildUserQuotesPrefill(pendingQuotes)
    if (content !== undefined) {
      const trimmed = content.trim()
      if (!trimmed) return
      doSubmit({ content: quotePrefix + trimmed })
    } else if (quotePrefix) {
      const trimmed = prompt.trim()
      if (!trimmed) return
      doSubmit({ content: quotePrefix + trimmed })
    } else {
      doSubmit()
    }
    if (quotePrefix) useQuoteStore.getState().removeAll()
    clearSelectedContext()
  }, [clearSelectedContext, doSubmit, pendingQuotes, prompt])

  const handleEditMessage = React.useCallback((index: number, content: string) => {
    const token = conversationLifecycleRef.current.currentToken()
    if (
      !token
      || token.sessionId !== activeSessionRef.current
      || !conversationLifecycleRef.current.canAct(token)
    ) return
    const editIndex = index - prependedHistory.length
    const trimmed = content.trim()
    if (editIndex < 0 || !trimmed) return
    doSubmit({ editIndex, content: trimmed })
    clearSelectedContext()
  }, [clearSelectedContext, doSubmit, prependedHistory.length])

  const handleResolveToolApproval = React.useCallback((
    approvalId: string,
    approved: boolean,
  ) => services.ai.resolveAiToolApproval({ approvalId, approved }), [])
  const handleSubmitErrorReport = React.useCallback(
    (reportId: string) => services.ai.submitAiErrorReport({ reportId }),
    [],
  )

  React.useEffect(() => {
    const lifecycle = conversationLifecycleRef.current
    if (activeSessionId == null) {
      lifecycle.invalidate()
      setConversationInitializing(false)
      setConversations([])
      setLoading(false)
      setConversationIdentity(
        `book-session:none:${String(bookId ?? 'none')}:${String(effectiveChapterId ?? 'none')}:${chatScope}`,
      )
      return
    }
    const sessionId = activeSessionId
    const token = lifecycle.beginLoad(sessionId)
    const recoveryController = new AbortController()
    setConversationIdentity(token.identity)
    const pendingSettingPrompt = chatScope === 'setting'
      ? pendingSettingPromptRef.current
      : undefined
    if (pendingSettingPrompt != null) {
      pendingSettingPromptRef.current = undefined
      lifecycle.setDraft(sessionId, pendingSettingPrompt)
    }
    setPromptState(lifecycle.getDraft(sessionId))
    const currentRuntime = getChatSessionRuntime(sessionId)
    const currentRuntimeAttached = Boolean(
      currentRuntime
      && (currentRuntime.loading || currentRuntime.stopping || currentRuntime.streamId),
    )
    const cachedMessages = currentRuntime?.messages ?? []
    // 切回已看过的会话：runtime store 留有上次渲染的完整消息，直接展示缓存
    // 并结束初始化等待，不再整段重放「恢复对话」；下方复核循环仍在后台重读
    // 服务端投影，如有差异由 commitSettled 刷新为权威内容。
    const restoreFromCache = !currentRuntimeAttached && cachedMessages.length > 0
    setConversations(
      currentRuntimeAttached || restoreFromCache ? cachedMessages : [],
    )
    setLoading(currentRuntimeAttached ? currentRuntime!.loading : false)
    setConversationInitializing(true)
    // 缓存快速路径的基线版本：复核期间用户若有新动作（发送/编辑），
    // commitSettled 放弃用旧快照覆盖 runtime。
    const cacheBaseRevision = restoreFromCache ? currentRuntime!.revision : null
    let initialLoadFinished = false

    const finishInitialLoad = () => {
      if (initialLoadFinished) return
      initialLoadFinished = true
      if (lifecycle.finishLoad(token)) setConversationInitializing(false)
    }
    if (restoreFromCache) finishInitialLoad()
    const projectOccurrences = (loaded: HydratedBookConversationReadModel) => {
      for (const occurrence of loaded.settingDiffOccurrences) {
        attachmentManager.add(occurrence.message, occurrence.card, {
          ...occurrence.owner,
          message: occurrence.message,
        })
        if (occurrence.card.status === 'pending') {
          proposeSettingDiff({
            ...occurrence.proposal,
            restoreOnly: true,
            resolutionTarget: occurrence.message.agentRunId
              ? {
                  sessionId: occurrence.owner.sessionId,
                  agentRunId: occurrence.message.agentRunId,
                  prompt: occurrence.owner.prompt,
                }
              : undefined,
          })
        } else {
          hydrateSettingDiffResolution({
            ...occurrence.card,
            ownerSessionId: occurrence.owner.sessionId,
          })
        }
      }
    }
    const hydrateRows = async (rows: Conversation[]) => {
      for (let attempt = 0; lifecycle.isCurrent(token); attempt++) {
        try {
          return await hydrateBookConversationReadModel(rows, {
            getRunSnapshot: (input) => services.ai.getAgentRunSnapshot(input),
            concurrency: 4,
            isCurrent: () => lifecycle.isCurrent(token),
          })
        } catch (error) {
          if (!(error instanceof BookConversationHydrationError) || attempt >= 8) throw error
          await waitForAgentRetry(Math.min(10_000, 250 * 2 ** attempt), recoveryController.signal)
        }
      }
      return undefined
    }
    const commitSettled = (
      loaded: HydratedBookConversationReadModel,
      recoveredOwner?: RecoveredRunProjectionOwner<number>,
    ) => {
      if (!lifecycle.isCurrent(token)) return
      if (
        cacheBaseRevision != null
        && getChatSessionRuntime(sessionId)?.revision !== cacheBaseRevision
      ) return
      if (
        recoveredOwner
        && !canCommitRecoveredRunProjection(
          recoveredOwner,
          getChatSessionRuntime(sessionId),
        )
      ) return
      projectOccurrences(loaded)
      replaceChatRuntimeMessages(sessionId, loaded.messages)
      setChatRuntimeLoading(sessionId, false)
      setChatRuntimeStopping(sessionId, false)
      setChatRuntimeStreamId(sessionId, undefined)
      setConversations(loaded.messages)
      setLoading(false)
    }
    const waitForAuthority = (delayMs = 500) => waitForAgentRetry(delayMs, recoveryController.signal)
    const hydrateLatest = (input: Parameters<typeof hydrateLatestBookRun>[0]) => (
      retryCurrentConversationRead({
        isCurrent: () => lifecycle.isCurrent(token),
        read: () => hydrateLatestBookRun(input, {
          getRunSnapshot: (request) => services.ai.getAgentRunSnapshot(request),
        }),
        isRetryable: (error) => error instanceof BookConversationHydrationError,
        wait: waitForAuthority,
      })
    )

    void (async () => {
      while (lifecycle.isCurrent(token)) {
        const stable = await readStableConversationProjection({
          isCurrent: () => lifecycle.isCurrent(token),
          getRevision: () => getChatSessionRuntime(sessionId)?.revision,
          read: () => Promise.all([
            services.conversations.getConversations({ sessionId }),
            services.ai.getLatestSessionAgentRun({ sessionId }),
          ]),
          isSuccessful: ([conversations, latest]) => (
            conversations.success && latest.success
          ),
          wait: waitForAuthority,
        })
        if (!stable || !lifecycle.isCurrent(token)) return
        const [conversationResult, latestResult] = stable.value

        let rows = conversationResult.data as Conversation[]
        const latest = latestResult.data
        const latestSnapshot = latest?.snapshot ?? null
        const latestRunId = latestSnapshot?.run.runId
        const rowHasLatest = Boolean(
          latestRunId && rows.some(
            (row) => String(row.agent_run_id || '') === latestRunId,
          ),
        )
        const history = await hydrateRows(rows)
        if (!history || !lifecycle.isCurrent(token)) return
        const attachedRuntime = getChatSessionRuntime(sessionId)
        const transportOwnsRun = Boolean(
          attachedRuntime
          && (attachedRuntime.loading || attachedRuntime.stopping)
          && attachedRuntime.streamId
          && !attachedRuntime.streamId.startsWith('recovered-run:'),
        )
        if (transportOwnsRun) {
          projectOccurrences(history)
          setConversations(attachedRuntime!.messages)
          setLoading(attachedRuntime!.loading)
          finishInitialLoad()
          return
        }

        if (latest?.request && !latestSnapshot) {
          // Only a fresh renderer without an attached transport may abandon an
          // accepted request whose secret-bearing POST body was lost. A live
          // A→B→A transport owns the same request and must never be canceled by
          // hydration.
          const canceled = await services.ai.cancelWritingChatRequest({
            requestId: latest.request.requestId,
          })
          if (!canceled.success) await waitForAuthority()
          continue
        }

        // Hydration itself may span a queued settlement. Never let an older
        // stable read commit after the session runtime revision has advanced.
        if (getChatSessionRuntime(sessionId)?.revision !== stable.revision) {
          continue
        }

        if (!latest || !latestSnapshot) {
          commitSettled(history)
          finishInitialLoad()
          return
        }

        if (latestSnapshot.run.status !== 'running') {
          if (rowHasLatest) {
            commitSettled(history)
            finishInitialLoad()
            return
          }
          const replayed = await hydrateLatest({
            sessionId,
            prompt: latest.prompt,
            snapshot: latestSnapshot,
          })
          if (!replayed || !lifecycle.isCurrent(token)) return
          const fallback: HydratedBookConversationReadModel = {
            messages: [...history.messages, ...replayed.messages],
            settingDiffOccurrences: [
              ...history.settingDiffOccurrences,
              ...replayed.settingDiffOccurrences,
            ],
          }
          for (
            let attempt = 0;
            attempt < 3 && lifecycle.isCurrent(token);
            attempt += 1
          ) {
            const refreshed = await services.conversations.getConversations({ sessionId })
            if (refreshed.success) {
              rows = refreshed.data as Conversation[]
              if (rows.some(
                (row) => String(row.agent_run_id || '') === latestRunId,
              )) {
                const materialized = await hydrateRows(rows)
                if (materialized) commitSettled(materialized)
                finishInitialLoad()
                return
              }
            }
            await new Promise<void>((resolve) => window.setTimeout(resolve, 100))
          }
          commitSettled(fallback)
          finishInitialLoad()
          return
        }

        const baseline = await loadCompleteAgentRunSnapshot(latestSnapshot.run.runId, {
          initialSnapshot: latestSnapshot,
          getRunSnapshot: request => services.ai.getAgentRunSnapshot(request),
          isCurrent: () => lifecycle.isCurrent(token),
        })
        if (!baseline || !lifecycle.isCurrent(token)) return
        let snapshot = baseline
        const promptForRun = latest.prompt
        let recoveredTerminalOwner: RecoveredRunProjectionOwner<number> | undefined
        let recoveredOperations: Record<string, CanonicalOperation> = {}
        const projectSnapshot = async () => {
          const running = await hydrateLatest({
            sessionId,
            prompt: promptForRun,
            snapshot,
          })
          if (!running || !lifecycle.isCurrent(token)) return
          recoveredOperations = running.messages.at(-1)?.canonicalOutput?.operations ?? {}
          const combined = mergeHydratedBookRun(
            history,
            running,
            snapshot.run.runId,
          )
          projectOccurrences(combined)
          replaceChatRuntimeMessages(sessionId, combined.messages)
          // Recovery owns this session until the authoritative terminal
          // Conversation projection commits. Releasing loading/stream here
          // lets a queued R2 start while the late R1 refetch can still replace
          // its state.
          const recoveryControl = recoveredRunProjectionControl(
            snapshot.run.runId,
          )
          setChatRuntimeLoading(sessionId, recoveryControl.loading)
          setChatRuntimeStopping(
            sessionId,
            snapshot.run.execution.cancellationRequested,
          )
          setChatRuntimeStreamId(sessionId, recoveryControl.streamId)
          setChatRuntimeActivity(sessionId, {
            state: snapshot.run.status === 'running'
              ? 'running'
              : snapshot.run.status === 'done'
                ? 'completed'
                : snapshot.run.status === 'canceled'
                  ? 'canceled'
                  : 'failed',
            queuedCount: 0,
          })
          setConversations(combined.messages)
          setLoading(true)
          finishInitialLoad()

        }
        await projectSnapshot()
        if (snapshot.run.status === 'running') {
          await services.ai.consumeAgentRunEvents({
            runId: snapshot.run.runId, sessionId, after: snapshot.nextCursor,
            signal: recoveryController.signal,
            onEvent: async page => {
              if (!lifecycle.isCurrent(token)) return
              snapshot = mergeAgentRunSnapshot(snapshot, page)
              if (page.hasMore) return
              await projectSnapshot()
              if (page.run.status !== 'running' || page.events.some(event =>
                event.chunk && shouldRefreshBookProposalProjection(event.chunk,
                  recoveredOperations[String((event.chunk.payload as Record<string, unknown> | undefined)?.operationId || '')]))) {
                const product = await services.ai.getAgentRunSnapshot({
                  runId: page.run.runId, after: page.nextCursor, limit: 1,
                })
                if (!lifecycle.isCurrent(token)) return
                if (product.success && product.data) snapshot.productEvents = product.data.productEvents
                await projectSnapshot()
              }
            },
          })
        }
        if (!lifecycle.isCurrent(token)) return
        const runtime = getChatSessionRuntime(sessionId)
        if (runtime) recoveredTerminalOwner = captureRecoveredRunProjectionOwner(runtime, snapshot.run.runId)

        // Canonical terminal materialization is server-owned. Refetch it; if
        // projection commit is a fraction behind, retain the replayed terminal
        // view and retry briefly instead of reviving stale pre-Run history.
        for (let attempt = 0; attempt < 3 && lifecycle.isCurrent(token); attempt += 1) {
          const refreshed = await services.conversations.getConversations({ sessionId })
          if (refreshed.success) {
            const refreshedRows = refreshed.data as Conversation[]
            if (refreshedRows.some(
              (row) => String(row.agent_run_id || '') === snapshot.run.runId,
            )) {
              const terminal = await hydrateRows(refreshedRows)
              if (terminal) commitSettled(terminal, recoveredTerminalOwner)
              return
            }
          }
          await new Promise<void>((resolve) => window.setTimeout(resolve, 100))
        }
        const terminal = await hydrateLatest({
          sessionId,
          prompt: promptForRun,
          snapshot,
        })
        if (!terminal) return
        commitSettled(
          mergeHydratedBookRun(history, terminal, snapshot.run.runId),
          recoveredTerminalOwner,
        )
        finishInitialLoad()
        return
      }
    })().catch(error => {
      if (lifecycle.isCurrent(token)) {
        finishInitialLoad()
        appMessage.error(error instanceof Error ? error.message : '恢复对话失败，请重新进入会话')
      }
    })
    return () => { recoveryController.abort(); lifecycle.invalidate() }
  }, [
    activeSessionId,
    attachmentManager,
    bookId,
    chatScope,
    conversationReloadRevision,
    effectiveChapterId,
    setConversations,
    setLoading,
  ])

  const handleAddFavorite = React.useCallback(async (
    sourcePrompt: string,
    content: string,
  ) => {
    if (activeSessionId == null) return
    const result = await services.favorites.saveAiFavorite({
      sessionId: activeSessionId,
      sessionTitle: currentSessionTitle,
      prompt: sourcePrompt,
      content,
    })
    if (result.success) appMessage.success('已收藏')
    else appMessage.error(result.error ?? '收藏失败')
  }, [activeSessionId, appMessage, currentSessionTitle])

  const closeSession = React.useCallback((id: string | number) => {
    const session = sessions.find((item) => item.id === id)
    if (session) handleCloseTab(session)
  }, [handleCloseTab, sessions])

  const deleteHistorySession = React.useCallback((session: AiSession) => {
    attachmentManager.evictSession(session.id)
    evictSettingDiffOwner(bookId, session.id)
    handleDeleteFromHistory(session)
  }, [attachmentManager, bookId, handleDeleteFromHistory])

  const bookConversationController = useBookConversationController({
    conversationIdentity,
    bookId,
    chapterId: effectiveChapterId,
    scope: chatScope,
    historyService: services.sessions,
    sessions,
    activeSessionId,
    messages: conversations,
    prependedHistory,
    activities: sessionActivities,
    queuedSubmissions,
    prompt: activePrompt,
    setPrompt,
    initializing: Boolean(
      scopeAvailable
      && (!sessionsLoaded || conversationInitializing || !activeLoadMatches),
    ),
    running: loading,
    stopping,
    attachmentsVersion: attachmentManager.getVersion(),
    scopeAvailable,
    composerDisabledHint: chatScope !== 'setting' && effectiveChapterId == null
      ? '请先选择一个章节'
      : undefined,
    modelConfigs,
    selectedModelId: selectedModel,
    setSelectedModelId: setSelectedModel,
    updateModel: onUpdateModelConfig,
    openModelSettings: onOpenModelSettings,
    taskPlan: activeTaskPlan,
    actions: {
      selectSession: (id) => setActiveSessionId(Number(id)),
      createSession: async () => {
        await handleNewSession()
      },
      closeSession,
      renameSession: (id, title) => handleRenameSession(Number(id), title),
      reorderSessions: (orderedIds) => handleReorderSessions(orderedIds.map(Number)),
      toggleSessionPinned: (id, pinned) => handleToggleSessionPinned(Number(id), pinned),
      send: handleSubmit,
      updateQueuedSubmission,
      abort: handleAbort,
      editMessage: handleEditMessage,
      resolveToolApproval: handleResolveToolApproval,
      onSubmitErrorReport: handleSubmitErrorReport,
    },
    onOpenFromHistory: handleOpenFromHistory,
    onDeleteFromHistory: deleteHistorySession,
  })
  const combinedMessages = bookConversationController.conversation.messages
  const bookConversationExtensions = useBookConversationExtensions({
    sessionId: activeSessionId,
    bookId,
    bookTitle,
    chapterId: effectiveChapterId,
    chapterTitle: chatScope === 'chapter'
      ? activeChapterTitle || undefined
      : undefined,
    scope: chatScope,
    setScope: handleChatScopeChange,
    contextBar,
    prompt: activePrompt,
    onInsertPrompt: setPrompt,
    promptTemplateContext,
    messages: combinedMessages,
    attachments,
    running: loading,
    onAddFavorite: handleAddFavorite,
    writingTechniqueChoices: techniqueSelection.choices,
    writingTechniqueSelection: techniqueSelection.selection,
    onToggleWritingTechnique: techniqueSelection.toggle,
    onSetWritingTechniqueMode: techniqueSelection.setMode,
  })

  const ellipsisMenuItems: PurrDropdownItem[] = [{
    key: 'favorites',
    label: '查看收藏列表',
    onClick: () => setFavoritesModalOpen(true),
  }]

  const panelExtensions = React.useMemo(() => {
    if (pendingQuotes.length === 0) return bookConversationExtensions
    return {
      ...bookConversationExtensions,
      renderComposerTop: () => (
        <ComposerQuoteChip
          quotes={pendingQuotes}
          onRemoveAt={(index) => useQuoteStore.getState().removeAt(index)}
          onRemoveAll={() => useQuoteStore.getState().removeAll()}
        />
      ),
    }
  }, [bookConversationExtensions, pendingQuotes])

  return (
    <div className="ai-panel panel-main">
      <AiPanelHeader menuItems={ellipsisMenuItems} />
      <div className="ai-panel-body">
        <AgentConversationPanel
          controller={bookConversationController}
          extensions={panelExtensions}
          subAgentReader={services.ai}
          emptyStateTitle={chatScope !== 'setting' && effectiveChapterId == null
            ? '选择章节后开始对话'
            : undefined}
          emptyStateDescription={chatScope !== 'setting' && effectiveChapterId == null
            ? '写作 Agent 依附章节工作：在左侧章节列表选择或新建一个章节后，即可直接输入发送'
            : undefined}
          indexOpen={conversationSidebarOpen}
          onIndexOpenChange={onConversationSidebarOpenChange}
          className="book-agent-conversation-panel"
          toolLabelContext={{
            writingChapters,
            availableOutlines,
            characters: bookCharacters,
          }}
        />
      </div>
      <FavoritesModal
        open={favoritesModalOpen}
        onCancel={() => setFavoritesModalOpen(false)}
      />
      <MemoryModal
        open={memoryModalOpen}
        onCancel={() => setMemoryModalOpen(false)}
        bookId={bookId ?? null}
        writingChapters={writingChapters}
        selectedIds={selectedMemoryIds}
        selectedLongTermMemoryIds={selectedLongTermMemoryIds}
        selectedForeshadowingIds={selectedForeshadowingIds}
        onSelectConfirm={(longTermMemoryIds, memoryIds, foreshadowingIds) => {
          setSelectedLongTermMemoryIds(longTermMemoryIds)
          setSelectedMemoryIds(memoryIds)
          setSelectedForeshadowingIds(foreshadowingIds)
        }}
      />
    </div>
  )
}
