import {
  clearAgentConversationRuntime,
  getAgentConversationActivities,
  getAgentConversationRuntime,
  getAgentConversationRuntimeVersion,
  replaceAgentConversationMessages,
  setAgentConversationActivity,
  setAgentConversationRunning,
  setAgentConversationStreamId,
  subscribeAgentConversationRuntime,
  updateAgentConversationMessages,
} from '../../../agent-runtime/runtimeStore'
import type { AgentConversationMessage } from '../../../agent-runtime/contracts'
import type {
  ChatSessionActivity,
  QueuedChatSubmission,
} from './chatQueue'

export interface ChatSessionRuntime {
  sessionId: number
  messages: AgentConversationMessage[]
  loading: boolean
  activity?: ChatSessionActivity
  streamId?: string
  updatedAt: number
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
    activity: runtime.activity as ChatSessionActivity | undefined,
    streamId: runtime.streamId,
    updatedAt: runtime.updatedAt,
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
