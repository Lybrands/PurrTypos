import type { AgentConversationMessage } from '../contracts.ts'
import type { AgentChunkHandler } from './types.ts'

function updateLastAssistant(
  ctx: Parameters<AgentChunkHandler>[1],
  updater: (message: AgentConversationMessage) => AgentConversationMessage,
): void {
  if (!ctx.host.isVisible()) return
  ctx.host.scheduleCommit((previous) => {
    const next = [...previous]
    const last = next.at(-1)
    if (!last || last.role !== 'assistant') return previous
    next[next.length - 1] = updater(last)
    return next
  })
}

export const handleLongTaskDispatched: AgentChunkHandler = (chunk, ctx) => {
  const dispatched = chunk.longTaskDispatched
  if (!dispatched?.taskId) return
  ctx.acc.longTaskId = dispatched.taskId
  updateLastAssistant(ctx, (message) => ({
    ...message,
    longTaskId: dispatched.taskId,
  }))
}

export const handleLongTaskProgress: AgentChunkHandler = (chunk, ctx) => {
  const progress = chunk.longTaskProgress
  if (!progress?.taskId) return
  ctx.acc.longTaskId = progress.taskId
  updateLastAssistant(ctx, (message) => ({
    ...message,
    longTaskId: progress.taskId,
  }))
}
