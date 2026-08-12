import React from 'react'
import type {
  AgentConversationActivity,
  AgentConversationMessage,
  AgentQueuedSubmission,
  AiTaskPlan,
} from '../../agent-runtime/contracts.ts'
import { getAgentConversationCapabilities } from '../../agent-runtime/conversationCapabilities.ts'
import type { AgentConversationController } from '../../components/AgentConversation/controller.ts'
import { toAgentConversationSession } from '../../components/AgentConversation/sessionView.ts'
import type {
  AiModelConfig,
  AiSession,
  ApiResult,
  EntityId,
} from '../../types.ts'
import type { ChatSessionScope } from './hooks/useAiSessions.ts'

export interface HistoryRequestCoordinator {
  activate(): void
  deactivate(): void
  beginLatest(): number
  invalidateLatest(): void
  isCurrent(request: number): boolean
  beginMutation(): number
  completeMutation(scopeRequest: number): boolean
  isCurrentScope(scopeRequest: number): boolean
  isMounted(): boolean
  runOnce<T>(key: string, operation: () => Promise<T>): Promise<T>
}

export function createHistoryRequestCoordinator(): HistoryRequestCoordinator {
  let loadGeneration = 0
  let scopeGeneration = 0
  let active = false
  const inFlight = new Map<string, Promise<unknown>>()
  return {
    activate() {
      active = true
      scopeGeneration += 1
      loadGeneration += 1
    },
    deactivate() {
      active = false
      scopeGeneration += 1
      loadGeneration += 1
      inFlight.clear()
    },
    beginLatest() {
      loadGeneration += 1
      return loadGeneration
    },
    invalidateLatest() {
      scopeGeneration += 1
      loadGeneration += 1
    },
    isCurrent(request) {
      return active && request === loadGeneration
    },
    beginMutation() {
      loadGeneration += 1
      return scopeGeneration
    },
    completeMutation(scopeRequest) {
      if (!active || scopeRequest !== scopeGeneration) return false
      loadGeneration += 1
      return true
    },
    isCurrentScope(scopeRequest) {
      return active && scopeRequest === scopeGeneration
    },
    isMounted() {
      return active
    },
    runOnce<T>(key: string, operation: () => Promise<T>): Promise<T> {
      const scopedKey = `${scopeGeneration}:${key}`
      const existing = inFlight.get(scopedKey) as Promise<T> | undefined
      if (existing) return existing
      let pending: Promise<T>
      try {
        pending = operation()
      } catch (error) {
        pending = Promise.reject(error)
      }
      const tracked = pending.finally(() => {
        if (inFlight.get(scopedKey) === tracked) inFlight.delete(scopedKey)
      })
      inFlight.set(scopedKey, tracked)
      return tracked
    },
  }
}

export interface BookConversationBindings {
  sessions: AiSession[]
  historySessions: AiSession[]
  historyLoading: boolean
  historyError?: string
  activeSessionId: number | null
  messages: AgentConversationMessage[]
  prependedHistory: AgentConversationMessage[]
  activities: Record<number, AgentConversationActivity>
  queuedMessages: string[]
  prompt: string
  setPrompt(value: string): void
  initializing: boolean
  running: boolean
  stopping?: boolean
  attachmentsVersion?: string | number
  scopeAvailable?: boolean
  modelConfigs: AiModelConfig[]
  selectedModelId: string
  setSelectedModelId(id: string): void
  updateModel?: AgentConversationController['composer']['updateModel']
  openModelSettings(): void
  taskPlan?: AiTaskPlan
  actions: Pick<
    AgentConversationController['actions'],
    | 'selectSession'
    | 'createSession'
    | 'closeSession'
    | 'renameSession'
    | 'loadSessionHistory'
    | 'openHistorySession'
    | 'deleteSession'
    | 'send'
    | 'abort'
    | 'editMessage'
    | 'resolveToolApproval'
    | 'onSubmitErrorReport'
  >
}

export interface UseBookConversationControllerParams extends Omit<
  BookConversationBindings,
  | 'historySessions'
  | 'historyLoading'
  | 'historyError'
  | 'actions'
> {
  bookId: EntityId | null | undefined
  chapterId: EntityId | null | undefined
  scope: ChatSessionScope
  historyService: {
    getSessions(data: {
      bookId: EntityId
      chapterId?: EntityId | null
      includeClosed?: boolean
      scope?: 'setting'
    }): Promise<ApiResult<AiSession[]>>
    deleteSession(data: { sessionId: number }): Promise<ApiResult<void>>
  }
  actions: Omit<
    BookConversationBindings['actions'],
    'loadSessionHistory' | 'openHistorySession' | 'deleteSession'
  >
  onOpenFromHistory(session: AiSession): void
  onDeleteFromHistory(session: AiSession): void
}

export function toBookQueuedSubmissions(
  sessionId: number | null,
  queuedMessages: string[],
): AgentQueuedSubmission[] {
  if (sessionId == null) return []
  return queuedMessages.map((content, index) => ({
    id: `book-queue-${sessionId}-${index}`,
    sessionId,
    content,
  }))
}

export function createBookConversationController(
  bindings: BookConversationBindings,
): AgentConversationController {
  const selectedModel = bindings.modelConfigs.find(
    (model) => model.id === bindings.selectedModelId,
  ) ?? null
  return {
    capabilities: getAgentConversationCapabilities({
      running: bindings.running,
      readOnly: false,
      sessionLoading: bindings.initializing,
    }),
    conversation: {
      sessions: bindings.sessions.map((session) => (
        toAgentConversationSession(session, session.create_time)
      )),
      activeSessionId: bindings.activeSessionId,
      messages: [...bindings.prependedHistory, ...bindings.messages],
      activities: bindings.activities,
      queuedSubmissions: toBookQueuedSubmissions(
        bindings.activeSessionId,
        bindings.queuedMessages,
      ),
      initializing: bindings.initializing,
      running: bindings.running,
      stopping: bindings.stopping ?? false,
      paused: false,
      resuming: false,
      attachmentsVersion: bindings.attachmentsVersion,
      history: {
        sessions: bindings.historySessions.map((session) => (
          toAgentConversationSession(session, session.create_time)
        )),
        loading: bindings.historyLoading,
        error: bindings.historyError,
      },
    },
    composer: {
      value: bindings.prompt,
      setValue: bindings.setPrompt,
      placeholder: '想写点什么',
      ariaLabel: '输入希望写作 Agent 完成的任务',
      submitDisabled: Boolean(
        !bindings.prompt.trim()
        || !selectedModel
        || bindings.activeSessionId == null
        || bindings.scopeAvailable === false
      ),
      selectedModel,
      modelConfigs: bindings.modelConfigs,
      selectModel: bindings.setSelectedModelId,
      updateModel: bindings.updateModel,
      openModelSettings: bindings.openModelSettings,
      taskPlan: bindings.taskPlan,
    },
    actions: bindings.actions,
  }
}

export function useBookConversationController({
  bookId,
  chapterId,
  scope,
  historyService,
  actions,
  onOpenFromHistory,
  onDeleteFromHistory,
  ...bindings
}: UseBookConversationControllerParams): AgentConversationController {
  const [historySessions, setHistorySessions] = React.useState<AiSession[]>([])
  const [historyLoading, setHistoryLoading] = React.useState(false)
  const [historyError, setHistoryError] = React.useState<string>()
  const [historyRequests] = React.useState(createHistoryRequestCoordinator)

  React.useEffect(() => {
    historyRequests.activate()
    return () => historyRequests.deactivate()
  }, [historyRequests])

  React.useEffect(() => {
    historyRequests.invalidateLatest()
    setHistorySessions([])
    setHistoryLoading(false)
    setHistoryError(undefined)
  }, [bookId, chapterId, historyRequests, scope])

  const loadSessionHistory = React.useCallback(async () => {
    const request = historyRequests.beginLatest()
    if (bookId == null) {
      if (historyRequests.isCurrent(request)) {
        setHistorySessions([])
        setHistoryError('请先选择书籍')
      }
      return
    }
    if (historyRequests.isCurrent(request)) {
      setHistoryLoading(true)
      setHistoryError(undefined)
    }
    try {
      const result = await historyService.getSessions(
        scope === 'setting'
          ? { bookId, includeClosed: true, scope: 'setting' }
          : { bookId, chapterId: chapterId ?? null, includeClosed: true },
      )
      if (!historyRequests.isCurrent(request)) return
      if (!result.success) {
        setHistorySessions([])
        setHistoryError(result.error || '历史对话加载失败')
        return
      }
      setHistorySessions(result.data)
    } catch {
      if (historyRequests.isCurrent(request)) {
        setHistorySessions([])
        setHistoryError('历史对话加载失败，请稍后重试')
      }
    } finally {
      if (historyRequests.isCurrent(request)) setHistoryLoading(false)
    }
  }, [bookId, chapterId, historyRequests, historyService, scope])

  const openHistorySession = React.useCallback((id: string | number) => {
    const session = historySessions.find((item) => item.id === id)
    if (session) onOpenFromHistory(session)
  }, [historySessions, onOpenFromHistory])

  const deleteHistorySession = React.useCallback(async (id: string | number) => {
    const session = historySessions.find((item) => item.id === id)
    if (!session) return
    await historyRequests.runOnce(`session:${session.id}`, async () => {
      const scopeRequest = historyRequests.beginMutation()
      if (historyRequests.isCurrentScope(scopeRequest)) setHistoryLoading(false)
      try {
        const result = await historyService.deleteSession({ sessionId: session.id })
        if (!result.success) {
          if (historyRequests.isCurrentScope(scopeRequest)) {
            setHistoryError(result.error || '删除历史对话失败')
          }
          return
        }
        if (!historyRequests.completeMutation(scopeRequest)) return
        setHistoryLoading(false)
        setHistorySessions((current) => (
          current.filter((item) => item.id !== session.id)
        ))
        onDeleteFromHistory(session)
      } catch {
        if (historyRequests.isCurrentScope(scopeRequest)) {
          setHistoryError('删除历史对话失败，请稍后重试')
        }
      }
    })
  }, [historyRequests, historyService, historySessions, onDeleteFromHistory])

  return createBookConversationController({
    ...bindings,
    historySessions,
    historyLoading,
    historyError,
    actions: {
      ...actions,
      loadSessionHistory,
      openHistorySession,
      deleteSession: deleteHistorySession,
    },
  })
}
