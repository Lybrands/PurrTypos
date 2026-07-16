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
  /** 标签，逗号分隔（卡片列表识别用，保持结构化） */
  tags: string;
  /** 人物档案 Markdown 全文（基本信息/外貌/性格/经历等，已取代旧表单字段） */
  profile_md?: string;
  /** @deprecated 旧表单字段，已迁移进 profile_md，仅历史数据读取 */
  gender?: string;
  /** @deprecated 同上 */
  age?: string;
  /** @deprecated 同上 */
  height?: string;
  /** @deprecated 同上 */
  occupation?: string;
  /** @deprecated 同上 */
  appearance?: string;
  /** @deprecated 同上 */
  origin?: string;
  /** @deprecated 同上 */
  personality?: string;
  /** @deprecated 同上 */
  background?: string;
  /** @deprecated 同上 */
  biography?: string;
  /** @deprecated 同上 */
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

/** 大纲历史来源标记 */
export type OutlineHistorySource =
  | "user"
  | "ai_tool"
  | `rollback_of:${number}`
  | string;

/** 历史列表项：长字段已截断为 preview，详情走 getOutlineHistory */
export interface OutlineHistoryListItem {
  id: number;
  outline_id: EntityId;
  before_title: string | null;
  before_type: string | null;
  markdown_preview: string | null;
  markdown_length: number;
  xmind_length: number;
  source: OutlineHistorySource;
  note: string | null;
  create_time: string;
}

export interface OutlineHistoryDetail {
  id: number;
  outline_id: EntityId;
  before_title: string | null;
  before_type: string | null;
  before_markdown_content: string | null;
  before_xmind_data: string | null;
  source: OutlineHistorySource;
  note: string | null;
  create_time: string;
}

export interface Article {
  id: number;
  chapter_id: EntityId;
  content: string;
  update_time?: string;
}

export interface BookStyle {
  book_id: EntityId;
  pov: string;
  tone: string;
  pace: string;
  banned_rules: string;
  /** JSON 字符串：number[] 或 string[]，参考章节 id */
  reference_chapter_ids: string;
  free_notes: string;
  update_time?: string;
}

export type SaveBookStylePayload = Omit<BookStyle, 'book_id' | 'update_time'> & { bookId: EntityId };

export interface ChapterDiffHistory {
  id: number;
  chapter_id: EntityId;
  before_text: string;
  after_text: string;
  source: string;
  accepted_segments: number;
  rejected_segments: number;
  create_time?: string;
}

/** 人物设定快照（diff 前后对比） */
export interface CharacterSettingSnapshot {
  name: string;
  tags: string;
  profileMd: string;
}

export interface CharacterSettingHistory {
  id: number;
  character_id: number;
  before_name: string;
  before_tags: string;
  before_profile_md: string;
  after_name: string;
  after_tags: string;
  after_profile_md: string;
  source: string;
  accepted_segments: number;
  rejected_segments: number;
  create_time?: string;
}

export interface StoryBackgroundSettingHistory {
  id: number;
  book_id: EntityId;
  before_content: string;
  after_content: string;
  source: string;
  accepted_segments: number;
  rejected_segments: number;
  create_time?: string;
}

/** 世界设定实体类型（地点 / 势力 / 物品 / 其他） */
export type SettingEntityType = "location" | "faction" | "item" | "other";

export interface SettingEntity {
  id: number;
  book_id: EntityId;
  entity_type: SettingEntityType;
  name: string;
  tags: string;
  profile_md: string;
  create_time?: string;
}

export interface SettingEntityHistory {
  id: number;
  entity_id: number;
  before_name: string;
  before_tags: string;
  before_profile_md: string;
  after_name: string;
  after_tags: string;
  after_profile_md: string;
  source: string;
  accepted_segments: number;
  rejected_segments: number;
  create_time?: string;
}

/** AI 工具提交的设定 diff 提议（人物 / 故事背景 / 世界设定实体） */
export interface ProposedSettingDiff {
  kind: "character" | "background" | "entity";
  bookId: EntityId;
  characterId?: number;
  characterName?: string;
  entityId?: number;
  entityType?: SettingEntityType;
  entityName?: string;
  before: CharacterSettingSnapshot | { content: string };
  proposed: CharacterSettingSnapshot | { content: string };
  source?: string;
}

/** 服务端暂停高风险 Agent 工具调用时发送的用户确认请求。 */
export type ToolApprovalStatus =
  | "pending"
  | "approved"
  | "rejected"
  | "timed_out"
  | "canceled"
  | "unavailable";

export interface ToolApprovalRequest {
  approvalId: string;
  toolName: string;
  title: string;
  riskLevel: "write" | "destructive";
  /** 后端生成的参数预览，供用户决定是否批准。 */
  summary: string;
  /** 服务端审批生命周期；缺省时按 pending 处理。 */
  status?: ToolApprovalStatus;
}

/** 设定 diff 卡片终态（提交 / 放弃后持久化到消息） */
export interface SettingDiffCardState {
  sessionKey: string;
  kind: "character" | "background" | "entity";
  title: string;
  status: "pending" | "committed" | "rejected";
  acceptedSegments?: number;
  rejectedSegments?: number;
}

export interface StoryBackgroundAttachment {
  id: number;
  book_id: EntityId;
  name: string;
  stored_path: string;
  create_time?: string;
}

// ─── 仪表盘（故事健康度 / 写作统计）───────────────────────────────

export interface ForeshadowHealthItem {
  id: number;
  content: string;
  type: string;
  chapterId: string | null;
  chapterTitle: string | null;
  chapterIndex: number | null;
  expectedChapterId: string | null;
  expectedChapterTitle: string | null;
  expectedChapterIndex: number | null;
  overdue: boolean;
  dueSoon: boolean;
  createTime?: string;
}

export interface CharacterAppearanceItem {
  id: number;
  name: string;
  tags: string;
  appearChapters: number;
  lastChapterIndex: number | null;
  lastChapterTitle: string | null;
  gapChapters: number | null;
}

export interface StoryHealthData {
  totalChapters: number;
  writtenChapters: number;
  totalWords: number;
  latestWrittenIndex: number;
  gapWarnThreshold: number;
  foreshadowing: {
    unresolved: ForeshadowHealthItem[];
    unresolvedCount: number;
    overdueCount: number;
    dueSoonCount: number;
    resolvedCount: number;
  };
  characters: CharacterAppearanceItem[];
}

export interface WritingStatsData {
  totalWords: number;
  todayWords: number;
  goalWords: number;
  streakDays: number;
  avgChapterWords: number;
  daily: Array<{ date: string; words: number }>;
  chapters: Array<{
    id: string;
    title: string;
    index: number;
    volumeTitle: string | null;
    words: number;
  }>;
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

export interface AiPromptTemplate {
  id: number;
  title: string;
  content: string;
  sort?: number;
  create_time?: string;
  update_time?: string;
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
  thinking_durations_ms?: string | null;
  duration_ms?: number | null;
  task_plan?: string | null;
  create_time?: string;
}

export interface AiTaskPlanChunk {
  title: string;
  goal?: string;
  status: 'planned' | 'running' | 'paused' | 'done' | 'blocked' | 'failed' | 'canceled';
  steps: {
    id: string;
    title: string;
    description?: string;
    type: 'read' | 'analyze' | 'write' | 'review' | 'confirm';
    status: 'pending' | 'running' | 'done' | 'blocked' | 'failed';
    executor?: 'model' | 'tool';
    riskLevel?: 'read' | 'write' | 'destructive';
    suggestedTools?: string[];
    resultSummary?: string;
    error?: string;
  }[];
}

/** 本书设定层级 */
export type SparkIdeaLayer = '全局' | '大纲' | '人物' | '章节' | '伏笔';

/** AI 对话模式（与 UI 模式选择一致） */
export const CHAT_AGENT_MODES = ['ask', 'agent'] as const;
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

export type MemoryKind =
  | 'canon'
  | 'plot'
  | 'character'
  | 'world'
  | 'foreshadowing'
  | 'style'
  | 'summary';

export type MemoryStatus = 'pending' | 'active' | 'archived' | 'superseded';
export type MemoryScopeType = 'book' | 'chapter' | 'character' | 'outline' | 'session';

export interface MemoryItem {
  id: number;
  book_id: EntityId;
  kind: MemoryKind;
  scope_type: MemoryScopeType;
  scope_id?: string | null;
  content: string;
  summary: string;
  keywords: string;
  importance: number;
  confidence: number;
  status: MemoryStatus;
  pinned: number;
  fingerprint: string;
  source_type: string;
  source_id?: string | null;
  create_time?: string;
  update_time?: string;
  last_used_at?: string | null;
  deduped?: boolean;
}

export interface MemoryContextDiagnostics {
  forced: number;
  recalled: number;
  included: number;
  deferred: number;
  suppressed: number;
  conflicts: number;
  relationExpanded: number;
  characterCount: number;
}

export interface MemoryContextBlock {
  text: string;
  includedIds: number[];
  deferredIds: number[];
  suppressedIds: number[];
  tokenEstimate: number;
  diagnostics: MemoryContextDiagnostics;
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
  /** 整本导出为单个 TXT：保存对话框 + 写盘 */
  writeSingleTextFile: (data: { defaultName: string; content: string }) => Promise<ApiResult<{ path: string }>>;
  /** EPUB 导出：后端生成，主进程保存对话框 + 写盘；chapterIds 缺省导出全书 */
  exportEpub: (data: { bookId: EntityId; chapterIds?: EntityId[] | null; defaultName?: string }) => Promise<ApiResult<{ path: string }>>;
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
  /** 对一个正在等待中的 Agent 高风险工具调用作出一次性决定。 */
  resolveAiToolApproval: (data: {
    approvalId: string;
    approved: boolean;
  }) => Promise<ApiResult<{ status: "approved" | "rejected" }>>;
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
  // 世界设定实体（地点 / 势力 / 物品 / 其他）
  getSettingEntities: (data: {
    bookId: EntityId;
    type?: SettingEntityType;
  }) => Promise<ApiResult<SettingEntity[]>>;
  createSettingEntity: (data: {
    bookId: EntityId;
    entityType: SettingEntityType;
    name: string;
    tags?: string;
    profileMd?: string;
  }) => Promise<ApiResult<SettingEntity>>;
  updateSettingEntity: (data: {
    id: number;
    data: {
      entityType?: SettingEntityType;
      name?: string;
      tags?: string;
      profileMd?: string;
    };
  }) => Promise<ApiResult<SettingEntity>>;
  deleteSettingEntity: (data: { id: number }) => Promise<ApiResult<void>>;
  // 仪表盘（故事健康度 / 写作统计）
  getStoryHealth: (data: {
    bookId: EntityId;
  }) => Promise<ApiResult<StoryHealthData>>;
  getWritingStats: (data: {
    bookId: EntityId;
  }) => Promise<ApiResult<WritingStatsData>>;
  setWritingGoal: (data: {
    bookId: EntityId;
    dailyWords: number;
  }) => Promise<ApiResult<void>>;
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
  /** 本章自身绑定的 outline（outlines.writing_chapter_id == id），
   *  与 OutlinePanel 显示的章/卷大纲一致。 */
  getOutlineForChapter: (
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
  listOutlineHistory: (data: {
    outlineId: EntityId;
    limit?: number;
  }) => Promise<ApiResult<OutlineHistoryListItem[]>>;
  getOutlineHistory: (data: {
    historyId: number;
  }) => Promise<ApiResult<OutlineHistoryDetail | null>>;
  restoreOutlineHistory: (data: {
    historyId: number;
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
    /** 写作大纲下创建卷节点等场景 */
    isVolume?: boolean;
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
    source?: string;
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
  // Book style
  getBookStyle: (data: { bookId: EntityId }) => Promise<ApiResult<BookStyle | null>>;
  saveBookStyle: (data: SaveBookStylePayload) => Promise<ApiResult<void>>;
  // Chapter diff history
  commitChapterDiff: (data: {
    chapterId: EntityId;
    content: string;
    beforeText?: string;
    afterText?: string;
    source?: string;
    acceptedSegments?: number;
    rejectedSegments?: number;
  }) => Promise<ApiResult<{ id: number } | null>>;
  listChapterDiff: (data: { chapterId: EntityId; limit?: number }) => Promise<ApiResult<ChapterDiffHistory[]>>;
  getChapterDiff: (data: { diffId: number }) => Promise<ApiResult<ChapterDiffHistory | null>>;
  rollbackChapterDiff: (data: { diffId: number }) => Promise<ApiResult<{ id: number; chapterId: EntityId } | null>>;
  // Setting diff history (character / story background)
  commitCharacterSettingDiff: (data: {
    characterId: number;
    name: string;
    tags: string;
    profileMd: string;
    before: CharacterSettingSnapshot;
    after: CharacterSettingSnapshot;
    source?: string;
    acceptedSegments?: number;
    rejectedSegments?: number;
  }) => Promise<ApiResult<{ id: number; characterId: number } | null>>;
  commitBackgroundSettingDiff: (data: {
    bookId: EntityId;
    content: string;
    beforeContent?: string;
    afterContent?: string;
    source?: string;
    acceptedSegments?: number;
    rejectedSegments?: number;
  }) => Promise<ApiResult<{ id: number; bookId: EntityId } | null>>;
  listCharacterSettingHistory: (data: {
    characterId: number;
    limit?: number;
  }) => Promise<ApiResult<CharacterSettingHistory[]>>;
  getCharacterSettingHistory: (data: {
    historyId: number;
  }) => Promise<ApiResult<CharacterSettingHistory | null>>;
  rollbackCharacterSettingHistory: (data: {
    historyId: number;
  }) => Promise<ApiResult<{ id: number; characterId: number } | null>>;
  listBackgroundSettingHistory: (data: {
    bookId: EntityId;
    limit?: number;
  }) => Promise<ApiResult<StoryBackgroundSettingHistory[]>>;
  getBackgroundSettingHistory: (data: {
    historyId: number;
  }) => Promise<ApiResult<StoryBackgroundSettingHistory | null>>;
  rollbackBackgroundSettingHistory: (data: {
    historyId: number;
  }) => Promise<ApiResult<{ id: number; bookId: EntityId } | null>>;
  commitEntitySettingDiff: (data: {
    entityId: number;
    name: string;
    tags: string;
    profileMd: string;
    before: CharacterSettingSnapshot;
    after: CharacterSettingSnapshot;
    source?: string;
    acceptedSegments?: number;
    rejectedSegments?: number;
  }) => Promise<ApiResult<{ id: number; entityId: number } | null>>;
  listEntitySettingHistory: (data: {
    entityId: number;
    limit?: number;
  }) => Promise<ApiResult<SettingEntityHistory[]>>;
  getEntitySettingHistory: (data: {
    historyId: number;
  }) => Promise<ApiResult<SettingEntityHistory | null>>;
  rollbackEntitySettingHistory: (data: {
    historyId: number;
  }) => Promise<ApiResult<{ id: number; entityId: number } | null>>;
  // AI
  createSession: (data: { bookId: EntityId; chapterId?: EntityId | null; scope?: "setting" }) => Promise<ApiResult<AiSession>>;
  getSessions: (data: { bookId: EntityId; chapterId?: EntityId | null; includeClosed?: boolean; scope?: "setting" }) => Promise<ApiResult<AiSession[]>>;
  setSessionClosed: (data: { sessionId: number }) => Promise<ApiResult<void>>;
  setSessionReopened: (data: { sessionId: number }) => Promise<ApiResult<void>>;
  deleteSession: (data: { sessionId: number }) => Promise<ApiResult<void>>;
  saveConversation: (data: {
    sessionId: number;
    bookId?: EntityId | null;
    chapterId?: EntityId | null;
    prompt: string;
    response: string;
    model?: string;
    thinking?: string;
    toolCallSegments?: {
      textBefore: string;
      labels: string[];
      completedToolCount?: number;
    }[];
    thinkingBlocks?: string[];
    thinkingDurationsMs?: number[];
    durationMs?: number;
    taskPlan?: AiTaskPlanChunk;
    agentRunId?: string;
  }) => Promise<ApiResult<{ id: number | null }>>;
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
  // 提示词模版
  listPromptTemplates: () => Promise<ApiResult<AiPromptTemplate[]>>;
  createPromptTemplate: (data: {
    title: string;
    content: string;
    sort?: number;
  }) => Promise<ApiResult<AiPromptTemplate>>;
  updatePromptTemplate: (data: {
    id: number;
    data: { title?: string; content?: string; sort?: number };
  }) => Promise<ApiResult<AiPromptTemplate>>;
  deletePromptTemplate: (data: { id: number }) => Promise<ApiResult<void>>;
  reorderPromptTemplates: (data: { ids: number[] }) => Promise<ApiResult<void>>;
  // 本书设定（五层）
  addSparkIdea: (data: { bookId: EntityId; layer: SparkIdeaLayer; content: string; chapterId?: EntityId | null; characterId?: number | null }) => Promise<ApiResult<AiSparkIdea>>;
  updateSparkIdea: (data: { id: number | string; data: Partial<Pick<AiSparkIdea, 'content' | 'layer' | 'chapter_id' | 'character_id'>> }) => Promise<ApiResult<AiSparkIdea>>;
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
  // 长期记忆
  createMemory: (data: {
    bookId: EntityId;
    kind: MemoryKind;
    content: string;
    scopeType?: MemoryScopeType;
    scopeId?: string | null;
    summary?: string;
    keywords?: string;
    importance?: number;
    confidence?: number;
    status?: MemoryStatus;
    pinned?: boolean;
    sourceType?: string;
    sourceId?: string | null;
  }) => Promise<ApiResult<MemoryItem>>;
  updateMemory: (data: { id: number | string; data: Partial<MemoryItem> }) => Promise<ApiResult<MemoryItem>>;
  archiveMemory: (data: { id: number | string }) => Promise<ApiResult<MemoryItem>>;
  searchMemories: (data: {
    bookId: EntityId;
    query?: string;
    options?: {
      statuses?: MemoryStatus[];
      kinds?: MemoryKind[];
      scopeType?: MemoryScopeType;
      scopeId?: string;
      limit?: number;
    };
  }) => Promise<ApiResult<MemoryItem[]>>;
  getMemoriesByIds: (data: { ids: (number | string)[] }) => Promise<ApiResult<MemoryItem[]>>;
  linkMemories: (data: {
    bookId: EntityId;
    fromMemoryId: number;
    toMemoryId: number;
    relation: 'supersedes' | 'contradicts' | 'supports' | 'relates_to';
    note?: string;
  }) => Promise<ApiResult<unknown>>;
  buildMemoryContext: (data: {
    bookId: EntityId;
    userPrompt?: string;
    mode?: string;
    selectedLongTermMemoryIds?: (number | string)[];
    selectedMemoryIds?: (number | string)[];
    selectedForeshadowingIds?: (number | string)[];
    memoryBudget?: number;
    memoryRecallLimit?: number;
    contextWindow?: AiContextWindow;
  }) => Promise<ApiResult<MemoryContextBlock>>;
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
    sessionId?: number;
    messages: Array<{ role: string; content: string; tool_calls?: unknown[]; reasoning_content?: string } | { role: "tool"; tool_call_id: string; content: string }>;
    options?: {
      model?: string;
      temperature?: number;
      max_tokens?: number;
      thinking?: { type: "disabled" | "enabled" };
      context_window?: AiContextWindow;
    };
    /** 是否允许三层 Writing Agent 暴露当前书籍范围内的工具。 */
    enableAgentTools?: boolean;
    bookId?: EntityId | null;
    chapterId?: EntityId | null;
    currentChapterTitle?: string;
    writingChapters?: { id: EntityId; title: string }[];
    availableOutlines?: { id: EntityId; title: string; type?: string }[];
    /** 与界面「关联章节」一致，后端预取内容直接注入 system */
    associatedChapterIds?: EntityId[];
    associatedOutlineIds?: EntityId[];
    /** AiContextBar 勾选的设定/伏笔 id，后端前置 fetch 后注入 system */
    selectedMemoryIds?: (number | string)[];
    selectedForeshadowingIds?: (number | string)[];
    chatAgentMode?: ChatAgentMode;
    contextWindow?: AiContextWindow;
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
      chapterCreated?: {
        chapterId: EntityId;
        title: string;
        parentId?: EntityId | null;
      };
      /** AI 写工具改动了设定类数据（人物/背景/大纲），前端面板据此刷新 */
      settingUpdated?: {
        kind: "character" | "background" | "outline";
        action?: string;
        id?: EntityId;
        name?: string;
      };
      /**
       * AI 工具 editChapterContent 提交的差异提议：
       * 后端不再直接 save_article，改为把 before/after 推给前端，
       * 由 DiffProvider 启动 diff 会话，等用户在 DiffOverlay 接受后才落库。
       */
      proposedChapterDiff?: {
        chapterId: EntityId;
        beforeText: string;
        proposedText: string;
        source?: string;
      };
      /** AI 工具 updateCharacter / editStoryBackground 提交的设定差异提议 */
      proposedSettingDiff?: ProposedSettingDiff;
      /** 高风险工具需要用户在当前 SSE 回合中批准或拒绝。 */
      toolApprovalRequired?: ToolApprovalRequest;
      /** 审批的服务端最终状态（包含超时与连接取消）。 */
      toolApprovalResolved?: {
        runId?: string;
        approvalId: string;
        toolName: string;
        status: Exclude<ToolApprovalStatus, "pending">;
      };
      /** Host-side accounting for the complete model context window. */
      contextBudget?: {
        windowTokens: number;
        estimatedInputTokens: number;
        toolSchemaTokens: number;
        outputReserveTokens: number;
        safetyReserveTokens: number;
        runtimeReserveTokens: number;
        memoryTokens: number;
        associatedTokens: number;
        droppedMessages: number;
        projectedTotalTokens: number;
        overflowTokens: number;
      };
      /** 当前批次内第 index 个工具已执行完成（0-based），用于逐条更新 UI */
      toolIndexCompleted?: number;
      /** 本次完成是否命中会话内只读缓存（不读库）；为 true 时前端可隐藏该行 */
      toolFromCache?: boolean;
      agentRunStarted?: {
        runId: string;
        status: string;
        title?: string;
        goal?: string | null;
      };
      agentRunTodosUpdated?: AiTaskPlanChunk & { runId: string };
      agentRunTodoUpdated?: {
        runId: string;
        stepId: string;
        step: AiTaskPlanChunk["steps"][number];
        status?: string;
      };
      agentRunCompleted?: { runId: string; status: "done" };
      agentRunFailed?: { runId: string; status: "failed"; error?: string };
      agentRunBlocked?: { runId: string; status: "blocked" };
      agentRunCanceled?: { runId: string; status: "canceled"; reason?: string };
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
  /** 开启后，AI 接受的改动会尝试用模型提炼待确认的长期记忆候选。 */
  memory_intelligence_enabled?: boolean;
  /** 可选：指定用于记忆提炼的模型配置 id；为空时使用第一个可用模型配置。 */
  memory_intelligence_model_id?: string;
}

export type AiContextWindow = '32k' | '64k' | '128k' | '200k' | '256k' | '300k' | '1m';

export type AiBuiltinProviderId = 'moonshot' | 'minimax' | 'mimo';

/** 单条 AI 模型配置（可自定义，用于设置页与对话模型下拉） */
export interface AiModelConfig {
  id: string;
  /** 来自内置目录时记录预设 id；旧配置与高级自定义配置不需要该字段。 */
  presetId?: string;
  /** 内置目录中的服务商 id，仅用于设置页展示与后续目录升级。 */
  providerId?: AiBuiltinProviderId;
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
  /** 当前模型是否默认以 thinking 模式请求。 */
  thinkingEnabled?: boolean;
  /** 当前模型上下文窗口，用于历史、记忆和关联上下文预算。 */
  contextWindow?: AiContextWindow;
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
