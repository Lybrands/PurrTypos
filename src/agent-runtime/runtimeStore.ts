import type {
  AgentConversationActivity,
  AgentConversationMessage,
  AgentSessionId,
} from './contracts.ts'

export interface AgentConversationRuntime {
  sessionId: AgentSessionId
  messages: AgentConversationMessage[]
  running: boolean
  activity?: AgentConversationActivity
  streamId?: string
  updatedAt: number
}

const runtimes = new Map<AgentSessionId, AgentConversationRuntime>()
const listeners = new Set<() => void>()
let version = 0

function emitChange(): void {
  version += 1
  listeners.forEach((listener) => listener())
}

export function subscribeAgentConversationRuntime(
  listener: () => void,
): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function getAgentConversationRuntimeVersion(): number {
  return version
}

export function getAgentConversationRuntime(
  sessionId: AgentSessionId | null | undefined,
): AgentConversationRuntime | undefined {
  return sessionId == null ? undefined : runtimes.get(sessionId)
}

export function replaceAgentConversationMessages(
  sessionId: AgentSessionId,
  messages: AgentConversationMessage[],
): void {
  const current = runtimes.get(sessionId)
  runtimes.set(sessionId, {
    sessionId,
    messages,
    running: current?.running ?? false,
    activity: current?.activity,
    streamId: current?.streamId,
    updatedAt: Date.now(),
  })
  emitChange()
}

export function updateAgentConversationMessages(
  sessionId: AgentSessionId,
  updater: (messages: AgentConversationMessage[]) => AgentConversationMessage[],
): void {
  const current = runtimes.get(sessionId)
  if (!current) return
  const messages = updater(current.messages)
  if (messages === current.messages) return
  runtimes.set(sessionId, {
    ...current,
    messages,
    updatedAt: Date.now(),
  })
  emitChange()
}

export function setAgentConversationRunning(
  sessionId: AgentSessionId,
  running: boolean | ((current: boolean) => boolean),
): void {
  const current = runtimes.get(sessionId)
  if (!current) return
  const next = typeof running === 'function'
    ? running(current.running)
    : running
  if (next === current.running) return
  runtimes.set(sessionId, {
    ...current,
    running: next,
    updatedAt: Date.now(),
  })
  emitChange()
}

export function setAgentConversationActivity(
  sessionId: AgentSessionId,
  activity: AgentConversationActivity,
): void {
  const current = runtimes.get(sessionId)
  if (!current) return
  if (
    current.activity?.state === activity.state
    && current.activity.queuedCount === activity.queuedCount
  ) return
  runtimes.set(sessionId, {
    ...current,
    activity,
    updatedAt: Date.now(),
  })
  emitChange()
}

export function setAgentConversationStreamId(
  sessionId: AgentSessionId,
  streamId: string | undefined,
): void {
  const current = runtimes.get(sessionId)
  if (!current) return
  runtimes.set(sessionId, {
    ...current,
    streamId,
    updatedAt: Date.now(),
  })
  emitChange()
}

export function getAgentConversationActivities(): Record<
  string,
  AgentConversationActivity
> {
  const activities: Record<string, AgentConversationActivity> = {}
  runtimes.forEach((runtime, sessionId) => {
    if (runtime.activity) activities[String(sessionId)] = runtime.activity
  })
  return activities
}

export function clearAgentConversationRuntime(sessionId: AgentSessionId): void {
  if (!runtimes.delete(sessionId)) return
  emitChange()
}
