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
import { createStore } from 'zustand/vanilla'
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

// 队列段（zustand vanilla）：版本号供组合订阅快照使用。
// clearChatRuntime 需要「只通知一次」：换队列内容时静音队列订阅，
// 由运行时清理（或队列版本号）承担唯一一次通知。
const queueStore = createStore<{ queue: QueuedChatSubmission[]; version: number }>(
  () => ({ queue: [], version: 0 }),
)
let queueNotifyMuted = false

function currentQueue(): QueuedChatSubmission[] {
  return queueStore.getState().queue
}

export function subscribeChatRuntime(listener: () => void): () => void {
  const unsubscribeRuntime = subscribeAgentConversationRuntime(listener)
  const unsubscribeQueue = queueStore.subscribe(() => {
    if (!queueNotifyMuted) listener()
  })
  return () => {
    unsubscribeRuntime()
    unsubscribeQueue()
  }
}

export function getChatRuntimeVersion(): number {
  return getAgentConversationRuntimeVersion() + queueStore.getState().version
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
  return currentQueue()
}

export function replaceChatRuntimeQueue(queue: QueuedChatSubmission[]): void {
  queueStore.setState((state) => ({ queue, version: state.version + 1 }))
}

export function updateChatQueuedSubmission(
  sessionId: number, bookId: string | number, id: string, patch: QueuedSubmissionEdit | null,
): boolean {
  const queue = currentQueue()
  const next = changeQueuedSubmission(queue, id, patch,
    item => item.sessionId === sessionId && item.bookId === bookId)
  if (next === queue) return false
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
  const queue = currentQueue()
  const nextQueue = queue.filter(
    (submission) => submission.sessionId !== sessionId,
  )
  const queueChanged = nextQueue.length !== queue.length
  if (queueChanged) {
    // 先换内容再通知：清理必须只产生一次组合通知，且通知时读到一致的队列
    if (hadRuntime) {
      queueNotifyMuted = true
      try {
        queueStore.setState({ queue: nextQueue })
        clearAgentConversationRuntime(sessionId)
      } finally {
        queueNotifyMuted = false
      }
      return
    }
    replaceChatRuntimeQueue(nextQueue)
  }
  clearAgentConversationRuntime(sessionId)
}
