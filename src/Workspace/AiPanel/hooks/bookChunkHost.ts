import { services } from '@/services'
import type { PurrToastApi } from '@/purr-components'
import type {
  AgentChunkHost,
  AgentRunOutcome,
  AgentTerminalSnapshot,
  AiStreamChunk,
} from '../../../agent-runtime/chunkHandlers/types'
import { normalizeApiProvider } from '../../../modelCatalog'
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
import { associateBookAssistantAttachmentIdentities } from '../bookAssistantAttachments'

export interface BookChunkHostDependencies {
  sessionId: number
  bookId: EntityId | null | undefined
  chapterId: EntityId | null | undefined
  needsTitle: boolean
  modelConfig: AiModelConfig
  apiModelName: string
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
    ),
    onSettled: (outcome, snapshot) => {
      dependencies.unsubscribe()
      dependencies.clearStream()
      settleQueue(outcome, dependencies)
      if (dependencies.persistConversation !== false) {
        persistTerminalSnapshot(snapshot, dependencies)
        maybeGenerateSessionTitle(outcome, snapshot, dependencies)
      }
    },
  }
  return host
}

function dispatchBookChunk(
  chunk: AiStreamChunk,
  host: AgentChunkHost,
  onAssistantAttachment?: BookSettingDiffAttachmentHandler,
): void {
  handleProposedChapterDiff(chunk, host)
  handleProposedSettingDiff(chunk, host, onAssistantAttachment)
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

function persistTerminalSnapshot(
  snapshot: AgentTerminalSnapshot,
  dependencies: BookChunkHostDependencies,
): void {
  const assistant = dependencies.readMessages().at(-1)
  const assistantIdentity = assistant
    ? {
        ...assistant,
        agentRunId: assistant.agentRunId || snapshot.agentRunId,
      }
    : undefined
  void services.conversations.saveConversation({
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
    agentProcess: assistant?.delegations?.length
      || assistant?.subAgentActivities?.length
      ? {
          delegations: assistant.delegations,
          subAgentActivities: assistant.subAgentActivities,
        }
      : undefined,
    agentRunId: snapshot.agentRunId,
  }).then((result) => {
    if (result?.success) {
      const conversationId = result.data?.id
      if (typeof conversationId === 'number') {
        if (assistantIdentity) {
          associateBookAssistantAttachmentIdentities(assistantIdentity, {
            ...assistantIdentity,
            conversationId,
          })
        }
        if (dependencies.isVisible()) {
          attachConversationId(snapshot, conversationId, dependencies)
        }
      }
      return
    }
    warnPersistenceFailure(result, dependencies.appMessage)
  }).catch((error: unknown) => {
    warnPersistenceFailure(
      { error: error instanceof Error ? error.message : String(error) },
      dependencies.appMessage,
    )
  })
}

function attachConversationId(
  snapshot: AgentTerminalSnapshot,
  conversationId: number,
  dependencies: BookChunkHostDependencies,
): void {
  dependencies.scheduleCommit((messages) => {
    const index = findConversationMessageIndex(
      messages,
      snapshot.userText,
      snapshot.response,
    )
    if (index < 0) return messages
    const next = [...messages]
    next[index] = { ...next[index], conversationId }
    return next
  })
}

function findConversationMessageIndex(
  messages: AgentConversationMessage[],
  userText: string,
  assistantContent: string,
): number {
  for (let index = messages.length - 1; index >= 1; index -= 1) {
    const assistant = messages[index]
    const user = messages[index - 1]
    if (
      assistant.role === 'assistant'
      && !assistant.conversationId
      && assistant.content === assistantContent
      && user?.role === 'user'
      && user.content === userText
    ) return index
  }
  return -1
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
