import type {
  AiAgentDelegation,
  AiContextBudgetState,
  AiContextCompactionState,
  ElectronAPI,
} from '../../types.ts'
import type { CanonicalOutputState } from '../canonicalOutput.ts'
import type {
  AgentConversationMessage,
  AgentSessionId,
  AiSubAgentActivity,
  AiTaskPlan,
  ToolCallSegment,
} from '../contracts.ts'

export type AiStreamChunk = Parameters<
  Parameters<ElectronAPI['onAiChunk']>[0]
>[0]

export interface AgentModelIdentity {
  configId?: string
  name: string
}

export interface AgentAccumulator {
  response: string
  commentary: string
  sessionId: AgentSessionId
  userText: string
  model: string
  turnStartedAt: number
  toolCallSegments?: ToolCallSegment[]
  commentaryBlocks?: string[]
  commentaryDurationsMs?: number[]
  commentaryBlockStartedAt?: number
  agentRunId?: string
  conversationRunId?: string
  longTaskId?: string
  taskPlan?: AiTaskPlan
  delegations?: AiAgentDelegation[]
  contextCompaction?: AiContextCompactionState
  contextBudget?: AiContextBudgetState
  subAgentActivities?: AiSubAgentActivity[]
  subAgentAccumulators?: Record<string, AgentAccumulator>
  canonicalOutput?: CanonicalOutputState
  terminalSettlement?: {
    phase: 'projecting' | 'delivered' | 'settled'
    outcome?: AgentRunOutcome
    runId?: string
  }
}

export function initialAgentAccumulator(input: {
  sessionId: AgentSessionId
  userText: string
  turnStartedAt: number
  model?: string
}): AgentAccumulator {
  return {
    response: '',
    commentary: '',
    sessionId: input.sessionId,
    userText: input.userText,
    model: input.model ?? '',
    turnStartedAt: input.turnStartedAt,
    commentaryBlocks: [],
    commentaryDurationsMs: [],
  }
}

export type AgentRunOutcome =
  | 'completed'
  | 'paused'
  | 'failed'
  | 'canceled'

export interface AgentTerminalSnapshot {
  sessionId: AgentSessionId
  userText: string
  response: string
  model?: string
  agentRunId?: string
  longTaskId?: string
  taskPlan?: AiTaskPlan
  commentaryBlocks: string[]
  commentaryDurationsMs: number[]
  toolCallSegments: ToolCallSegment[]
  contextCompaction?: AiContextCompactionState
  contextBudget?: AiContextBudgetState
  durationMs: number
}

export interface AgentChunkHost {
  readMessages(): AgentConversationMessage[]
  replaceMessages(messages: AgentConversationMessage[]): void
  scheduleCommit(
    updater: (messages: AgentConversationMessage[]) => AgentConversationMessage[],
  ): void
  flushCommits(): void
  setRunning(running: boolean): void
  isVisible(): boolean
  onHostChunk?(chunk: AiStreamChunk): void
  onSettled(outcome: AgentRunOutcome, snapshot: AgentTerminalSnapshot): void
}

export interface AgentChunkRuntimeContext {
  acc: AgentAccumulator
  sessionId: AgentSessionId
  modelIdentity: AgentModelIdentity
  host: AgentChunkHost
  persistConversation?: boolean
  now(): number
}

export type AgentChunkHandler = (
  chunk: AiStreamChunk,
  context: AgentChunkRuntimeContext,
) => boolean | void
