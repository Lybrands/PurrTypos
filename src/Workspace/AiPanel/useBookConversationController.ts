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
  beginSessionDelete(id: number, allowed: boolean): object | null
  completeSessionDelete(token: object): boolean
  failSessionDelete(token: object): boolean
  isSessionDeleting(id: number): boolean
  canOpenSession(id: number): boolean
}

export function createHistoryRequestCoordinator(): HistoryRequestCoordinator {
  let loadGeneration = 0
  let scopeGeneration = 0
  let active = false
  const inFlight = new Map<string, Promise<unknown>>()
  const deleting = new Map<number, object>()
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
      deleting.clear()
    },
    beginLatest() {
      loadGeneration += 1
      return loadGeneration
    },
    invalidateLatest() {
      scopeGeneration += 1
      loadGeneration += 1
      deleting.clear()
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
    beginSessionDelete(id, allowed) {
      if (!active || !allowed || deleting.has(id)) return null
      loadGeneration += 1
      const token = { id, scopeGeneration, loadGeneration }
      deleting.set(id, token)
      return token
    },
    completeSessionDelete(token) {
      const id = Number((token as { id?: unknown }).id)
      if (deleting.get(id) !== token) return false
      deleting.delete(id)
      if (!active || (token as { scopeGeneration?: unknown }).scopeGeneration !== scopeGeneration) {
        return false
      }
      loadGeneration += 1
      return true
    },
    failSessionDelete(token) {
      const id = Number((token as { id?: unknown }).id)
      if (deleting.get(id) !== token) return false
      deleting.delete(id)
      return active
        && (token as { scopeGeneration?: unknown }).scopeGeneration === scopeGeneration
        && (token as { loadGeneration?: unknown }).loadGeneration === loadGeneration
    },
    isSessionDeleting(id) {
      return deleting.has(id)
    },
    canOpenSession(id) {
      return !deleting.has(id)
    },
  }
}

export interface BookConversationBindings {
  conversationIdentity?: string
  sessions: AiSession[]
  historySessions: AiSession[]
  historyLoading: boolean
  historyError?: string
  deletingSessionIds?: number[]
  deleteDisabledSessionIds?: number[]
  activeSessionId: number | null
  messages: AgentConversationMessage[]
  prependedHistory: AgentConversationMessage[]
  activities: Record<number, AgentConversationActivity>
  queuedSubmissions: AgentQueuedSubmission[]
  prompt: string
  setPrompt(value: string): void
  initializing: boolean
  running: boolean
  stopping?: boolean
  canSelectSession?(id: string | number): boolean
  attachmentsVersion?: string | number
  scopeAvailable?: boolean
  /** 写作范围未选章节等导致禁发时给用户的提示 */
  composerDisabledHint?: string
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
    | 'updateQueuedSubmission'
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
  queuedSubmissions: AgentQueuedSubmission[],
): AgentQueuedSubmission[] {
  if (sessionId == null) return []
  return queuedSubmissions.filter(item => item.sessionId === sessionId)
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
      // 零会话时可直接发送：useChatSubmit 的 ensureSession 会自动创建会话
      // （能力默认已放开，此处显式声明以表明发送路径支持该约定）。
      sessionlessSend: true,
    }),
    conversation: {
      identity: bindings.conversationIdentity
        ?? `book-session:${String(bindings.activeSessionId ?? 'none')}`,
      sessions: bindings.sessions.map((session) => (
        toAgentConversationSession(session, session.create_time)
      )),
      activeSessionId: bindings.activeSessionId,
      messages: [...bindings.prependedHistory, ...bindings.messages],
      activities: bindings.activities,
      queuedSubmissions: toBookQueuedSubmissions(
        bindings.activeSessionId,
        bindings.queuedSubmissions,
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
        deletingSessionIds: bindings.deletingSessionIds,
        deleteDisabledSessionIds: bindings.deleteDisabledSessionIds,
      },
    },
    composer: {
      value: bindings.prompt,
      setValue: bindings.setPrompt,
      placeholder: '想写点什么',
      ariaLabel: '输入希望写作 Agent 完成的任务',
      // 零会话不再阻断输入/发送：发送时会自动创建会话（useChatSubmit ensureSession）。
      // 会话列表加载期间（initializing）保持禁发，避免在不知道是否已有会话时重复建会话。
      ready: bindings.scopeAvailable !== false && !bindings.initializing,
      disabledHint: bindings.composerDisabledHint,
      submitDisabled: Boolean(
        !bindings.prompt.trim()
        || !selectedModel
        || bindings.scopeAvailable === false
        || bindings.initializing
      ),
      selectedModel,
      modelConfigs: bindings.modelConfigs,
      selectModel: bindings.setSelectedModelId,
      updateModel: bindings.updateModel,
      openModelSettings: bindings.openModelSettings,
      taskPlan: bindings.taskPlan,
    },
    actions: {
      ...bindings.actions,
      selectSession: (id) => {
        if (bindings.canSelectSession?.(id) === false) return
        return bindings.actions.selectSession(id)
      },
      send: (content) => {
        if (!bindings.initializing) return bindings.actions.send(content)
      },
      editMessage: (index, content) => {
        if (!bindings.initializing) return bindings.actions.editMessage(index, content)
      },
    },
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
  const [deletingSessions, setDeletingSessions] = React.useState(
    () => new Map<number, object>(),
  )

  React.useEffect(() => {
    historyRequests.activate()
    return () => historyRequests.deactivate()
  }, [historyRequests])

  React.useEffect(() => {
    historyRequests.invalidateLatest()
    setHistorySessions([])
    setHistoryLoading(false)
    setHistoryError(undefined)
    setDeletingSessions(new Map())
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
    if (!historyRequests.canOpenSession(Number(id))) return
    const session = historySessions.find((item) => item.id === id)
    if (session) onOpenFromHistory(session)
  }, [historyRequests, historySessions, onOpenFromHistory])

  const deleteHistorySession = React.useCallback(async (id: string | number) => {
    const session = historySessions.find((item) => item.id === id)
    if (!session) return
    const activity = bindings.activities[session.id]
    const protectedActivity = Boolean(
      activity
      && (
        activity.queuedCount > 0
        || activity.state === 'running'
        || activity.state === 'queued'
        || activity.state === 'paused'
      ),
    ) || (bindings.activeSessionId === session.id && bindings.running)
    const token = historyRequests.beginSessionDelete(session.id, !protectedActivity)
    if (!token) {
      if (protectedActivity) {
        setHistoryError('运行中、排队中或已暂停的对话不能删除。')
      }
      return
    }
    setDeletingSessions((current) => new Map(current).set(session.id, token))
    setHistoryLoading(false)
    setHistoryError(undefined)
    try {
      const result = await historyService.deleteSession({ sessionId: session.id })
      if (!result.success) {
        if (historyRequests.failSessionDelete(token)) {
          setHistoryError(result.error || '删除历史对话失败')
        }
        return
      }
      if (!historyRequests.completeSessionDelete(token)) return
      setHistoryLoading(false)
      setHistorySessions((current) => (
        current.filter((item) => item.id !== session.id)
      ))
      onDeleteFromHistory(session)
    } catch {
      if (historyRequests.failSessionDelete(token)) {
        setHistoryError('删除历史对话失败，请稍后重试')
      }
    } finally {
      setDeletingSessions((current) => {
        if (current.get(session.id) !== token) return current
        const next = new Map(current)
        next.delete(session.id)
        return next
      })
    }
  }, [
    bindings.activeSessionId,
    bindings.activities,
    bindings.running,
    historyRequests,
    historyService,
    historySessions,
    onDeleteFromHistory,
  ])

  const deleteDisabledSessionIds = historySessions
    .filter((session) => {
      const activity = bindings.activities[session.id]
      return Boolean(
        (activity && (
          activity.queuedCount > 0
          || activity.state === 'running'
          || activity.state === 'queued'
          || activity.state === 'paused'
        ))
        || (bindings.activeSessionId === session.id && bindings.running)
      )
    })
    .map((session) => session.id)

  return createBookConversationController({
    ...bindings,
    historySessions,
    historyLoading,
    historyError,
    deletingSessionIds: [...deletingSessions.keys()],
    deleteDisabledSessionIds,
    canSelectSession: (id) => historyRequests.canOpenSession(Number(id)),
    actions: {
      ...actions,
      loadSessionHistory,
      openHistorySession,
      deleteSession: deleteHistorySession,
    },
  })
}
