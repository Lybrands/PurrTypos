import {
  clearAgentConversationRuntime,
  getAgentConversationActivities,
  getAgentConversationRuntime,
  getAgentConversationRuntimeVersion,
  replaceAgentConversationMessages,
  setAgentConversationActivity,
  setAgentConversationRunning,
  setAgentConversationStopping,
  setAgentConversationStreamId,
  subscribeAgentConversationRuntime,
  updateAgentConversationMessages,
} from '../../../agent-runtime/runtimeStore'
import type { AgentConversationMessage } from '../../../agent-runtime/contracts'
import { changeQueuedSubmission, type QueuedSubmissionEdit } from '../../../agent-runtime/queuedSubmission'
import type {
  ChatSessionActivity,
  QueuedChatSubmission,
} from './chatQueue'

export interface ChatSessionRuntime {
  sessionId: number
  messages: AgentConversationMessage[]
  loading: boolean
  stopping: boolean
  activity?: ChatSessionActivity
  streamId?: string
  updatedAt: number
  revision: number
}

let queuedSubmissions: QueuedChatSubmission[] = []
const queueListeners = new Set<() => void>()
let queueVersion = 0

function emitQueueChange(): void {
  queueVersion += 1
  queueListeners.forEach((listener) => listener())
}

export function subscribeChatRuntime(listener: () => void): () => void {
  const unsubscribeRuntime = subscribeAgentConversationRuntime(listener)
  queueListeners.add(listener)
  return () => {
    unsubscribeRuntime()
    queueListeners.delete(listener)
  }
}

export function getChatRuntimeVersion(): number {
  return getAgentConversationRuntimeVersion() + queueVersion
}

export function getChatSessionRuntime(
  sessionId: number | null | undefined,
): ChatSessionRuntime | undefined {
  const runtime = getAgentConversationRuntime(sessionId)
  if (!runtime) return undefined
  return {
    sessionId: runtime.sessionId as number,
    messages: runtime.messages,
    loading: runtime.running,
    stopping: runtime.stopping,
    activity: runtime.activity as ChatSessionActivity | undefined,
    streamId: runtime.streamId,
    updatedAt: runtime.updatedAt,
    revision: runtime.revision,
  }
}

export function replaceChatRuntimeMessages(
  sessionId: number,
  messages: AgentConversationMessage[],
): void {
  replaceAgentConversationMessages(sessionId, messages)
}

export function updateChatRuntimeMessages(
  sessionId: number,
  updater: (messages: AgentConversationMessage[]) => AgentConversationMessage[],
): void {
  updateAgentConversationMessages(
    sessionId,
    updater,
  )
}

export function setChatRuntimeLoading(
  sessionId: number,
  next: boolean | ((current: boolean) => boolean),
): void {
  setAgentConversationRunning(sessionId, next)
}

export function setChatRuntimeStopping(
  sessionId: number,
  stopping: boolean,
): void {
  setAgentConversationStopping(sessionId, stopping)
}

export function setChatRuntimeActivity(
  sessionId: number,
  activity: ChatSessionActivity,
): void {
  setAgentConversationActivity(sessionId, activity)
}

export function setChatRuntimeStreamId(
  sessionId: number,
  streamId: string | undefined,
): void {
  setAgentConversationStreamId(sessionId, streamId)
}

export function getChatSessionActivities(): Record<number, ChatSessionActivity> {
  return getAgentConversationActivities() as Record<number, ChatSessionActivity>
}

export function getChatRuntimeQueue(): QueuedChatSubmission[] {
  return queuedSubmissions
}

export function replaceChatRuntimeQueue(queue: QueuedChatSubmission[]): void {
  queuedSubmissions = queue
  emitQueueChange()
}

export function updateChatQueuedSubmission(
  sessionId: number, bookId: string | number, id: string, patch: QueuedSubmissionEdit | null,
): boolean {
  const next = changeQueuedSubmission(queuedSubmissions, id, patch,
    item => item.sessionId === sessionId && item.bookId === bookId)
  if (next === queuedSubmissions) return false
  replaceChatRuntimeQueue(next)
  const runtime = getChatSessionRuntime(sessionId)
  const queuedCount = next.filter(item => item.sessionId === sessionId).length
  if (runtime?.activity) setChatRuntimeActivity(sessionId, {
    ...runtime.activity, queuedCount,
    state: runtime.activity.state === 'queued' && !queuedCount ? 'canceled' : runtime.activity.state,
  })
  return true
}

export function clearChatRuntime(sessionId: number): void {
  const hadRuntime = getAgentConversationRuntime(sessionId) != null
  const nextQueue = queuedSubmissions.filter(
    (submission) => submission.sessionId !== sessionId,
  )
  const queueChanged = nextQueue.length !== queuedSubmissions.length
  if (queueChanged) queuedSubmissions = nextQueue
  clearAgentConversationRuntime(sessionId)
  if (!hadRuntime && queueChanged) emitQueueChange()
}
