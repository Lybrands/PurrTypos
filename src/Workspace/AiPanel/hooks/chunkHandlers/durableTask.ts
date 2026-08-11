import type { ChatMessage } from '../chat.types'
import type { ChunkHandler } from './types'

function updateLastAssistant(
  ctx: Parameters<ChunkHandler>[1],
  updater: (message: ChatMessage) => ChatMessage,
): void {
  if (!ctx.isVisibleSession()) return
  ctx.scheduleCommit((previous) => {
    const next = [...previous]
    const last = next.at(-1)
    if (!last || last.role !== 'assistant') return previous
    next[next.length - 1] = updater(last as ChatMessage)
    return next
  })
}

export const handleLongTaskDispatched: ChunkHandler = (chunk, ctx) => {
  const dispatched = chunk.longTaskDispatched
  if (!dispatched?.taskId) return
  ctx.acc.longTaskId = dispatched.taskId
  updateLastAssistant(ctx, (message) => ({
    ...message,
    longTaskId: dispatched.taskId,
  }))
}

export const handleLongTaskProgress: ChunkHandler = (chunk, ctx) => {
  const progress = chunk.longTaskProgress
  if (!progress?.taskId) return
  ctx.acc.longTaskId = progress.taskId
  updateLastAssistant(ctx, (message) => ({
    ...message,
    longTaskId: progress.taskId,
  }))
}
