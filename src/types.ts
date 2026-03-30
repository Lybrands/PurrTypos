/** 书籍 / 大纲 / 章节主键：8 位字符串（数字 + 大小写字母，见 idUtils / shortUuid） */
export type EntityId = string;

export interface Chapter {
  id: EntityId;
  outline_id: EntityId;
  title: string;
  level: number;
  progress: string;
  sort: number;
  parent_id?: EntityId | null;
}

export interface Book {
  id: EntityId;
  title: string;
  cover_color?: string | null;
  enable_volume?: number;
  create_time?: string;
}

export interface CharacterOption {
  id: number;
  category: string;
  value: string;
  sort: number;
}

export interface Character {
  id: number;
  book_id: EntityId;
  name: string;
  gender?: string;
  age?: string;
  height?: string;
  occupation?: string;
  appearance?: string;
  origin?: string;
  personality?: string;
  background: string;
  biography: string;
  tags: string;
  /** 备注（自由补充说明） */
  remark?: string;
  create_time?: string;
}

export interface Outline {
  id: EntityId;
  title: string;
  type?: "global" | "chapter" | "volume";
  sort?: number;
  xmind_data?: string | null;
  file_path?: string | null;
  /** 应用内 Markdown 大纲正文，与 XMind 并行保存 */
  markdown_content?: string | null;
  book_id?: EntityId | null;
  parent_outline_id?: EntityId | null;
  writing_chapter_id?: EntityId | null;
  create_time?: string;
}

export interface VolumeOutline extends Outline {
  chapters: Outline[];
}

export interface Article {
  id: number;
  chapter_id: EntityId;
  content: string;
  update_time?: string;
}

export interface StoryBackgroundAttachment {
  id: number;
  book_id: EntityId;
  name: string;
  stored_path: string;
  create_time?: string;
}

export interface AiSession {
  id: number;
  book_id?: EntityId | null;
  chapter_id?: EntityId | null;
  title: string;
  create_time?: string;
}

export interface AiFavorite {
  id: number;
  session_id: number;
  session_title: string;
  prompt: string;
  content: string;
  create_time?: string;
}

export interface Conversation {
  id: number;
  session_id: number;
  chapter_id: EntityId;
  prompt: string;
  response: string;
  model?: string;
  thinking?: string;
  tool_call_segments?: string | null;
  thinking_blocks?: string | null;
  create_time?: string;
}

/** 本书设定层级 */
export type SparkIdeaLayer = '全局' | '大纲' | '人物' | '章节' | '伏笔';

/** AI 对话模式（与 UI 模式选择一致） */
export const CHAT_AGENT_MODES = ['ask', 'agent', 'expert', 'collab'] as const;
export type ChatAgentMode = (typeof CHAT_AGENT_MODES)[number];

export interface AiSparkIdea {
  id: number | string;  // mem0 使用 UUID 字符串
  book_id: EntityId;
  layer: SparkIdeaLayer;
  content: string;
  chapter_id?: EntityId | null;
  character_id?: number | null;
  create_time?: string;
}

export interface AiForeshadowing {
  id: number | string;  // mem0 使用 UUID 字符串
  book_id: EntityId;
  chapter_id: EntityId;
  content: string;
  type: string;
  expected_chapter_id?: EntityId | null;
  status: '未回收' | '已回收';
  resolved_chapter_id?: EntityId | null;
  create_time?: string;
  update_time?: string;
}

export interface XmindTopic {
  title?: string;
  children?: { attached?: XmindTopic[] };
}

export interface XmindSheet {
  rootTopic?: XmindTopic;
}

export interface ApiResult<T = unknown> {
  success: boolean;
  data: T;
  error?: string;
}

export interface ElectronAPI {
  openXmindFile: () => Promise<string | null>;
  parseXmind: (filePath: string) => Promise<ApiResult<XmindSheet[]>>;
  openFilePath: (filePath: string) => Promise<ApiResult<void>>;
  readFileBuffer: (filePath: string) => Promise<ApiResult<Buffer>>;
  writeExportFiles: (data: { entries: Array<{ path: string; content: string }>; exportAsZip: boolean }) => Promise<ApiResult<void>>;
  exportDatabase: () => Promise<ApiResult<void>>;
  importDatabase: () => Promise<ApiResult<{
    beforeStats: { books: number; outlineChapters: number; articles: number };
    afterStats: { books: number; outlineChapters: number; articles: number };
  }>>;
  getDatabaseInfo: () => Promise<ApiResult<{ dbPath: string; books: number; outlineChapters: number; articles: number }>>;
  openDatabaseDirectory: () => Promise<ApiResult<void>>;
  // 书籍
  getBooks: () => Promise<ApiResult<Book[]>>;
  createBook: (data: {
    title: string;
    enableVolume?: boolean;
  }) => Promise<ApiResult<Book>>;
  deleteBook: (data: { bookId: EntityId }) => Promise<ApiResult<void>>;
  renameBook: (data: {
    bookId: EntityId;
    title: string;
  }) => Promise<ApiResult<void>>;
  /** 本书写作章节正文总字数（与编辑器统计规则一致） */
  getBookWordCount: (data: {
    bookId: EntityId;
  }) => Promise<ApiResult<{ count: number }>>;
  // 人物
  getCharacters: (data: { bookId: EntityId }) => Promise<ApiResult<Character[]>>;
  createCharacter: (data: {
    bookId: EntityId;
    data: Partial<Character>;
  }) => Promise<ApiResult<Character>>;
  updateCharacter: (data: {
    id: number;
    data: Partial<Character>;
  }) => Promise<ApiResult<Character>>;
  deleteCharacter: (data: { id: number }) => Promise<ApiResult<void>>;
  // 人物选项
  getCharacterOptions: (data: {
    category: string;
  }) => Promise<ApiResult<CharacterOption[]>>;
  addCharacterOption: (data: {
    category: string;
    value: string;
  }) => Promise<ApiResult<CharacterOption>>;
  updateCharacterOption: (data: {
    id: number;
    value: string;
  }) => Promise<ApiResult<CharacterOption>>;
  deleteCharacterOption: (data: { id: number }) => Promise<ApiResult<void>>;
  // 大纲
  saveOutline: (data: {
    title: string;
    type?: "global" | "chapter" | "volume";
    xmind_data?: string;
    file_path?: string;
    book_id?: EntityId | null;
    writing_chapter_id?: EntityId | null;
    parent_outline_id?: EntityId | null;
  }) => Promise<ApiResult<Outline>>;
  getVolumeOutlines: (
    bookId?: EntityId | null,
  ) => Promise<ApiResult<VolumeOutline[]>>;
  getOutlineByWritingChapter: (
    writingChapterId: EntityId,
  ) => Promise<ApiResult<Outline | null>>;
  getOutlines: (
    typeFilter?: "global" | "chapter",
  ) => Promise<ApiResult<Outline[]>>;
  getWritingOutline: (bookId?: EntityId | null) => Promise<ApiResult<Outline>>;
  getGlobalOutline: (
    bookId?: EntityId | null,
  ) => Promise<ApiResult<Outline | null>>;
  /** 若无总纲记录则创建空总纲（可只写 Markdown），用于与章节大纲一致的文本大纲入口 */
  ensureGlobalOutline: (
    bookId?: EntityId | null,
  ) => Promise<ApiResult<Outline>>;
  getChapterOutlines: (bookId?: EntityId | null) => Promise<ApiResult<Outline[]>>;
  /** AI 关联大纲列表（卷/章节），顺序与左侧大纲面板章节区一致 */
  getAssociableOutlines: (
    bookId?: EntityId | null,
  ) => Promise<ApiResult<Outline[]>>;
  deleteOutline: (data: { outlineId: EntityId }) => Promise<ApiResult<void>>;
  updateOutline: (data: {
    outlineId: EntityId;
    title?: string;
    xmind_data?: string;
    file_path?: string;
    markdown_content?: string | null;
  }) => Promise<ApiResult<Outline>>;
  // 章节
  saveChapters: (data: {
    outlineId: EntityId;
    chapters: unknown[];
  }) => Promise<ApiResult<void>>;
  getChapters: (data: { outlineId: EntityId }) => Promise<ApiResult<Chapter[]>>;
  addChapter: (data: {
    outlineId: EntityId;
    title: string;
    parentId?: EntityId | null;
  }) => Promise<ApiResult<Chapter>>;
  deleteChapter: (data: { id: EntityId }) => Promise<ApiResult<void>>;
  renameChapter: (data: {
    id: EntityId;
    title: string;
  }) => Promise<ApiResult<void>>;
  updateChapterProgress: (data: {
    id: EntityId;
    progress: string;
  }) => Promise<ApiResult<void>>;
  // 文档
  saveArticle: (data: {
    chapterId: EntityId;
    content: string;
  }) => Promise<ApiResult<void>>;
  getArticle: (data: {
    chapterId: EntityId;
  }) => Promise<ApiResult<Article | null>>;
  getStoryBackground: (data: { bookId: EntityId }) => Promise<ApiResult<{ book_id: EntityId; content: string; update_time?: string } | null>>;
  saveStoryBackground: (data: { bookId: EntityId; content: string }) => Promise<ApiResult<void>>;
  openAndReadTextFile: () => Promise<ApiResult<string>>;
  pickStoryBackgroundAttachments: (data: { bookId: EntityId }) => Promise<ApiResult<StoryBackgroundAttachment[]>>;
  getStoryBackgroundAttachments: (data: { bookId: EntityId }) => Promise<ApiResult<StoryBackgroundAttachment[]>>;
  deleteStoryBackgroundAttachment: (data: { id: number }) => Promise<ApiResult<void>>;
  openStoryBackgroundAttachment: (data: { storedPath: string }) => Promise<string>;
  // AI
  createSession: (data: { bookId: EntityId; chapterId?: EntityId | null }) => Promise<ApiResult<AiSession>>;
  getSessions: (data: { bookId: EntityId; chapterId?: EntityId | null; includeClosed?: boolean }) => Promise<ApiResult<AiSession[]>>;
  setSessionClosed: (data: { sessionId: number }) => Promise<ApiResult<void>>;
  setSessionReopened: (data: { sessionId: number }) => Promise<ApiResult<void>>;
  deleteSession: (data: { sessionId: number }) => Promise<ApiResult<void>>;
  saveConversation: (data: {
    sessionId: number;
    chapterId?: EntityId | null;
    prompt: string;
    response: string;
    model?: string;
    thinking?: string;
    toolCallSegments?: {
      textBefore: string;
      labels: string[];
      completedToolCount?: number;
      trace?: {
        insertedByDag?: number;
        insertedSkillNames?: string[];
        plannedToolNames?: string[];
        repairedRounds?: number;
        repairReasons?: string[];
      };
    }[];
    thinkingBlocks?: string[];
  }) => Promise<ApiResult<void>>;
  getConversations: (data: {
    sessionId: number;
  }) => Promise<ApiResult<Conversation[]>>;
  deleteConversationsAfterTurn: (data: {
    sessionId: number;
    keepTurnCount: number;
  }) => Promise<ApiResult<void>>;
  updateSessionTitle: (data: {
    sessionId: number;
    title: string;
  }) => Promise<ApiResult<void>>;
  saveAiFavorite: (data: {
    sessionId: number;
    sessionTitle: string;
    prompt: string;
    content: string;
  }) => Promise<ApiResult<AiFavorite>>;
  getAiFavorites: () => Promise<ApiResult<AiFavorite[]>>;
  deleteAiFavorite: (data: { id: number }) => Promise<ApiResult<void>>;
  // 本书设定（五层）
  addSparkIdea: (data: { bookId: EntityId; layer: SparkIdeaLayer; content: string; chapterId?: EntityId | null; characterId?: number | null }) => Promise<ApiResult<AiSparkIdea>>;
  updateSparkIdea: (data: { id: number | string; data: Partial<Pick<AiSparkIdea, 'content' | 'chapter_id' | 'character_id'>> }) => Promise<ApiResult<AiSparkIdea>>;
  deleteSparkIdea: (data: { id: number | string }) => Promise<ApiResult<void>>;
  getSparkIdeasByBook: (data: { bookId: EntityId; layer?: SparkIdeaLayer }) => Promise<ApiResult<AiSparkIdea[]>>;
  getSparkIdeasByIds: (data: { ids: (number | string)[] }) => Promise<ApiResult<AiSparkIdea[]>>;
  getSparkIdeasForPrompt: (data: {
    bookId: EntityId;
    query?: string;
    options?: { layers?: SparkIdeaLayer[]; chapterId?: EntityId; limitPerLayer?: number; limit?: number };
  }) => Promise<ApiResult<AiSparkIdea[]>>;
  // 伏笔记忆
  addForeshadowing: (data: { bookId: EntityId; chapterId: EntityId; content: string; type?: string; expectedChapterId?: EntityId | null }) => Promise<ApiResult<AiForeshadowing>>;
  updateForeshadowing: (data: { id: number | string; data: Partial<Pick<AiForeshadowing, 'content' | 'type' | 'expected_chapter_id' | 'status' | 'resolved_chapter_id'>> }) => Promise<ApiResult<AiForeshadowing>>;
  deleteForeshadowing: (data: { id: number | string }) => Promise<ApiResult<void>>;
  getForeshadowingByBook: (data: { bookId: EntityId; status?: '未回收' | '已回收' }) => Promise<ApiResult<AiForeshadowing[]>>;
  getForeshadowingByIds: (data: { ids: (number | string)[] }) => Promise<ApiResult<AiForeshadowing[]>>;
  getForeshadowingForPrompt: (data: { bookId: EntityId; query?: string; options?: { limit?: number; status?: '未回收' | '已回收' } }) => Promise<ApiResult<AiForeshadowing[]>>;
  generateSessionTitle: (data: {
    apiKey: string;
    baseURL?: string;
    prompt: string;
    apiProvider?: "openai" | "anthropic";
    model?: string;
  }) => Promise<ApiResult<string>>;
  listModels: (data: {
    apiKey: string;
    baseURL?: string;
    apiProvider?: "openai" | "anthropic";
  }) => Promise<ApiResult<string[]>>;
  aiChatStream: (data: {
    apiKey: string;
    baseURL?: string;
    /** 默认 openai：OpenAI 兼容 provider；anthropic 使用官方 Messages API */
    apiProvider?: "openai" | "anthropic";
    messages: Array<{ role: string; content: string; tool_calls?: unknown[]; reasoning_content?: string } | { role: "tool"; tool_call_id: string; content: string }>;
    options?: {
      model?: string;
      temperature?: number;
      max_tokens?: number;
      thinking?: { type: "disabled" | "enabled" };
      /** 采样 top-k；写作专家模式由前端设为 45 */
      top_k?: number;
    };
    tools?: unknown[];
    useToolRouter?: boolean;
    bookId?: EntityId | null;
    chapterId?: EntityId | null;
    currentChapterTitle?: string;
    writingChapters?: { id: EntityId; title: string }[];
    availableOutlines?: { id: EntityId; title: string; type?: string }[];
    /** 与界面「关联章节」一致，主进程并入 toolCtx 供写作专家 system 附录 */
    associatedChapterIds?: EntityId[];
    associatedOutlineIds?: EntityId[];
    agentMode?: "legacy" | "subagent";
    chatAgentMode?: "ask" | "agent" | "expert" | "collab";
    /** legacy 下协作共创 */
    writingMode?: "default" | "collab";
    /** 写作专家多选阶段 */
    agentActions?: string[];
  }) => void;
  abortAiStream: () => void;
  onAiChunk: (
    callback: (chunk: {
      delta?: string;
      thinkingDelta?: string;
      done?: boolean;
      aborted?: boolean;
      error?: string;
      model?: string;
      toolCalls?: { id: string; type: string; function: { name: string; arguments: string } }[];
      toolCallsInProgress?: boolean;
      partialContent?: string;
      partialThinking?: string;
      messagesSent?: Array<{ role: string; content: string }>;
      chapterContentUpdated?: EntityId;
      chapterCreated?: {
        chapterId: EntityId;
        title: string;
        parentId?: EntityId | null;
      };
      /** 协作共创：最近一次写入正文的段落（前端以 Markdown 段落块展示） */
      collabLatestParagraph?: string;
      /** 当前批次内第 index 个工具已执行完成（0-based），用于逐条更新 UI */
      toolIndexCompleted?: number;
      /** 本次完成是否命中会话内只读缓存（不读库）；为 true 时前端可隐藏该行 */
      toolFromCache?: boolean;
      /** 本批工具执行前即已命中只读缓存的掩码（与 toolCalls 等长）；为 true 的索引整段不展示 */
      toolReadCacheMask?: boolean[];
      /** 命中请求内只读缓存的工具序号（与 toolIndexCompleted 配合） */
      toolCallCachedIndex?: number;
      /** 工具路由（嵌入/意图模型）失败时的简短提示，由主进程经流式通道下发 */
      toolRouterWarning?: string;
      /** DAG 编排阶段信息，用于前端可视化“自动补前置” */
      orchestratorInfo?: {
        insertedByDag?: number;
        plannedNodeCount?: number;
        insertedSkillNames?: string[];
        plannedToolNames?: string[];
      };
      /** 执行阶段自动修复信息 */
      orchestratorRepair?: {
        repairedRounds?: number;
        events?: Array<{
          tool?: string;
          reason?: string;
          resolvedOutlineId?: EntityId;
        }>;
      };
      subagentStage?: string;
      subagentStageName?: string;
      /** 为 true 时表示本 chunk 仅标记阶段开始（与交付物 chunk 区分） */
      subagentStageStarting?: boolean;
      subagentStageDone?: string;
      /** 主稿专家在各子阶段之间输出过渡说明时置 true，结束时 false */
      subagentBridging?: boolean;
      /** 进入最终主稿专家流式呈现时置 true */
      subagentMainPresenter?: boolean;
      subagentPayload?: unknown;
      subagentPayloadMeta?: { contentLength?: number; issueCount?: number };
      /** 写作专家：阶段摘要 Markdown，逐段追加 */
      subagentPipelineDigest?: string;
    }) => void,
  ) => () => void;
  // 设置
  getSettings: () => Promise<ApiResult<GeneralSettings>>;
  setSettings: (data: Partial<GeneralSettings>) => Promise<ApiResult<void>>;
}

export interface GeneralSettings {
  sync_outline_chapter: boolean;
  ai_system_prompt: string;
  /** 自定义 AI 模型配置列表，用于对话与模型选择 */
  ai_model_configs?: AiModelConfig[];
  ai_agent_mode?: "legacy" | "subagent";
}

/** 单条 AI 模型配置（可自定义，用于设置页与对话模型下拉） */
export interface AiModelConfig {
  id: string;
  /**
   * API 协议：openai 为 OpenAI 兼容 Chat Completions；anthropic 为 Anthropic Messages API（@anthropic-ai/sdk）。
   * 未设置时按 openai 处理。
   */
  apiProvider?: "openai" | "anthropic";
  /** 模型名称（如 API 模型 id 或正式名称） */
  name: string;
  /** 昵称，选模型时优先显示；为空则显示 name */
  nickname?: string;
  supportsThinking: boolean;
  thinkingOnly: boolean;
  /**
   * 为 true 时在设置中展示并采用下方 temperature，请求会携带 temperature。
   * 为 false 时不传 temperature，由大模型接口使用其默认采样行为。
   * 未设置：兼容旧数据，仍按 temperatureThinking / temperatureNonThinking 传参。
   */
  customizeTemperature?: boolean;
  /** 开启思考模式时使用的 temperature；未设置时默认 0.6 */
  temperatureThinking?: number;
  /** 关闭思考模式时使用的 temperature；未设置时默认 0.6 */
  temperatureNonThinking?: number;
  apiKey: string;
  baseUrl: string;
}
