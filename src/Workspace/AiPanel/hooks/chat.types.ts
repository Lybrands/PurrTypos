import type {
  AiModelConfig,
  Outline,
  AiSession,
  EntityId,
  SettingDiffCardState,
} from "../../../types";
import type { AgentConversationMessage } from "../../../agent-runtime/contracts";

export type {
  AiSubAgentActivity,
  AiTaskPlan,
  AiTaskPlanStatus,
  AiTaskStep,
  AiTaskStepExecutor,
  AiTaskStepStatus,
  AiTaskStepType,
  ToolCallLabelOutcome,
  ToolCallSegment,
} from "../../../agent-runtime/contracts";

export type ChatMessage = AgentConversationMessage;

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
  onAssistantAttachment?: (
    message: AgentConversationMessage,
    card: SettingDiffCardState,
  ) => void;
}
