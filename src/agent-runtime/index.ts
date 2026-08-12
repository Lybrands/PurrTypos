/**
 * Shared Agent conversation runtime.
 *
 * Business surfaces consume this boundary instead of implementing SSE state,
 * terminal handling, persistence conversion, and frame batching themselves.
 */
export type {
  AgentConversationActivity,
  AgentConversationMessage,
  AgentConversationMessage as ChatMessage,
  AgentQueuedSubmission,
  AgentSessionId,
  AiSubAgentActivity,
  AiTaskPlan,
  AiTaskStep,
  ToolCallSegment,
} from './contracts'
export {
  buildHistoryConverter,
  EMPTY_RESPONSE_MESSAGE,
} from './chatHistory'
export {
  getActiveTaskPlan,
  getTaskPlanCountLabel,
  getTaskPlanProgress,
  getVisibleTaskPlanSteps,
  isImplicitRespondStep,
  MIN_VISIBLE_TASK_PLAN_STEPS,
  shouldShowTaskPlan,
} from './taskPlan'
export {
  calculateContextUsage,
  formatContextTokens,
  type ContextUsage,
  type ContextUsageSource,
} from './contextUsage'
export {
  buildStreamOptions,
  type BuiltStream,
  type StreamRequestOptions,
} from './streamOptions'
export {
  dispatchChunk,
  type AccState,
  type AiStreamChunk,
  type ChunkCtx,
} from '../Workspace/AiPanel/hooks/chunkHandlers'
export { createCommitScheduler } from '../Workspace/AiPanel/hooks/chunkHandlers/commitScheduler'
export { parseConversationsFromApi } from '../Workspace/AiPanel/utils'
export {
  getAgentConversationCapabilities,
  type AgentConversationCapabilities,
  type AgentSubmitMode,
} from './conversationCapabilities'
export {
  AgentChunkReplay,
  type AgentChunkTurnSeed,
} from './chunkReplay'
