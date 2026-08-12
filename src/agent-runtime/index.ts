/**
 * Shared Agent conversation runtime.
 *
 * Business surfaces consume this boundary instead of implementing SSE state,
 * terminal handling, persistence conversion, and frame batching themselves.
 */
export type {
  AgentConversationActivity,
  AgentConversationMessage,
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
  createCommitScheduler,
  dispatchAgentChunk,
  initialAgentAccumulator,
  type AgentAccumulator,
  type AgentChunkHost,
  type AgentChunkRuntimeContext,
  type AgentModelIdentity,
  type AgentRunOutcome,
  type AgentTerminalSnapshot,
  type AiStreamChunk,
} from './chunkHandlers/index'
export {
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
  type AgentConversationRuntime,
} from './runtimeStore'
export {
  getAgentConversationCapabilities,
  type AgentConversationCapabilities,
  type AgentSubmitMode,
} from './conversationCapabilities'
export {
  AgentChunkReplay,
  type AgentChunkTurnSeed,
} from './chunkReplay'
