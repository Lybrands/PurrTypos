/**
 * Shared Agent conversation runtime.
 *
 * Business surfaces consume this boundary instead of implementing SSE state,
 * terminal handling, persistence conversion, and frame batching themselves.
 */
export type {
  AiTaskPlan,
  AiTaskStep,
  ChatMessage,
  ToolCallSegment,
} from '../Workspace/AiPanel/hooks/chat.types'
export {
  dispatchChunk,
  type AccState,
  type AiStreamChunk,
  type ChunkCtx,
} from '../Workspace/AiPanel/hooks/chunkHandlers'
export { createCommitScheduler } from '../Workspace/AiPanel/hooks/chunkHandlers/commitScheduler'
export { buildHistoryConverter } from '../Workspace/AiPanel/hooks/chatHistory'
export { parseConversationsFromApi } from '../Workspace/AiPanel/utils'
export {
  getAgentConversationCapabilities,
  type AgentConversationCapabilities,
  type AgentSubmitMode,
} from './conversationCapabilities'
