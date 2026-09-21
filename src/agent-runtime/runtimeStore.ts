import { createStore } from 'zustand/vanilla'
import type {
  AgentConversationActivity,
  AgentConversationMessage,
  AgentSessionId,
} from './contracts.ts'

export interface AgentConversationRuntime {
  sessionId: AgentSessionId
  messages: AgentConversationMessage[]
  running: boolean
  stopping: boolean
  activity?: AgentConversationActivity
  streamId?: string
  updatedAt: number
  revision: number
}

/**
 * 会话运行时 store（zustand vanilla）：Map<sessionId, runtime> + 全局 version。
 * 导出 API 与原手写实现完全一致（订阅/版本号/no-op 语义），消费者零改动；
 * revision 为全局单调种子，供稳定读取门控（readStableConversationProjection）。
 */

interface RuntimeStoreState {
  runtimes: Map<AgentSessionId, AgentConversationRuntime>
  version: number
}

const runtimeStore = createStore<RuntimeStoreState>(() => ({
  runtimes: new Map(),
  version: 0,
}))

// 全局单调修订号（跨会话共享，不参与订阅）
let runtimeRevision = 0

function nextRuntimeRevision(): number {
  runtimeRevision += 1
  return runtimeRevision
}

function commitRuntime(next: AgentConversationRuntime): void {
  runtimeStore.setState((state) => {
    const runtimes = new Map(state.runtimes)
    runtimes.set(next.sessionId, next)
    return { runtimes, version: state.version + 1 }
  })
}

export function subscribeAgentConversationRuntime(
  listener: () => void,
): () => void {
  return runtimeStore.subscribe(listener)
}

export function getAgentConversationRuntimeVersion(): number {
  return runtimeStore.getState().version
}

export function getAgentConversationRuntime(
  sessionId: AgentSessionId | null | undefined,
): AgentConversationRuntime | undefined {
  return sessionId == null
    ? undefined
    : runtimeStore.getState().runtimes.get(sessionId)
}

export function replaceAgentConversationMessages(
  sessionId: AgentSessionId,
  messages: AgentConversationMessage[],
): void {
  const current = getAgentConversationRuntime(sessionId)
  commitRuntime({
    sessionId,
    messages,
    running: current?.running ?? false,
    stopping: current?.stopping ?? false,
    activity: current?.activity,
    streamId: current?.streamId,
    updatedAt: Date.now(),
    revision: nextRuntimeRevision(),
  })
}

export function setAgentConversationStopping(
  sessionId: AgentSessionId,
  stopping: boolean,
): void {
  const current = getAgentConversationRuntime(sessionId)
  if (!current || current.stopping === stopping) return
  commitRuntime({
    ...current,
    stopping,
    updatedAt: Date.now(),
    revision: nextRuntimeRevision(),
  })
}

export function updateAgentConversationMessages(
  sessionId: AgentSessionId,
  updater: (messages: AgentConversationMessage[]) => AgentConversationMessage[],
): void {
  const current = getAgentConversationRuntime(sessionId)
  if (!current) return
  const messages = updater(current.messages)
  if (messages === current.messages) return
  commitRuntime({
    ...current,
    messages,
    updatedAt: Date.now(),
    revision: nextRuntimeRevision(),
  })
}

export function setAgentConversationRunning(
  sessionId: AgentSessionId,
  running: boolean | ((current: boolean) => boolean),
): void {
  const current = getAgentConversationRuntime(sessionId)
  if (!current) return
  const next = typeof running === 'function'
    ? running(current.running)
    : running
  if (next === current.running) return
  commitRuntime({
    ...current,
    running: next,
    updatedAt: Date.now(),
    revision: nextRuntimeRevision(),
  })
}

export function setAgentConversationActivity(
  sessionId: AgentSessionId,
  activity: AgentConversationActivity,
): void {
  const current = getAgentConversationRuntime(sessionId)
  if (!current) return
  if (
    current.activity?.state === activity.state
    && current.activity.queuedCount === activity.queuedCount
  ) return
  commitRuntime({
    ...current,
    activity,
    updatedAt: Date.now(),
    revision: nextRuntimeRevision(),
  })
}

export function setAgentConversationStreamId(
  sessionId: AgentSessionId,
  streamId: string | undefined,
): void {
  const current = getAgentConversationRuntime(sessionId)
  if (!current) return
  commitRuntime({
    ...current,
    streamId,
    updatedAt: Date.now(),
    revision: nextRuntimeRevision(),
  })
}

export function getAgentConversationActivities(): Record<
  string,
  AgentConversationActivity
> {
  const activities: Record<string, AgentConversationActivity> = {}
  runtimeStore.getState().runtimes.forEach((runtime, sessionId) => {
    if (runtime.activity) activities[String(sessionId)] = runtime.activity
  })
  return activities
}

export function clearAgentConversationRuntime(sessionId: AgentSessionId): void {
  runtimeStore.setState((state) => {
    if (!state.runtimes.has(sessionId)) return state
    const runtimes = new Map(state.runtimes)
    runtimes.delete(sessionId)
    return { runtimes, version: state.version + 1 }
  })
}
