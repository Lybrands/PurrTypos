import type {
  AiModelConfig,
  Outline,
  AiSession,
  AiErrorReport,
  EntityId,
  SettingDiffCardState,
  ToolApprovalRequest,
  AiAgentDelegation,
  AiContextBudgetState,
  AiContextCompactionState,
} from "../../../types";

export type ToolCallLabelOutcome = "ok" | "context_error";

export type AiTaskStepType =
  | "read"
  | "analyze"
  | "write"
  | "review"
  | "confirm";

export type AiTaskStepStatus =
  | "pending"
  | "running"
  | "done"
  | "blocked"
  | "failed";

export type AiTaskStepExecutor = "model" | "tool" | "agent";

export type AiTaskPlanStatus =
  | "planned"
  | "running"
  | "paused"
  | "done"
  | "blocked"
  | "failed"
  | "canceled";

export interface AiTaskStep {
  id: string;
  title: string;
  description?: string;
  type: AiTaskStepType;
  status: AiTaskStepStatus;
  executor?: AiTaskStepExecutor;
  riskLevel?: "read" | "write" | "destructive";
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
  /** 本段内已执行完成的工具数量（与后端 toolIndexCompleted 同步，顺序递增） */
  completedToolCount?: number;
  /** 工具批次开始时间（performance.now），仅实时 UI 使用。 */
  startedAt?: number;
  /** 整个工具批次耗时；完成时写入历史。 */
  durationMs?: number;
}

export interface ChatMessage {
  role: "user" | "assistant" | "system";
  content: string;
  /** 用户消息的发送时间；历史记录来自数据库，实时消息在发送时写入。 */
  sentAt?: string;
  /** 已落库的 ai_conversations.id，仅 assistant 消息有值。 */
  conversationId?: number;
  /** 本轮 Agent Run id，用于 To-dos 状态与落库回填。 */
  agentRunId?: string;
  /** 本轮创建或关联的持久任务；其生命周期独立于调度 Run。 */
  longTaskId?: string;
  isError?: boolean;
  /** 流式过程已产生可检查内容时，保留过程并在末尾单独显示的终止原因。 */
  error?: string;
  /** 用户主动终止等非错误终态，作为独立状态展示，不混入回答正文。 */
  termination?: string;
  /** 自动创建的本地脱敏错误记录。 */
  errorReport?: AiErrorReport;
  model?: string;
  /** 本轮开始时间（performance.now），仅实时 UI 使用。 */
  turnStartedAt?: number;
  /** 从发送到完成/中止/报错的整轮耗时。 */
  durationMs?: number;
  /** 当前公开执行说明，流式时持续追加。 */
  commentary?: string;
  /** 当前公开说明块开始时间（performance.now），仅实时 UI 使用。 */
  commentaryStartedAt?: number;
  /** 与工具批次交错的公开执行说明。 */
  commentaryBlocks?: string[];
  /** 与 commentaryBlocks 等长：各说明块耗时（毫秒）。 */
  commentaryDurationsMs?: number[];
  toolCalling?: boolean;
  toolCallSegments?: ToolCallSegment[];
  /** AI 提议的设定 diff 卡片（人物 / 故事背景） */
  settingDiffCards?: SettingDiffCardState[];
  /** 等待用户批准的高风险 Agent 工具调用。 */
  toolApprovals?: ToolApprovalRequest[];
  /** AI 将用户目标拆成的任务计划；保存在消息上供顶部任务胶囊读取。 */
  taskPlan?: AiTaskPlan;
  /** 当前主 Run 调用的子 Agent 生命周期状态。 */
  delegations?: AiAgentDelegation[];
  /** 子 Run 通过根 Run 统一事件流产生的独立消息/工具活动。 */
  subAgentActivities?: AiSubAgentActivity[];
  /** 本轮会话压缩的实时/最终状态。 */
  contextCompaction?: AiContextCompactionState;
  /** 后端对本轮完整模型输入的实际预算。 */
  contextBudget?: AiContextBudgetState;
}

export interface AiSubAgentActivity {
  delegationId: string;
  parentRunId: string;
  rootRunId: string;
  childRunId?: string | null;
  agentRole: string;
  agentTitle?: string | null;
  objective?: string;
  status: AiAgentDelegation["status"];
  /** Reuses the ordinary assistant reducer without merging concurrent tokens. */
  message: ChatMessage;
}

export interface UseChatSubmitParams {
  /** 当前选中的模型配置（含 apiKey、baseUrl、name）；为空时无法发送 */
  selectedModelConfig: AiModelConfig | null;
  prompt: string;
  setPrompt: React.Dispatch<React.SetStateAction<string>>;
  loading: boolean;
  setLoading: React.Dispatch<React.SetStateAction<boolean>>;
  conversations: ChatMessage[];
  setConversations: React.Dispatch<React.SetStateAction<ChatMessage[]>>;
  bookId: EntityId | null | undefined;
  chapterId: EntityId | null | undefined;
  activeSessionId: number | null;
  setActiveSessionId: React.Dispatch<React.SetStateAction<number | null>>;
  sessions: AiSession[];
  setSessions: React.Dispatch<React.SetStateAction<AiSession[]>>;
  associatedChapterIds: EntityId[];
  associatedOutlineIds: EntityId[];
  writingChapters: { id: EntityId; title: string }[];
  availableOutlines: Outline[];
  currentChapterTitle?: string;
  selectedModel: string;
  agentEnabled: boolean;
  selectedMemoryIds?: (number | string)[];
  selectedForeshadowingIds?: (number | string)[];
  /**
   * 会话作用域：setting = 全局会话（不绑章节），不要求选中章节即可发送；
   * 默认 chapter（必须先选章节）。
   */
  sessionScope?: "chapter" | "setting";
}
