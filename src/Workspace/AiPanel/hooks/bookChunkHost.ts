import { services } from '@/services'
import type { PurrToastApi } from '@/purr-components'
import type {
  AgentChunkHost,
  AgentRunOutcome,
  AgentTerminalSnapshot,
  AiStreamChunk,
} from '../../../agent-runtime/chunkHandlers/types'
import { normalizeApiProvider } from '../../../modelCatalog'
import { buildStreamOptions } from '../../../agent-runtime/streamOptions'
import type { AiModelConfig, AiSession, EntityId } from '../../../types'
import type { AgentConversationMessage } from '../../../agent-runtime/contracts'
import {
  countQueuedForSession,
  getSettledSessionActivity,
  type ChatSessionActivity,
  type QueuedChatSubmission,
} from './chatQueue'
import {
  handleChapterCreated,
  handleProposedChapterDiff,
  handleSettingUpdated,
} from './bookChunkSideEffects'
import { handleProposedSettingDiff } from './bookSettingDiff'
import type { BookSettingDiffAttachmentHandler } from './bookSettingDiff'
import type { BookSettingDiffAttachmentOwner } from './bookSettingDiff'

export interface BookChunkHostDependencies {
  sessionId: number
  bookId: EntityId | null | undefined
  chapterId: EntityId | null | undefined
  needsTitle: boolean
  modelConfig: AiModelConfig
  apiModelName: string
  expectedConversationIds: number[]
  readMessages(): AgentConversationMessage[]
  replaceMessages(messages: AgentConversationMessage[]): void
  scheduleCommit(
    updater: (messages: AgentConversationMessage[]) => AgentConversationMessage[],
  ): void
  flushCommits(): void
  setRunning(running: boolean): void
  isVisible(): boolean
  setSessions(updater: (sessions: AiSession[]) => AiSession[]): void
  appMessage: PurrToastApi
  unsubscribe(): void
  clearStream(): void
  getQueue(): QueuedChatSubmission[]
  replaceQueue(queue: QueuedChatSubmission[]): void
  setActivity(activity: ChatSessionActivity): void
  isSessionRunning(): boolean
  submitQueued(submission: QueuedChatSubmission): void
  onAssistantAttachment?: BookSettingDiffAttachmentHandler
  attachmentOwner?: BookSettingDiffAttachmentOwner
  associateAssistantIdentities?(
    source: AgentConversationMessage,
    target: AgentConversationMessage,
  ): void
  productAgentProcess?(
    message: AgentConversationMessage,
  ): Record<string, unknown> | undefined
  afterSettled?(outcome: AgentRunOutcome, snapshot: AgentTerminalSnapshot): void
  onPersistenceBlocked?(): void
  onPersistenceConflict?(): void
  onPersistenceStarted?(): void
  onPersistenceAborted?(): void
  shouldRetryPersistence?(): boolean
  persistConversation?: boolean
}

export function createBookChunkHost(
  dependencies: BookChunkHostDependencies,
): AgentChunkHost {
  const host: AgentChunkHost = {
    readMessages: dependencies.readMessages,
    replaceMessages: dependencies.replaceMessages,
    scheduleCommit: dependencies.scheduleCommit,
    flushCommits: dependencies.flushCommits,
    setRunning: dependencies.setRunning,
    isVisible: dependencies.isVisible,
    onHostChunk: (chunk) => dispatchBookChunk(
      chunk,
      host,
      dependencies.onAssistantAttachment,
      dependencies.attachmentOwner,
    ),
    onSettled: (outcome, snapshot) => {
      dependencies.unsubscribe()
      dependencies.clearStream()
      const finishSettlement = () => {
        dependencies.setRunning(false)
        settleQueue(outcome, dependencies)
        dependencies.afterSettled?.(outcome, snapshot)
      }
      if (dependencies.persistConversation === false) {
        finishSettlement()
        return
      }
      // The reducer has delivered the authoritative terminal, but editing or
      // draining a queued turn before this write settles can race a history
      // truncation against a late Conversation insert. Keep the product
      // session busy until the durable projection either commits or fails.
      dependencies.setRunning(true)
      dependencies.onPersistenceStarted?.()
      void persistTerminalSnapshotUntilConfirmed(snapshot, dependencies).then((result) => {
        if (result === 'retry-aborted') {
          dependencies.onPersistenceAborted?.()
          return
        }
        if (result === 'rejected') {
          dependencies.onPersistenceConflict?.()
          dependencies.replaceQueue(
            dependencies.getQueue().filter(
              (submission) => submission.sessionId !== dependencies.sessionId,
            ),
          )
          dependencies.setRunning(false)
          dependencies.setActivity(getSettledSessionActivity('failed', 0))
          dependencies.afterSettled?.('failed', snapshot)
          return
        }
        maybeGenerateSessionTitle(outcome, snapshot, dependencies)
        finishSettlement()
      })
    },
  }
  return host
}

async function persistTerminalSnapshotUntilConfirmed(
  snapshot: AgentTerminalSnapshot,
  dependencies: BookChunkHostDependencies,
): Promise<'persisted' | 'rejected' | 'retry-aborted'> {
  let attempt = 0
  while (dependencies.shouldRetryPersistence?.() !== false) {
    const result = await persistTerminalSnapshot(
      snapshot,
      dependencies,
      attempt === 0,
    )
    if (result === 'persisted' || result === 'rejected') return result
    if (attempt === 0) dependencies.onPersistenceBlocked?.()
    attempt += 1
    if (dependencies.shouldRetryPersistence?.() === false) return 'retry-aborted'
    const retryDelayMs = attempt === 1
      ? 0
      : Math.min(250 * (2 ** (attempt - 2)), 5_000)
    if (retryDelayMs === 0) {
      await Promise.resolve()
    } else {
      await new Promise((resolve) => globalThis.setTimeout(resolve, retryDelayMs))
    }
  }
  return 'retry-aborted'
}

function dispatchBookChunk(
  chunk: AiStreamChunk,
  host: AgentChunkHost,
  onAssistantAttachment?: BookSettingDiffAttachmentHandler,
  attachmentOwner?: BookSettingDiffAttachmentOwner,
): void {
  handleProposedChapterDiff(chunk, host)
  handleProposedSettingDiff(
    chunk,
    host,
    onAssistantAttachment,
    attachmentOwner,
  )
  handleChapterCreated(chunk, host)
  handleSettingUpdated(chunk, host)
}

function settleQueue(
  outcome: AgentRunOutcome,
  dependencies: BookChunkHostDependencies,
): void {
  const queuedCount = countQueuedForSession(
    dependencies.getQueue(),
    dependencies.sessionId,
  )
  dependencies.setActivity(getSettledSessionActivity(outcome, queuedCount))
  if (outcome === 'completed' && queuedCount === 0) {
    dependencies.appMessage.success('对话已完成')
  }
  if (queuedCount === 0) return

  queueMicrotask(() => {
    const queue = dependencies.getQueue()
    if (queue.some(item => item.sessionId === dependencies.sessionId && item.editing)) return
    const nextIndex = queue.findIndex(
      (submission) => submission.sessionId === dependencies.sessionId,
    )
    if (nextIndex < 0 || dependencies.isSessionRunning()) return
    const nextSubmission = queue[nextIndex]
    dependencies.replaceQueue(
      queue.filter((_submission, index) => index !== nextIndex),
    )
    dependencies.submitQueued(nextSubmission)
  })
}

async function persistTerminalSnapshot(
  snapshot: AgentTerminalSnapshot,
  dependencies: BookChunkHostDependencies,
  notifyFailure = true,
): Promise<'persisted' | 'rejected' | 'retry'> {
  const assistant = dependencies.readMessages().at(-1)
  const assistantIdentity = assistant
    ? {
        ...assistant,
        agentRunId: assistant.agentRunId || snapshot.agentRunId,
      }
    : undefined
  try {
    const result = await services.conversations.saveConversation({
    sessionId: dependencies.sessionId,
    bookId: dependencies.bookId ?? undefined,
    chapterId: dependencies.chapterId ?? null,
    prompt: snapshot.userText,
    response: snapshot.response,
    model: snapshot.model,
    commentaryBlocks: snapshot.commentaryBlocks.length
      ? snapshot.commentaryBlocks
      : undefined,
    commentaryDurationsMs: snapshot.commentaryDurationsMs.length
      ? snapshot.commentaryDurationsMs
      : undefined,
    durationMs: snapshot.durationMs,
    toolCallSegments: snapshot.toolCallSegments.length
      ? snapshot.toolCallSegments
      : undefined,
    taskPlan: snapshot.taskPlan,
    contextCompaction: snapshot.contextCompaction,
    contextBudget: snapshot.contextBudget,
    agentProcess: buildProductConversationProjection(
      assistant,
      assistantIdentity
        ? dependencies.productAgentProcess?.(assistantIdentity)
        : undefined,
    ),
    agentRunId: snapshot.agentRunId,
    clientTurnId: assistantIdentity?.clientTurnId,
    expectedConversationIds: dependencies.expectedConversationIds,
    })
    if (result?.success) {
      const conversationId = result.data?.id
      if (typeof conversationId === 'number') {
        if (assistantIdentity) {
          const persistedAssistant = {
            ...assistantIdentity,
            conversationId,
          }
          dependencies.associateAssistantIdentities?.(
            assistantIdentity,
            persistedAssistant,
          )
          dependencies.replaceMessages(
            attachConversationIdentity(
              dependencies.readMessages(),
              persistedAssistant,
              snapshot,
            ),
          )
        }
      }
      return 'persisted'
    }
    if (notifyFailure) warnPersistenceFailure(result, dependencies.appMessage)
    return typeof result?.httpStatus === 'number'
      && result.httpStatus >= 400
      && result.httpStatus < 500
      ? 'rejected'
      : 'retry'
  } catch (error: unknown) {
    if (notifyFailure) {
      warnPersistenceFailure(
        { error: error instanceof Error ? error.message : String(error) },
        dependencies.appMessage,
      )
    }
    return 'retry'
  }
}

function buildProductConversationProjection(
  assistant: AgentConversationMessage | undefined,
  attachmentProjection?: Record<string, unknown>,
): Record<string, unknown> | undefined {
  if (!assistant) return undefined
  const projection = {
    delegations: assistant.delegations,
    subAgentActivities: assistant.subAgentActivities,
    error: assistant.error,
    isError: assistant.isError,
    termination: assistant.termination,
    errorReport: assistant.errorReport,
    toolApprovals: assistant.toolApprovals,
  }
  const combined = { ...projection, ...attachmentProjection }
  return Object.values(combined).some((value) => value !== undefined)
    ? combined
    : undefined
}

function attachConversationIdentity(
  messages: AgentConversationMessage[],
  persisted: AgentConversationMessage,
  snapshot: AgentTerminalSnapshot,
): AgentConversationMessage[] {
  for (let index = messages.length - 1; index >= 1; index -= 1) {
    const assistant = messages[index]
    const user = messages[index - 1]
    if (
      assistant.role === 'assistant'
      && !assistant.conversationId
      && (
        (persisted.clientTurnId
          && assistant.clientTurnId === persisted.clientTurnId)
        || (persisted.agentRunId
          && assistant.agentRunId === persisted.agentRunId)
        || (
          assistant.content === snapshot.response
          && user?.role === 'user'
          && user.content === snapshot.userText
        )
      )
    ) {
      const next = [...messages]
      next[index] = {
        ...assistant,
        conversationId: persisted.conversationId,
        agentRunId: assistant.agentRunId || persisted.agentRunId,
      }
      return next
    }
  }
  return messages
}

function warnPersistenceFailure(
  result: { error?: unknown; detail?: unknown } | null | undefined,
  appMessage: PurrToastApi,
): void {
  const detail = result?.detail
  const message = typeof result?.error === 'string'
    ? result.error
    : Array.isArray(detail)
      ? detail
          .map((entry: { msg?: string }) => String(entry?.msg ?? entry ?? '').trim())
          .filter(Boolean)
          .join('；')
      : ''
  appMessage.warning(
    message
      ? `本轮对话未能写入本地库：${message}`
      : '本轮对话未能写入本地库（保存接口异常）。',
  )
}

function maybeGenerateSessionTitle(
  outcome: AgentRunOutcome,
  snapshot: AgentTerminalSnapshot,
  dependencies: BookChunkHostDependencies,
): void {
  const titleSource = snapshot.response.trim()
  if (
    outcome === 'failed'
    || !dependencies.needsTitle
    || !titleSource
  ) return

  void services.ai.generateSessionTitle({
    apiKey: dependencies.modelConfig.apiKey,
    baseURL: dependencies.modelConfig.baseUrl || undefined,
    prompt: `User:\n${snapshot.userText}\n\nAssistant:\n${titleSource}`.trim(),
    apiProvider: normalizeApiProvider(dependencies.modelConfig.apiProvider),
    model: dependencies.apiModelName,
    options: buildStreamOptions({
      cfg: dependencies.modelConfig,
      selectedModel: dependencies.apiModelName,
    }).options,
  }).then(async (result) => {
    if (!result.success || !result.data?.trim()) return
    const title = result.data.trim()
    await services.sessions.updateSessionTitle({
      sessionId: dependencies.sessionId,
      title,
    })
    dependencies.setSessions((sessions) => sessions.map((session) => (
      session.id === dependencies.sessionId ? { ...session, title } : session
    )))
  }).catch((error: unknown) => {
    console.warn('[AI 对话] 标题生成请求异常：', error)
  })
}
