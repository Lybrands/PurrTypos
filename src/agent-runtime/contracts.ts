import type {
  AiAgentDelegation,
  AiContextBudgetState,
  AiContextCompactionState,
  AiErrorReport,
  ToolApprovalRequest,
} from '../types.ts'
import type { CanonicalOutputState } from './canonicalOutput.ts'

export type AgentSessionId = string | number

export interface AgentQueuedSubmission {
  id: string
  sessionId: AgentSessionId
  content: string
}

export interface AgentConversationActivity {
  state: 'running' | 'paused' | 'queued' | 'completed' | 'failed' | 'canceled'
  queuedCount: number
}

export type ToolCallLabelOutcome = 'ok' | 'context_error'

export type AiTaskStepType =
  | 'read'
  | 'analyze'
  | 'write'
  | 'review'
  | 'confirm'

export type AiTaskStepStatus =
  | 'pending'
  | 'running'
  | 'done'
  | 'blocked'
  | 'failed'

export type AiTaskStepExecutor = 'model' | 'tool' | 'agent'

export type AiTaskPlanStatus =
  | 'planned'
  | 'running'
  | 'paused'
  | 'done'
  | 'blocked'
  | 'failed'
  | 'canceled'

export interface AiTaskStep {
  id: string;
  title: string;
  description?: string;
  type: AiTaskStepType;
  status: AiTaskStepStatus;
  executor?: AiTaskStepExecutor;
  riskLevel?: 'read' | 'write' | 'destructive';
  suggestedTools?: string[];
  planningCapability?: string;
  protocolPrivate?: boolean;
  agentRole?: string;
  assignment?: Record<string, unknown>;
  dependsOn?: string[];
  resultSummary?: string;
  error?: string;
}

export interface AiTaskPlan {
  /** Owns lifecycle updates for this plan; child Runs must not terminalize it. */
  runId?: string;
  title: string;
  goal?: string;
  status: AiTaskPlanStatus;
  steps: AiTaskStep[];
}

/** 一次工具批次及其在公开执行过程中的位置。 */
export interface ToolCallSegment {
  labels: string[];
  /**
   * 紧邻本段之前的 commentaryBlocks 下标。null 表示本段前没有公开说明。
   */
  commentaryBlockIndex: number | null;
  /** 与 labels 同长度：目录/参数无法与当前书籍对齐时标记 context_error，气泡显示为失败 */
  labelOutcomes?: ToolCallLabelOutcome[];
  /** 与 labels 同长度：该次工具调用是否命中请求内只读缓存 */
  cachedFlags?: boolean[];
  /** 本段内已执行完成的工具数量。 */
  completedToolCount?: number;
  /** 工具批次开始时间（performance.now），仅实时 UI 使用。 */
  startedAt?: number;
  /** 整个工具批次耗时；完成时写入历史。 */
  durationMs?: number;
  /** 与 labels 等长：每个具体操作的已完成耗时。 */
  itemDurationsMs?: Array<number | null>;
  /** 当前顺序操作的开始时间（performance.now）。 */
  activeItemStartedAt?: number;
}

export interface AgentConversationMessage {
  role: 'user' | 'assistant' | 'system'
  content: string
  streamingContent?: string
  clientTurnId?: string
  sentAt?: string
  conversationId?: number
  agentRunId?: string
  longTaskId?: string
  isError?: boolean
  error?: string
  termination?: string
  errorReport?: AiErrorReport
  model?: string
  turnStartedAt?: number
  durationMs?: number
  commentary?: string
  commentaryStartedAt?: number
  commentaryBlocks?: string[]
  commentaryDurationsMs?: number[]
  toolCalling?: boolean
  toolCallSegments?: ToolCallSegment[]
  toolApprovals?: ToolApprovalRequest[]
  taskPlan?: AiTaskPlan
  delegations?: AiAgentDelegation[]
  subAgentActivities?: AiSubAgentActivity[]
  contextCompaction?: AiContextCompactionState
  contextBudget?: AiContextBudgetState
  canonicalOutput?: CanonicalOutputState
}

export interface AiSubAgentActivity {
  delegationId: string;
  parentRunId: string;
  rootRunId: string;
  childRunId?: string | null;
  agentRole: string;
  agentTitle?: string | null;
  objective?: string;
  status: AiAgentDelegation['status'];
  /** Reuses the ordinary assistant reducer without merging concurrent tokens. */
  message: AgentConversationMessage;
}
