import type {
  AiModelConfig,
  Outline,
  AiSession,
  EntityId,
  SettingDiffCardState,
} from "../../../types";
import type { WritingSubagentRole } from "../pipelineStages";

export type ToolCallLabelOutcome = "ok" | "context_error";

/** 一段「调用前文案 + 该次调用的正在查看列表」，按调用顺序排列 */
export interface ToolCallSegment {
  textBefore: string;
  labels: string[];
  /** 与 labels 同长度：目录/参数无法与当前书籍对齐时标记 context_error，气泡显示为失败 */
  labelOutcomes?: ToolCallLabelOutcome[];
  /** 与 labels 同长度：该次工具调用是否命中请求内只读缓存 */
  cachedFlags?: boolean[];
  /** 本段内已执行完成的工具数量（与后端 toolIndexCompleted 同步，顺序递增） */
  completedToolCount?: number;
  trace?: {
    insertedByDag?: number;
    insertedSkillNames?: string[];
    plannedToolNames?: string[];
    repairedRounds?: number;
    repairReasons?: string[];
    /** 当前工具执行阶段（如 subagent 的 analyze/plan） */
    stage?: string;
  };
}

export interface ChatMessage {
  role: "user" | "assistant" | "system";
  content: string;
  isError?: boolean;
  model?: string;
  /** 当前/最后一轮思考（流式时持续追加） */
  thinking?: string;
  /** 多轮思考内容，与 toolCallSegments 交错：思考1、工具1、思考2、工具2… */
  thinkingBlocks?: string[];
  toolCalling?: boolean;
  toolCallSegments?: ToolCallSegment[];
  contentAfterToolCalls?: string;
  /** Subagent：当前阶段 id（如 analyze） */
  subagentStageId?: string;
  /** 写作专家模式：当前阶段展示名（如「分析专家」） */
  subagentStageName?: string;
  /** Subagent：该阶段是否仍在执行（含工具调用） */
  subagentStageWorking?: boolean;
  /** Subagent：最近一次完成阶段的展示名 */
  subagentLastCompletedStageName?: string;
  /** Subagent：按返回顺序记录阶段状态 */
  subagentStages?: Array<{
    id: string;
    name: string;
    status: "running" | "done";
  }>;
  /** 主稿专家正在输出阶段间过渡文案 */
  subagentBridging?: boolean;
  /** 已进入最终主稿专家答复流 */
  subagentMainPresenter?: boolean;
  /** 写作专家：各阶段摘要（Markdown），在工具条与主答复之前展示 */
  subagentPipelineDigest?: string;
  /** 按需子专家进行中 */
  writingSubagentActive?: boolean;
  writingSubagentLabel?: string;
  writingSubagentRole?: WritingSubagentRole;
  /** 子专家结构化结果（审校 / 规划 / 润色 / 风格） */
  subagentResult?: { role: WritingSubagentRole; payload: unknown };
  /** AI 提议的设定 diff 卡片（人物 / 故事背景） */
  settingDiffCards?: SettingDiffCardState[];
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
  thinkingEnabled: boolean;
  agentEnabled: boolean;
  /** 用于 max_tokens 等；temperature 由选中模型的 AiModelConfig 与思考开关决定 */
  modelConfigs: Record<string, { label?: string; max_tokens?: number }>;
  selectedMemoryIds?: (number | string)[];
  selectedForeshadowingIds?: (number | string)[];
  agentMode?: "legacy" | "subagent";
  /** legacy 下协作共创时传 collab，主进程注入协商提示并限制写入工具 */
  writingMode?: "default" | "collab";
  /**
   * 会话作用域：setting = 设定会话（人物/背景），不要求选中章节即可发送；
   * 默认 chapter（必须先选章节）。
   */
  sessionScope?: "chapter" | "setting";
  /** 写作专家：下次发送使用的子专家；发送后由 onPendingSubagentRoleConsumed 清空 */
  pendingSubagentRole?: WritingSubagentRole | null;
  onPendingSubagentRoleConsumed?: () => void;
}

/** 写作专家：共用子管线协议与 UI */
export function isWritingExpertPipeline(
  mode: "legacy" | "subagent" | undefined,
): boolean {
  return mode === "subagent";
}
