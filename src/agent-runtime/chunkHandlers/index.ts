import { handleCanonicalOutput } from './canonical.ts'
import {
  handleLongTaskDispatched,
  handleLongTaskProgress,
} from './durableTask.ts'
import {
  handleDone,
  handleError,
  handleRequestResultTerminal,
  handleRunResultTerminal,
} from './terminal.ts'
import type {
  AgentChunkRuntimeContext,
  AiStreamChunk,
} from './types.ts'

export type {
  AgentAccumulator,
  AgentChunkHost,
  AgentChunkRuntimeContext,
  AgentModelIdentity,
  AgentRunOutcome,
  AgentTerminalSnapshot,
  AiStreamChunk,
} from './types.ts'
export { initialAgentAccumulator } from './types.ts'
export { createCommitScheduler } from './commitScheduler.ts'
export {
  handleLongTaskDispatched,
  handleLongTaskProgress,
} from './durableTask.ts'
export {
  EMPTY_RESPONSE_MESSAGE,
  handleDone,
  handleError,
  handleRunResultTerminal,
  MANUAL_ABORT_MESSAGE,
} from './terminal.ts'

export function dispatchAgentChunk(
  chunk: AiStreamChunk,
  context: AgentChunkRuntimeContext,
): void {
  const receiptRunId = String(chunk.requestReceipt?.runId || '').trim()
  if (receiptRunId) context.acc.conversationRunId = receiptRunId
  if (handleCanonicalOutput(chunk, context)) return
  if (handleRequestResultTerminal(chunk, context)) return
  if (handleRunResultTerminal(chunk, context)) return
  if (handleError(chunk, context)) return
  context.host.onHostChunk?.(chunk)
  handleLongTaskDispatched(chunk, context)
  handleLongTaskProgress(chunk, context)
  handleDone(chunk, context)
}
