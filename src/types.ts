export interface Chapter {
  id: number;
  outline_id: number;
  title: string;
  level: number;
  progress: string;
  sort: number;
  parent_id?: number | null;
}

export interface Book {
  id: number;
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
  book_id: number;
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
  id: number;
  title: string;
  type?: "global" | "chapter" | "other" | "volume";
  sort?: number;
  xmind_data?: string | null;
  file_path?: string | null;
  /** 应用内 Markdown 大纲正文，与 XMind 并行保存 */
  markdown_content?: string | null;
  book_id?: number | null;
  parent_outline_id?: number | null;
  writing_chapter_id?: number | null;
  create_time?: string;
}

export interface VolumeOutline extends Outline {
  chapters: Outline[];
}

export interface Article {
  id: number;
  chapter_id: number;
  content: string;
  update_time?: string;
}

export interface StoryBackgroundAttachment {
  id: number;
  book_id: number;
  name: string;
  stored_path: string;
  create_time?: string;
}

export interface AiSession {
  id: number;
  book_id?: number | null;
  chapter_id?: number | null;
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
  chapter_id: number;
  prompt: string;
  response: string;
  model?: string;
  thinking?: string;
  tool_call_segments?: string | null;
  thinking_blocks?: string | null;
  create_time?: string;
}

/** 长期记忆层级 */
export type MemoryLayer = '全局' | '大纲' | '人物' | '章节' | '伏笔';

export interface AiMemory {
  id: number | string;  // mem0 使用 UUID 字符串
  book_id: number;
  layer: MemoryLayer;
  content: string;
  chapter_id?: number | null;
  character_id?: number | null;
  create_time?: string;
}

export interface AiForeshadowing {
  id: number | string;  // mem0 使用 UUID 字符串
  book_id: number;
  chapter_id: number;
  content: string;
  type: string;
  expected_chapter_id?: number | null;
  status: '未回收' | '已回收';
  resolved_chapter_id?: number | null;
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
  importDatabase: () => Promise<ApiResult<void>>;
  // 书籍
  getBooks: () => Promise<ApiResult<Book[]>>;
  createBook: (data: {
    title: string;
    enableVolume?: boolean;
  }) => Promise<ApiResult<Book>>;
  deleteBook: (data: { bookId: number }) => Promise<ApiResult<void>>;
  renameBook: (data: {
    bookId: number;
    title: string;
  }) => Promise<ApiResult<void>>;
  // 人物
  getCharacters: (data: { bookId: number }) => Promise<ApiResult<Character[]>>;
  createCharacter: (data: {
    bookId: number;
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
    type?: "global" | "chapter" | "other" | "volume";
    xmind_data?: string;
    file_path?: string;
    book_id?: number | null;
    writing_chapter_id?: number | null;
    parent_outline_id?: number | null;
  }) => Promise<ApiResult<Outline>>;
  getVolumeOutlines: (
    bookId?: number | null,
  ) => Promise<ApiResult<VolumeOutline[]>>;
  getOutlineByWritingChapter: (
    writingChapterId: number,
  ) => Promise<ApiResult<Outline | null>>;
  getOutlines: (
    typeFilter?: "global" | "chapter",
  ) => Promise<ApiResult<Outline[]>>;
  getWritingOutline: (bookId?: number | null) => Promise<ApiResult<Outline>>;
  getGlobalOutline: (
    bookId?: number | null,
  ) => Promise<ApiResult<Outline | null>>;
  /** 若无总纲记录则创建空总纲（可只写 Markdown），用于与章节大纲一致的文本大纲入口 */
  ensureGlobalOutline: (
    bookId?: number | null,
  ) => Promise<ApiResult<Outline>>;
  getChapterOutlines: (bookId?: number | null) => Promise<ApiResult<Outline[]>>;
  getOtherOutlines: (bookId?: number | null) => Promise<ApiResult<Outline[]>>;
  deleteOutline: (data: { outlineId: number }) => Promise<ApiResult<void>>;
  updateOutline: (data: {
    outlineId: number;
    title?: string;
    xmind_data?: string;
    file_path?: string;
    markdown_content?: string | null;
  }) => Promise<ApiResult<Outline>>;
  // 章节
  saveChapters: (data: {
    outlineId: number;
    chapters: unknown[];
  }) => Promise<ApiResult<void>>;
  getChapters: (data: { outlineId: number }) => Promise<ApiResult<Chapter[]>>;
  addChapter: (data: {
    outlineId: number;
    title: string;
    parentId?: number | null;
  }) => Promise<ApiResult<Chapter>>;
  deleteChapter: (data: { id: number }) => Promise<ApiResult<void>>;
  renameChapter: (data: {
    id: number;
    title: string;
  }) => Promise<ApiResult<void>>;
  updateChapterProgress: (data: {
    id: number;
    progress: string;
  }) => Promise<ApiResult<void>>;
  // 文档
  saveArticle: (data: {
    chapterId: number;
    content: string;
  }) => Promise<ApiResult<void>>;
  getArticle: (data: {
    chapterId: number;
  }) => Promise<ApiResult<Article | null>>;
  getStoryBackground: (data: { bookId: number }) => Promise<ApiResult<{ book_id: number; content: string; update_time?: string } | null>>;
  saveStoryBackground: (data: { bookId: number; content: string }) => Promise<ApiResult<void>>;
  openAndReadTextFile: () => Promise<ApiResult<string>>;
  pickStoryBackgroundAttachments: (data: { bookId: number }) => Promise<ApiResult<StoryBackgroundAttachment[]>>;
  getStoryBackgroundAttachments: (data: { bookId: number }) => Promise<ApiResult<StoryBackgroundAttachment[]>>;
  deleteStoryBackgroundAttachment: (data: { id: number }) => Promise<ApiResult<void>>;
  openStoryBackgroundAttachment: (data: { storedPath: string }) => Promise<string>;
  // AI
  createSession: (data: { bookId: number; chapterId?: number | null }) => Promise<ApiResult<AiSession>>;
  getSessions: (data: { bookId: number; chapterId?: number | null; includeClosed?: boolean }) => Promise<ApiResult<AiSession[]>>;
  setSessionClosed: (data: { sessionId: number }) => Promise<ApiResult<void>>;
  setSessionReopened: (data: { sessionId: number }) => Promise<ApiResult<void>>;
  deleteSession: (data: { sessionId: number }) => Promise<ApiResult<void>>;
  saveConversation: (data: {
    sessionId: number;
    chapterId?: number | null;
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
  // 长期记忆（五层）
  addMemory: (data: { bookId: number; layer: MemoryLayer; content: string; chapterId?: number | null; characterId?: number | null }) => Promise<ApiResult<AiMemory>>;
  updateMemory: (data: { id: number | string; data: Partial<Pick<AiMemory, 'content' | 'chapter_id' | 'character_id'>> }) => Promise<ApiResult<AiMemory>>;
  deleteMemory: (data: { id: number | string }) => Promise<ApiResult<void>>;
  getMemoriesByBook: (data: { bookId: number; layer?: MemoryLayer }) => Promise<ApiResult<AiMemory[]>>;
  getMemoriesByIds: (data: { ids: (number | string)[] }) => Promise<ApiResult<AiMemory[]>>;
  getMemoriesForPrompt: (data: {
    bookId: number;
    query?: string;
    options?: { layers?: MemoryLayer[]; chapterId?: number; limitPerLayer?: number; limit?: number };
  }) => Promise<ApiResult<AiMemory[]>>;
  // 伏笔记忆
  addForeshadowing: (data: { bookId: number; chapterId: number; content: string; type?: string; expectedChapterId?: number | null }) => Promise<ApiResult<AiForeshadowing>>;
  updateForeshadowing: (data: { id: number | string; data: Partial<Pick<AiForeshadowing, 'content' | 'type' | 'expected_chapter_id' | 'status' | 'resolved_chapter_id'>> }) => Promise<ApiResult<AiForeshadowing>>;
  deleteForeshadowing: (data: { id: number | string }) => Promise<ApiResult<void>>;
  getForeshadowingByBook: (data: { bookId: number; status?: '未回收' | '已回收' }) => Promise<ApiResult<AiForeshadowing[]>>;
  getForeshadowingByIds: (data: { ids: (number | string)[] }) => Promise<ApiResult<AiForeshadowing[]>>;
  getForeshadowingForPrompt: (data: { bookId: number; query?: string; options?: { limit?: number; status?: '未回收' | '已回收' } }) => Promise<ApiResult<AiForeshadowing[]>>;
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
    };
    tools?: unknown[];
    useToolRouter?: boolean;
    bookId?: number | null;
    chapterId?: number | null;
    currentChapterTitle?: string;
    writingChapters?: { id: number; title: string }[];
    availableOutlines?: { id: number; title: string }[];
    agentMode?: "legacy" | "subagent";
    agentAction?: "analyze" | "plan" | "draft" | "review" | "polish" | "full";
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
      chapterContentUpdated?: number;
      /** 当前批次内第 index 个工具已执行完成（0-based），用于逐条更新 UI */
      toolIndexCompleted?: number;
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
          resolvedOutlineId?: number;
        }>;
      };
      subagentStage?: string;
      /** 与 subagentStageName 同时出现时表示某一 subagent 阶段已结束 */
      subagentStageName?: string;
      subagentStageDone?: string;
      subagentPayload?: unknown;
      subagentPayloadMeta?: { contentLength?: number; issueCount?: number };
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
