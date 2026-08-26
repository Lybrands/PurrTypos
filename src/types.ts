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
  creation_mode?: 'original' | 'continuation';
  continuation_source_title?: string | null;
  continuation_fork_section_title?: string | null;
  continuation_fork_ordinal?: number | null;
  continuation_source_revision_id?: string | null;
  continuation_canon_snapshot_id?: string | null;
}

export type ScreenplaySourceKind = 'book' | 'original';
export type ScreenplayFormat = '短片' | '电影' | '单集剧' | '连续剧' | '竖屏短剧';
export type ScreenplayStage =
  | 'orientation'
  | 'brief'
  | 'structure'
  | 'scenes'
  | 'draft'
  | 'review'
  | 'completed';
export type ScreenplayProjectStatus = 'active' | 'archived';
export type ScreenplaySourceScopeMode =
  | 'whole_book'
  | 'first_chapters'
  | 'first_volumes'
  | 'selected_chapters'
  | 'selected_volumes';
export interface ScreenplaySourceScopeRequest {
  mode: ScreenplaySourceScopeMode;
  count?: number;
  chapterIds?: EntityId[];
  volumeIds?: EntityId[];
}
export interface ScreenplaySourceScope {
  schemaVersion: 1;
  mode: ScreenplaySourceScopeMode;
  requestedCount: number | null;
  chapterIds: EntityId[];
  volumeIds: EntityId[];
  chapters: Array<{
    id: EntityId;
    title: string;
    index: number;
    volumeId: EntityId | null;
    volumeTitle: string | null;
  }>;
}
export type ScreenplayDocumentKind =
  | 'source_analysis'
  | 'creative_brief'
  | 'beat_sheet'
  | 'episode_outline'
  | 'scene_list'
  | 'scene_draft'
  | 'review';
export type ScreenplayDocumentStatus = 'draft' | 'accepted' | 'superseded';

export interface ScreenplayDeliveryManifest {
  schemaVersion: 1;
  projectSnapshot: {
    projectId: EntityId;
    title: string;
    sourceKind: ScreenplaySourceKind;
    sourceBookId: EntityId | null;
    sourceScope: ScreenplaySourceScope;
    format: ScreenplayFormat;
    approach: string;
    premise: string;
  };
  lineage: {
    sourceAnalysisId: EntityId | null;
    creativeBriefId: EntityId;
    structureId: EntityId;
    sceneListId: EntityId;
    finalDraftId: EntityId;
    finalReviewId: EntityId;
  };
  documents: Array<{
    role: string;
    documentId: EntityId;
    kind: ScreenplayDocumentKind;
    title: string;
    version: number;
    contentDigest: string;
    sourceRefCount: number;
  }>;
  qualityGate: {
    verdict: 'ready';
    openIssueCount: 0;
    verifiedPriorIssueCount: number;
    sceneCount: number;
    sceneExecutionCount: number;
  };
  sourceTrace: {
    totalSourceRefCount: number;
  };
  packageDigest: string;
  generatedAt: string;
}

export interface ScreenplayProject {
  id: EntityId;
  title: string;
  source_kind: ScreenplaySourceKind;
  source_book_id: EntityId | null;
  source_scope: ScreenplaySourceScope;
  format: ScreenplayFormat;
  approach: string;
  premise: string;
  delivery_manifest: ScreenplayDeliveryManifest | null;
  active_stage: ScreenplayStage;
  status: ScreenplayProjectStatus;
  revision?: number;
  create_time?: string;
  update_time?: string;
}

export interface ScreenplayDocument {
  id: EntityId;
  project_id: EntityId;
  kind: ScreenplayDocumentKind;
  title: string;
  content_json: Record<string, unknown>;
  content_text: string;
  version: number;
  status: ScreenplayDocumentStatus;
  derived_from_ids: EntityId[];
  create_time?: string;
  update_time?: string;
}

export interface ScreenplayDraftEpisode {
  id: EntityId;
  project_id: EntityId;
  draft_document_id: EntityId;
  scene_list_document_id: EntityId;
  episode_number: number;
  title: string;
  scene_ids: EntityId[];
  scene_count: number;
  scene_executions?: Array<Record<string, unknown>>;
  scene_texts?: Array<{ sceneId: EntityId; contentText: string }>;
  content_text?: string;
  continuity_excerpt: string;
  continuity_summary?: string;
  version: number;
  status: ScreenplayDocumentStatus;
  storage_mode: 'revision_part';
  create_time?: string;
  update_time?: string;
}

export interface ScreenplayDocumentEpisode {
  id: EntityId;
  project_id: EntityId;
  document_id: EntityId;
  document_kind: Extract<ScreenplayDocumentKind, 'episode_outline' | 'scene_list' | 'review'>;
  episode_number: number;
  title: string;
  item_ids: EntityId[];
  item_count: number;
  content_json?: Record<string, unknown>;
  content_text?: string;
  version: number;
  status: ScreenplayDocumentStatus;
  storage_mode: 'revision_part';
  create_time?: string;
  update_time?: string;
}

export interface ScreenplayDocumentProposal {
  kind: ScreenplayDocumentKind;
  title: string;
  contentJson: Record<string, unknown>;
  contentText: string;
  derivedFromIds: EntityId[];
  /** Run whose formal proposal event produced this exact payload. */
  sourceRunId?: string;
}

export interface ScreenplayRevisionRef {
  schemaVersion: 1;
  projectId: EntityId;
  taskId: string;
  revisionId: string;
  role: ScreenplayV2DeliverableRole;
  revisionNo: number;
  /** Live transport provenance; persisted conversations resolve by Revision id. */
  sourceRunId?: string;
}

export interface ScreenplaySourceRef {
  id: number;
  project_id: EntityId;
  document_id: EntityId | null;
  agent_run_id: string;
  tool_name: string;
  source_type:
    | 'book'
    | 'chapter'
    | 'outline'
    | 'character'
    | 'setting'
    | 'background';
  source_id: string;
  source_revision: string;
  excerpt: string;
  create_time?: string;
}

export type ScreenplayV2Format =
  | 'shortFilm'
  | 'featureFilm'
  | 'singleEpisode'
  | 'series'
  | 'verticalSeries';
export type ScreenplayV2DeliverableRole =
  | 'sourceAnalysis'
  | 'creativeBrief'
  | 'structure'
  | 'sceneList'
  | 'screenplayDraft'
  | 'review';
export interface ScreenplayStageCommand {
  kind: 'stage_action';
  action: 'create' | 'revise' | 'review';
  targetRole: ScreenplayV2DeliverableRole;
  scope: {
    kind: 'current_stage' | 'next_episodes' | 'episodes' | 'all_remaining';
    count?: number;
    episodeNumbers?: number[];
  };
}
export interface ScreenplayV2RevisionSummary {
  id: string;
  deliverableId: string;
  role: ScreenplayV2DeliverableRole;
  revisionNo: number;
  parentRevisionId: string | null;
  contentDigest: string;
  summary: Record<string, unknown>;
  agentTaskId: string | null;
  rootRunId?: string | null;
  finalizingRunId?: string | null;
  applicability?: 'current' | 'stale';
  status?: 'current' | 'historical' | 'candidate';
  createdAt?: string | null;
}

export interface ScreenplayV2RevisionPart {
  type: 'document' | 'episode' | 'scene' | 'reviewIssueGroup';
  key: string;
  position: number;
  payload: Record<string, unknown>;
  contentText: string;
  contentDigest: string;
}

export interface ScreenplayV2RevisionDetail extends ScreenplayV2RevisionSummary {
  projectId: EntityId;
  schemaVersion: number;
  createdBy: 'agent' | 'user';
  inputRevisions: Partial<Record<ScreenplayV2DeliverableRole, string>>;
  parts: ScreenplayV2RevisionPart[];
  sources: Array<{
    type: string;
    id: string;
    revision: string;
    excerpt: string;
  }>;
}

export interface ScreenplayV2WorkingCopy {
  id: string;
  projectId: EntityId;
  deliverableId: string;
  role: ScreenplayV2DeliverableRole;
  baseRevisionId: string | null;
  revision: number;
  content: Record<string, unknown>;
  updatedAt?: string | null;
}

export interface ScreenplayV2Project {
  id: string;
  revision: number;
  title: string;
  format: ScreenplayV2Format;
  source: Record<string, unknown> & {
    type?: ScreenplaySourceKind;
    bookId?: string | null;
    scope?: Record<string, unknown>;
  };
  brief: {
    approach: string;
    premise: string;
  };
  lifecycle: 'active' | 'archived';
  stage: ScreenplayStage;
  createdAt?: string | null;
  updatedAt?: string | null;
}

export type ScreenplayV2ReviewFindingStatus =
  | 'pending'
  | 'planned'
  | 'resolved'
  | 'dismissed'
  | 'riskAccepted';
export type ScreenplayV2ReviewPhase =
  | 'awaitingReview'
  | 'adjudicating'
  | 'readyToRevise'
  | 'readyToFinalize'
  | 'completed';
export interface ScreenplayV2ReviewFinding {
  id: string;
  severity: 'critical' | 'major' | 'minor';
  description: string;
  sceneIds: string[];
  status: ScreenplayV2ReviewFindingStatus;
  note: string;
  actor?: string | null;
  decidedAt?: string | null;
}
export interface ScreenplayV2ReviewState {
  phase: ScreenplayV2ReviewPhase;
  draftRevisionId: string | null;
  reviewRevisionId: string | null;
  recommendation: 'ready' | 'revise' | 'major_rework' | null;
  findings: ScreenplayV2ReviewFinding[];
  counts: {
    total: number;
    pending: number;
    planned: number;
    resolved: number;
    dismissed: number;
    riskAccepted: number;
  };
  hardChecks: Array<{ code: string; message: string }>;
  canFinalize: boolean;
  completionSource: 'user' | 'legacyAgentVerdict' | null;
  nextAction: Record<string, string> | null;
}

export interface ScreenplayV2Workspace {
  project: ScreenplayV2Project;
  workflow: {
    stage: ScreenplayStage;
    heads: Partial<Record<ScreenplayV2DeliverableRole, ScreenplayV2RevisionSummary | null>>;
    nextActions: Array<Record<string, unknown>>;
    review: ScreenplayV2ReviewState;
  };
  deliverables: Array<{
    id: string;
    role: ScreenplayV2DeliverableRole;
    headRevisionId: string | null;
  }>;
  candidates: ScreenplayV2RevisionSummary[];
  workingCopies: ScreenplayV2WorkingCopy[];
}

export type ScreenplayConversationTurnStatus =
  | 'queued'
  | 'planning'
  | 'running'
  | 'paused'
  | 'completed'
  | 'failed'
  | 'canceled';

export interface ScreenplayConversationTurn {
  id: string;
  projectId: EntityId;
  sessionId: number;
  status: ScreenplayConversationTurnStatus;
  userContent: string;
  stageCommand: ScreenplayStageCommand | null;
  assistantContent: string;
  runtimeProfile: {
    apiProvider?: string;
    model?: string;
    modelProfile?: string | null;
    endpointDigest?: string;
    locale?: string;
    contextWindow?: string | null;
  };
  intent: Record<string, unknown> | null;
  rootRunId: string | null;
  taskId: string | null;
  error: { code?: string; message?: string } | null;
  createdAt?: string | null;
  updatedAt?: string | null;
}

export type ScreenplayAgentTaskStatus =
  | 'pending'
  | 'queued'
  | 'running'
  | 'paused'
  | 'completed'
  | 'failed'
  | 'canceled';

export interface ScreenplayAgentTaskUnit {
  id: string;
  semanticKey: string;
  position: number;
  kind: string;
  status:
    | 'pending'
    | 'claimed'
    | 'running'
    | 'waiting_retry'
    | 'blocked'
    | 'needs_split'
    | 'expanded'
    | 'completed'
    | 'failed'
    | 'canceled';
  input: Record<string, unknown>;
  outputRef: string | null;
  artifactDigest: string | null;
  validationReceipt: Record<string, unknown>;
  error: { code?: string; message?: string } | null;
  attempt: number;
}

export interface ScreenplayAgentTask {
  id: string;
  projectId: EntityId;
  sessionId: number;
  turnId: string;
  status: ScreenplayAgentTaskStatus;
  targetRole: ScreenplayV2DeliverableRole;
  intent: Record<string, unknown>;
  rootRunId: string | null;
  totalUnits: number;
  completedUnits: number;
  usage: ScreenplayOperationUsage;
  resultRevisionId: string | null;
  resultRevision: ScreenplayV2RevisionSummary | null;
  error: { code?: string; message?: string } | null;
  units: ScreenplayAgentTaskUnit[];
  createdAt?: string | null;
  updatedAt?: string | null;
}

export type ScreenplayOperationStatus =
  | 'queued'
  | 'running'
  | 'paused'
  | 'succeeded'
  | 'failed'
  | 'canceled';

export interface ScreenplayOperationProjection {
  id: string;
  turnId: string;
  taskId: string | null;
  status: ScreenplayOperationStatus;
  revision: number;
  targetRole: ScreenplayV2DeliverableRole;
  parts: ScreenplayAgentTaskUnit[];
  resultRevisionId: string | null;
  finalizationReceiptId: string | null;
  cancelReceiptId: string | null;
  cancelRequestedAt: string | null;
  error: { code?: string; message?: string } | null;
  usage: ScreenplayOperationUsage;
  resultRevision?: ScreenplayV2RevisionSummary | null;
  createdAt?: string | null;
  updatedAt?: string | null;
}

export interface ScreenplayOperationUsage {
  invocationCount: number;
  inputTokens: number;
  outputTokens: number;
  reasoningTokens: number | null;
}

export interface ScreenplayResumeOperationReceipt {
  operationId: string;
  turnId: string;
  status: ScreenplayOperationStatus;
  revision: number;
  capabilitySnapshotDigest: string;
}

export interface ScreenplayCancelOperationReceipt {
  id: string;
  cancelReceiptId: string;
  operationId: string | null;
  turnId: string;
  requestedAt: string;
  terminalStatus: 'cancel_requested' | 'succeeded' | 'failed' | 'canceled';
}

export interface ScreenplayConversationSnapshot {
  projectId: EntityId;
  sessionId: number;
  turns: ScreenplayConversationTurn[];
  tasks: ScreenplayAgentTask[];
  operations: ScreenplayOperationProjection[];
  cursor: number;
}

export interface ScreenplayAgentChunkEvent {
  cursor: number;
  turnId: string;
  taskId: string | null;
  runId: string | null;
  runRole: 'root' | 'unit' | 'final_response' | 'related';
  userContent: string;
  model: string | null;
  turnCreatedAt?: string | null;
  chunk: Record<string, unknown>;
  createdAt?: string | null;
}

export interface ScreenplayAgentChunkPage {
  kind: 'agent_chunks';
  chunks: ScreenplayAgentChunkEvent[];
  nextCursor: number;
  hasMore: boolean;
}

export type ScreenplayConversationStreamEvent = ScreenplayAgentChunkPage;

export interface ScreenplayConversationRuntimeInput {
  apiKey: string;
  baseURL?: string;
  apiProvider?: AiApiProvider;
  locale?: string;
  options: {
    model: string;
    model_profile?: string;
    temperature?: number;
    max_tokens?: number;
    thinking?: { type: 'disabled' | 'enabled' };
    context_window?: AiContextWindow;
  };
  contextWindow?: AiContextWindow;
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

export type WritingMethodType = 'primary' | 'technique';

export interface WritingMethodOverrides {
  forceRevisionIds: string[];
  excludeRevisionIds: string[];
}

export interface NovelSourcePickedFile {
  fileName: string;
  extension: '.txt' | '.md' | '.markdown';
  byteCount: number;
  content: string;
}

export interface NovelSourceSection {
  id: string;
  revision_id: string;
  ordinal: number;
  title: string;
  content_digest: string;
  locator: Record<string, unknown>;
  text_content?: string;
}

export interface NovelSourceRevision {
  id: string;
  work_id: string;
  version_no: number;
  content_digest: string;
  parser_version: number;
  byte_count: number;
  character_count: number;
  source_metadata: Record<string, unknown>;
  sections?: NovelSourceSection[];
  create_time: string;
}

export interface NovelSourceWork {
  id: string;
  title: string;
  source_type: 'external_text' | 'frozen_book';
  origin_book_id: EntityId | null;
  status: 'active' | 'archived';
  metadata: Record<string, unknown>;
  revision_count?: number;
  latest_revision_id?: string | null;
  revisions?: NovelSourceRevision[];
  create_time: string;
  update_time: string;
}

export interface NovelSourceImportPreview {
  fileName: string;
  extension: '.txt' | '.md' | '.markdown';
  byteCount: number;
  characterCount: number;
  contentDigest: string;
  suggestedTitle: string;
  parserVersion: number;
  sectionCount: number;
  requiresSingleSectionConfirmation: boolean;
  estimatedAdditionalStorageBytes: number;
  rightsNotice: string;
  modelDataBoundaryNotice: string;
  sections: Array<{
    ordinal: number;
    title: string;
    characterCount: number;
    preview: string;
  }>;
}

export interface NovelAnalysisEvidence {
  sectionId: string;
  excerpt: string;
  sectionOrdinal?: number;
  locator?: { start: number; end: number };
  excerptDigest?: string;
}

export interface NovelAnalysisFact {
  id?: string;
  factKind: string;
  subjectKey: string;
  predicate: string;
  value: unknown;
  lifecycleStatus: string;
  evidence: NovelAnalysisEvidence[];
}

export interface NovelAnalysisCraftCard {
  id?: string;
  cardKind: string;
  title: string;
  bodyMarkdown: string;
  evidence: NovelAnalysisEvidence[];
}

export interface NovelAnalysisArtifact {
  artifactId: string;
  artifactKind: string;
  sourceRevisionId: string;
  sectionIds: string[];
  facts: NovelAnalysisFact[];
  craftCards: NovelAnalysisCraftCard[];
  coverage: Record<string, unknown>;
  conflicts: unknown[];
  reviewStatus: 'pending' | 'reviewed';
}

export interface NovelAnalysisRun {
  runId: string;
  runStatus: string;
  commandId: string;
  taskId: string | null;
  taskStatus: string | null;
  taskRevision: number | null;
  totalUnits: number;
  completedUnits: number;
  failedUnits: number;
  error?: string | null;
  artifactRef?: string | null;
}

export interface PublishedNovelAnalysis {
  id: string;
  sourceRevisionId: string;
  versionNo: number;
  coverageEndOrdinal: number;
  schemaVersion: number;
  contentDigest: string;
  facts: NovelAnalysisFact[];
  craftCards: NovelAnalysisCraftCard[];
  summary: Record<string, unknown>;
  createTime: string;
}

export interface ContinuationCanonPreview {
  sourceRevisionId: string;
  sourceAnalysisId: string;
  forkSectionId: string;
  forkOrdinal: number;
  sourceWorkId: string;
  sourceTitle: string;
  sourceVersionNo: number;
  forkSectionTitle: string;
  snapshotDigest: string;
  records: Array<{
    sourceFactId: string;
    factKind: string;
    subjectKey: string;
    predicate: string;
    value: unknown;
    contentDigest: string;
  }>;
}

export interface ContinuationWorkspace {
  book: Book;
  binding: {
    id: string;
    sourceWorkId: string;
    sourceRevisionId: string;
    sourceAnalysisId: string;
    sourceTitle: string;
    forkSectionId: string;
    forkSectionTitle: string;
    forkOrdinal: number;
    canonSnapshotId: string;
    canonSnapshotDigest: string;
    bindingDigest: string;
  };
  canonRecords: ContinuationCanonPreview['records'];
}

export interface WritingMethodRevision {
  id: string;
  method_id: string;
  version_no: number;
  name: string;
  description: string;
  method_type: WritingMethodType;
  tags: string[];
  markdown_body: string;
  metadata: Record<string, unknown>;
  content_digest: string;
  published_at: string;
}

export interface WritingMethod {
  id: string;
  name: string;
  description: string;
  method_type: WritingMethodType;
  tags: string[];
  source_type: string;
  source_ref: Record<string, unknown>;
  is_builtin: number;
  status: 'active' | 'archived' | 'disabled';
  draft_markdown: string;
  draft_metadata: Record<string, unknown>;
  draft_revision: number;
  current_published_revision_id: string | null;
  revisions?: WritingMethodRevision[];
  create_time: string;
  update_time: string;
}

export interface WritingSchemeRevisionMember {
  ordinal: number;
  method_revision_id: string;
  method_id: string;
  name: string;
  method_type: WritingMethodType;
  version_no: number;
  content_digest: string;
}

export interface WritingSchemeRevision {
  id: string;
  scheme_id: string;
  version_no: number;
  name: string;
  description: string;
  members_digest: string;
  members: WritingSchemeRevisionMember[];
  published_at: string;
}

export interface WritingScheme {
  id: string;
  name: string;
  description: string;
  draft_member_revision_ids: string[];
  draft_revision: number;
  source_type: string;
  source_ref: Record<string, unknown>;
  is_builtin: number;
  status: 'active' | 'archived' | 'disabled';
  current_published_revision_id: string | null;
  revisions?: WritingSchemeRevision[];
  create_time: string;
  update_time: string;
}

export interface WritingMethodCandidateBatch {
  analysisId: string;
  scheme: WritingScheme;
  methods: WritingMethod[];
  bindingChanged: false;
}

export interface BookWritingMethodBinding {
  id: string;
  book_id: EntityId;
  binding_type: 'method' | 'scheme';
  method_revision_id: string | null;
  scheme_revision_id: string | null;
  priority: number;
  source: string;
  revision: WritingMethodRevision | WritingSchemeRevision;
}

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

export interface StoryMemoryAnalysisReceipt {
  book_id: string;
  chapter_id: string;
  source_revision: string;
  status: 'skipped' | 'running' | 'completed' | 'reused' | 'failed';
  candidate_count: number;
  delta_id?: string | null;
  reason: string;
  model: string;
}

export interface StoryMemoryEvolutionFieldChange {
  field: string;
  before: unknown;
  after: unknown;
}

export interface StoryMemoryEvolutionDecision {
  delta_id: string;
  target_key: string;
  kind: 'character_state' | 'relationship_state' | 'world_fact' | 'timeline_event' | 'plot_thread';
  classification: 'addition' | 'update' | 'duplicate' | 'conflict' | 'supersession';
  recommendation: 'apply' | 'reject' | 'review';
  risk: 'low' | 'medium' | 'high';
  rationale: string;
  field_changes: StoryMemoryEvolutionFieldChange[];
  candidate_payload: Record<string, unknown>;
  source_excerpt: string;
  related_memory_key?: string | null;
  existing_record_id?: string | null;
  existing_version?: number | null;
  candidate_confidence: number;
  review_status: 'open' | 'resolved' | 'stale';
  resolution: 'pending' | 'accepted' | 'rejected' | 'reverted';
  resolved_delta_id?: string | null;
  resolution_actor?: string | null;
  resolved_at?: string | null;
}

export interface StoryMemoryEvolutionReview {
  delta_id: string;
  book_id: string;
  chapter_id: string;
  status: 'open' | 'resolved' | 'stale';
  decisions: StoryMemoryEvolutionDecision[];
  summary: Record<StoryMemoryEvolutionDecision['classification'], number>;
}

export interface StoryMemoryEvolutionResolutionReceipt {
  original_delta_id: string;
  applied_delta_id?: string | null;
  accepted_keys: string[];
  rejected_keys: string[];
  status: 'applied' | 'rejected';
  actor: string;
}

export interface StoryMemoryVersionView {
  record_id: string;
  book_id: string;
  memory_key: string;
  version: number;
  delta_id: string;
  action: string;
  payload: Record<string, unknown>;
  status: 'confirmed' | 'inferred' | 'disputed' | 'deprecated';
  lifecycle: 'active' | 'reverted';
  provenance_status: 'valid' | 'stale';
  source_id?: string | null;
  create_time?: string | null;
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
  /** Durable occurrence identity; entity sessionKey remains only an editor route. */
  proposalId?: string;
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
  /** Product owner needed to durably resolve this journal occurrence. */
  resolutionTarget?: {
    sessionId: number;
    agentRunId: string;
    prompt: string;
  };
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
  proposalId: string;
  sessionKey: string;
  kind: "character" | "background" | "entity";
  title: string;
  status: "pending" | "committed" | "rejected";
  acceptedSegments?: number;
  rejectedSegments?: number;
}

export interface SettingDiffResolutionCommand extends SettingDiffCardState {
  sessionId: number;
  agentRunId: string;
  status: "committed";
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
  screenplay_project_id?: EntityId | null;
  scope?: 'chapter' | 'setting' | 'screenplay';
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
  commentary?: string;
  tool_call_segments?: string | null;
  commentary_blocks?: string | null;
  commentary_durations_ms?: string | null;
  duration_ms?: number | null;
  task_plan?: string | null;
  context_compaction?: string | null;
  context_budget?: string | null;
  agent_process?: string | null;
  agent_run_id?: string | null;
  long_task_id?: string | null;
  client_turn_id?: string | null;
  create_time?: string;
}

export interface AiContextCompactionState {
  status: "running" | "completed" | "failed";
  outcome?: string;
  selectedTurnCount?: number;
  compactedTurnCount?: number;
  retainedRawTurnCount?: number;
  previousSummaryVersion?: number | null;
  summaryVersion?: number | null;
}

export interface AiOutputBudgetState {
  policyKey: string;
  workUnits: number;
  targetTokens: number;
  requestedTokens: number;
  effectiveTokens: number;
  reasoningReserveTokens: number;
  thinkingEnabled: boolean;
  taskHardCapTokens: number;
  modelMaxOutputTokens?: number | null;
  contextMaxOutputTokens: number;
  limitingFactor: 'task_estimate' | 'task_hard_cap' | 'model_capability' | 'context_available';
  executionMode: 'single' | 'chunked';
  lengthStrategy: 'fail' | 'continue' | 'retry_larger';
}

export interface AiContextBudgetState {
  windowTokens: number;
  /** 发起本轮请求时所用的本地模型配置，防止同窗口模型互相复用用量。 */
  modelConfigId?: string;
  /** 发给供应商的模型名称，用于兼容没有 modelConfigId 的历史记录。 */
  modelName?: string;
  estimatedInputTokens: number;
  toolSchemaTokens: number;
  outputReserveTokens: number;
  safetyReserveTokens: number;
  runtimeReserveTokens: number;
  droppedMessages: number;
  projectedTotalTokens: number;
  overflowTokens: number;
  memoryTokens?: number;
  associatedTokens?: number;
  actualInputTokens?: number;
  actualOutputTokens?: number;
  actualTotalTokens?: number;
  cachedInputTokens?: number;
  reasoningOutputTokens?: number;
  actualUsageRound?: number;
  inputTokenEstimateAtUsage?: number;
  usageSource?: "provider";
  requestedOutputTokens?: number;
  finishReason?: string;
  outputBudget?: AiOutputBudgetState;
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
    executor?: 'model' | 'tool' | 'agent';
    riskLevel?: 'read' | 'write' | 'destructive';
    suggestedTools?: string[];
    assignment?: Record<string, unknown>;
    dependsOn?: string[];
    resultSummary?: string;
    error?: string;
  }[];
}

export interface AiAgentRunEventEnvelope {
  version: 1;
  cursor: number;
  type: string;
  runId: string;
  payload: Record<string, unknown>;
  /** Canonical public chunk produced by the same mapper as live SSE. */
  chunk?: Record<string, unknown>;
  createdAt?: string | null;
}

export interface AiAgentRunProductEvent {
  version: 1;
  type: "writing.proposed_setting_diff";
  runId: string;
  proposalId: string;
  toolCallId?: string;
  effectIndex: number;
  payload: ProposedSettingDiff & { proposalId: string };
  chunk: {
    runId: string;
    proposedSettingDiff: ProposedSettingDiff & { proposalId: string };
  };
  createdAt?: string | null;
}

export interface AiAgentRunSnapshot {
  version: 1;
  run: {
    runId: string;
    sessionId?: number | null;
    conversationId?: number | null;
    status: 'running' | 'done' | 'blocked' | 'failed' | 'canceled';
    mode?: string | null;
    finalResponse: string;
    createdAt?: string | null;
    updatedAt?: string | null;
    execution: {
      attempt: number;
      leaseExpiresAtMs?: number | null;
      heartbeatAtMs?: number | null;
      cancellationRequested: boolean;
    };
    provenance: {
      modelProvider?: string | null;
      modelName?: string | null;
      contextWindow?: number | null;
      endpointDigest?: string | null;
      requestProfileDigest?: string | null;
    };
  };
  todos: AiTaskPlanChunk['steps'];
  events: AiAgentRunEventEnvelope[];
  /** Product-owned projection; deliberately separate from canonical Core events. */
  productEvents?: AiAgentRunProductEvent[];
  delegations: {
    items: AiAgentDelegation[];
    aggregate: AiAgentDelegationAggregate;
  };
  nextCursor: number;
  hasMore: boolean;
}

export interface AiWritingChatRequestReceipt {
  requestId: string
  sessionId: number
  status: 'accepted' | 'starting' | 'run_bound' | 'rejected' | 'canceled'
  runId: string | null
  cancelRequested: boolean
  rejectionCode: string | null
  revision: number
}

export type AiLongTaskStatus =
  | 'pending'
  | 'running'
  | 'paused'
  | 'completed'
  | 'failed'
  | 'canceled';

export type AiLongTaskUnitStatus =
  | 'pending'
  | 'claimed'
  | 'running'
  | 'completed'
  | 'failed'
  | 'canceled';

export interface AiAgentDelegation {
  delegationId: string;
  runId: string;
  agentName: string;
  agentTitle?: string | null;
  objective: string;
  /** Planner step identity bound to a durable unit for this delegation. */
  unitId?: string | null;
  /** One-based execution attempt for the same durable unit. */
  attempt?: number | null;
  status: 'queued' | 'claimed' | 'running' | 'done' | 'failed' | 'canceled';
  required: boolean;
  priority: number;
  resultSummary?: string | null;
  error?: string | null;
}

export interface AiAgentDelegationAggregate {
  state: 'pending' | 'ready' | 'blocked';
  counts: Record<string, number>;
  requiredFailures: string[];
  results: Array<{
    delegationId: string;
    agentName: string;
    agentTitle?: string | null;
    summary: string;
  }>;
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

export type UnifiedMemorySource = 'semantic' | 'story_state' | 'story_candidate';
export type UnifiedMemoryStatus = 'active' | 'pending' | 'conflict' | 'stale' | 'archived' | 'rejected';

export interface UnifiedMemoryItem {
  id: string;
  book_id: EntityId;
  source: UnifiedMemorySource;
  kind: MemoryKind | StoryMemoryEvolutionDecision['kind'];
  status: UnifiedMemoryStatus;
  content: string;
  summary: string;
  structured_data: Record<string, unknown>;
  scope_type: string;
  scope_id?: string | null;
  chapter_id?: string | null;
  chapter_title?: string | null;
  evidence_excerpt: string;
  confidence: number;
  version?: number | null;
  pinned: boolean;
  source_type: string;
  source_id?: string | null;
  memory_key?: string | null;
  delta_id?: string | null;
  target_key?: string | null;
  classification?: StoryMemoryEvolutionDecision['classification'] | null;
  recommendation?: StoryMemoryEvolutionDecision['recommendation'] | null;
  risk?: StoryMemoryEvolutionDecision['risk'] | null;
  resolution?: StoryMemoryEvolutionDecision['resolution'] | null;
  actions: Array<'activate' | 'edit' | 'pin' | 'archive' | 'accept' | 'reject' | 'view_history'>;
  create_time?: string | null;
  update_time?: string | null;
}

export interface UnifiedMemoryPage {
  items: UnifiedMemoryItem[];
  total: number;
  suppressedDuplicates: number;
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
  httpStatus?: number;
}

export type AiErrorReportStatus = "captured" | "submitted" | "resolved";

export interface AiErrorReport {
  id: string;
  streamId: string;
  agentRunId?: string;
  sessionId?: number;
  conversationId?: number;
  bookId?: EntityId;
  chapterId?: EntityId;
  source: string;
  status: AiErrorReportStatus;
  errorCode?: string;
  errorMessage: string;
  model?: string;
  diagnostics: Record<string, unknown>;
  userNote?: string;
  submittedAt?: string;
  resolvedAt?: string;
  createTime: string;
  updateTime: string;
}

export interface AiAgentRunStabilityReport {
  verdict: "pass" | "warn" | "fail";
  metrics: {
    toolCalls: number;
    completedToolCalls: number;
    failedToolCalls: number;
    incompleteToolCalls: number;
    toolSuccessRate?: number | null;
    toolProtocolFailures: number;
    toolErrorCodes: Record<string, number>;
    modelAttempts: number;
    interruptedModelAttempts: number;
    retryAttempts: number;
    contextOverflows: number;
    maxDroppedMessages: number;
    compactionPasses: number;
    compactedTurns: number;
    compactionFallbacks: number;
    compactionFailures: number;
    compactionOutcomes: Record<string, number>;
  };
  checks: Array<{
    name: string;
    status: "pass" | "warn" | "fail";
    detail: unknown;
  }>;
}

export interface AiAgentRunArtifactMetrics {
  artifactCount: number;
  openArtifacts: number;
  finalizedArtifacts: number;
  abortedArtifacts: number;
  batchCount: number;
  committedItemCount: number;
  expectedItemCount: number;
  completionRate?: number | null;
  artifacts: Array<{
    artifactId: string;
    namespace: string;
    kind: string;
    status: string;
    ownerRef: {
      kind: string;
      id: string;
    };
    revision: number;
    committedItemCount: number;
    expectedItemCount?: number | null;
    batchCount: number;
    batchItemCount: number;
  }>;
}

export interface AiArtifactMaintenanceSnapshot {
  checkedAtMs: number;
  scopeRunId?: string | null;
  artifactCount: number;
  openArtifacts: number;
  finalizedArtifacts: number;
  abortedArtifacts: number;
  unknownArtifacts: number;
  claimCount: number;
  activeClaims: number;
  expiredClaims: number;
  unavailableRunClaims: number;
  invalidTargetClaims: number;
  reclaimableClaims: number;
  consistencyIssues: number;
  requiresAttention: boolean;
}

export interface AiArtifactMaintenanceReport {
  expiredClaimsReleased: number;
  unavailableRunClaimsReleased: number;
  invalidTargetClaimsReleased: number;
  releasedClaims: number;
  purgedArtifacts: number;
  consistencyIssues: number;
  changed: boolean;
}

export interface AiArtifactMaintenanceResult {
  report: AiArtifactMaintenanceReport;
  snapshot: AiArtifactMaintenanceSnapshot;
}

export interface AiAgentRunStabilityTrendAlert {
  code: string;
  severity: "warn" | "fail";
  value: number | null;
  threshold: number;
}

export interface AiAgentRunFailureFinding {
  code: string;
  category: string;
  severity: "warn" | "fail";
  confidence: "high" | "medium" | "low";
  evidence: Record<string, unknown>;
  remediation: string;
}

export interface AiAgentRunFailureClassification {
  verdict: "pass" | "warn" | "fail";
  primaryFinding?: AiAgentRunFailureFinding | null;
  findings: AiAgentRunFailureFinding[];
  summary: {
    findingCount: number;
    categoryCounts: Record<string, number>;
  };
}

export interface AiAgentRunRecoveryDecision {
  round: number;
  cause: string;
  action: "retry_model" | "fallback_provider_mode" | "replan" | string;
  allowed: boolean;
  reasonCode: string;
  attempt: number;
  maxAttempts: number;
  remainingModelRounds: number;
  effectState: "not_started" | "committed" | "unknown" | string;
  mayRepeatSideEffect: boolean;
}

export interface AiAgentRunRecoveryReport {
  summary: {
    decisionCount: number;
    allowedCount: number;
    deniedCount: number;
    safetyProtectedCount: number;
    causes: Record<string, number>;
    allowedActions: Record<string, number>;
    deniedReasons: Record<string, number>;
  };
  decisions: AiAgentRunRecoveryDecision[];
}

export interface AiAgentRunStabilityRegressionGate {
  verdict: "pass" | "warn" | "fail" | "insufficient_data";
  candidateSampleSize: number;
  baselineSampleSize: number;
  minimumWindowSize: number;
  metricDeltas: Record<string, number | null>;
  newToolErrorCodes: string[];
  newCriticalSignals: string[];
  checks: Array<{
    name: string;
    status: "pass" | "warn" | "fail" | "insufficient_data" | "not_applicable";
    detail: Record<string, unknown>;
  }>;
  alerts: Array<{
    code: string;
    severity: "warn" | "fail";
    detail: Record<string, unknown>;
  }>;
}

export interface AiAgentRunStabilityTrendReport {
  verdict: "pass" | "warn" | "fail" | "insufficient_data";
  sampleSize: number;
  minimumSampleSize: number;
  windowLimit: number;
  comparisonWindowSize: number;
  scope: {
    type: "session" | "book" | "screenplay_project" | "global";
    id?: EntityId | null;
  };
  metrics: {
    failedRuns: number;
    runFailureRate: number | null;
    stabilityFailedRuns: number;
    stabilityFailureRate: number | null;
    toolRuns: number;
    toolProtocolFailureRuns: number;
    toolProtocolRunRate: number | null;
    incompleteToolRuns: number;
    incompleteToolRunRate: number | null;
    contextOverflowRuns: number;
    contextOverflowRunRate: number | null;
    compactionFailureRuns: number;
    compactionFailureRunRate: number | null;
    modelRuns: number;
    retryRuns: number;
    retryRunRate: number | null;
    currentFailureStreak: number;
    toolErrorCodes: Record<string, number>;
    topToolErrorCodes: Array<{ code: string; count: number }>;
  };
  checks: Array<{
    name: string;
    status: "pass" | "warn" | "fail" | "insufficient_data" | "not_applicable";
    value: number | null;
    numerator: number;
    denominator: number | null;
    warnAt: number;
    failAt: number;
  }>;
  alerts: AiAgentRunStabilityTrendAlert[];
  regressionGate: AiAgentRunStabilityRegressionGate;
  recentRuns: Array<{
    runId: string;
    runStatus: string;
    stabilityVerdict: "pass" | "warn" | "fail" | string;
    createTime?: string | null;
  }>;
}

export interface AiAgentRunDiagnostics {
  runId?: string;
  runStatus?: string;
  verdict: "pass" | "warn" | "fail";
  performance: Record<string, unknown>;
  stability: AiAgentRunStabilityReport;
  recovery: AiAgentRunRecoveryReport;
  failureClassification: AiAgentRunFailureClassification;
  artifacts: AiAgentRunArtifactMetrics;
  artifactMaintenance: AiArtifactMaintenanceSnapshot;
  [key: string]: unknown;
}

export interface ElectronAPI {
  openXmindFile: () => Promise<string | null>;
  parseXmind: (filePath: string) => Promise<ApiResult<XmindSheet[]>>;
  openFilePath: (filePath: string) => Promise<ApiResult<void>>;
  readFileBuffer: (filePath: string) => Promise<ApiResult<Uint8Array>>;
  writeExportFiles: (data: { entries: Array<{ path: string; content: string }>; exportAsZip: boolean }) => Promise<ApiResult<void>>;
  /** 整本导出为单个 TXT：保存对话框 + 写盘 */
  writeSingleTextFile: (data: { defaultName: string; content: string }) => Promise<ApiResult<{ path: string }>>;
  /** 将已接受整稿或最终交付清单写入本地文件。 */
  writeScreenplayFile: (data: {
    defaultName: string;
    content: string;
    format: 'fountain' | 'markdown' | 'txt' | 'json';
  }) => Promise<ApiResult<{ path: string }>>;
  /** 后端生成标准剧本排版 PDF，主进程负责保存。 */
  exportScreenplayPdf: (data: {
    projectId: EntityId;
    defaultName: string;
  }) => Promise<ApiResult<{ path: string }>>;
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
  // 剧本项目
  listScreenplayProjects: (data?: {
    includeArchived?: boolean;
  }) => Promise<ApiResult<ScreenplayV2Project[]>>;
  getOrCreateScreenplaySession: (data: {
    projectId: EntityId;
  }) => Promise<ApiResult<AiSession>>;
  listScreenplaySessions: (data: {
    projectId: EntityId;
    includeClosed?: boolean;
  }) => Promise<ApiResult<AiSession[]>>;
  createScreenplaySession: (data: {
    commandId: string;
    projectId: EntityId;
  }) => Promise<ApiResult<AiSession>>;
  submitScreenplayConversationTurn: (data: {
    commandId: string;
    projectId: EntityId;
    sessionId: number;
    content: string;
    stageCommand?: ScreenplayStageCommand;
    runtime: ScreenplayConversationRuntimeInput;
  }) => Promise<ApiResult<ScreenplayConversationTurn>>;
  getScreenplayConversationSnapshot: (data: {
    projectId: EntityId;
    sessionId: number;
  }) => Promise<ApiResult<ScreenplayConversationSnapshot>>;
  watchScreenplayConversationEvents: (data: {
    projectId: EntityId;
    sessionId: number;
    chunkAfter?: number;
    onEvent: (event: ScreenplayConversationStreamEvent) => void;
  }) => () => void;
  cancelScreenplayConversationTurn: (data: {
    commandId: string;
    turnId: string;
  }) => Promise<ApiResult<ScreenplayCancelOperationReceipt>>;
  resumeScreenplayConversationOperation: (data: {
    commandId: string;
    operationId: string;
    expectedOperationRevision: number;
    runtime: ScreenplayConversationRuntimeInput;
  }) => Promise<ApiResult<ScreenplayResumeOperationReceipt>>;
  truncateScreenplayConversationFromTurn: (data: {
    turnId: string;
  }) => Promise<ApiResult<{
    projectId: EntityId;
    sessionId: number;
    deletedTurnIds: string[];
    deletedTaskIds: string[];
    deletedOperationIds?: string[];
  }>>;
  createScreenplayV2Project: (data: {
    commandId: string;
    title: string;
    format: ScreenplayV2Format;
    source: {
      type: ScreenplaySourceKind;
      bookId?: EntityId;
      scope?: {
        mode: 'wholeBook' | 'firstChapters' | 'firstVolumes' | 'selectedChapters' | 'selectedVolumes';
        count?: number;
        chapterIds?: EntityId[];
        volumeIds?: EntityId[];
      };
    };
    brief: { approach: string; premise: string };
  }) => Promise<ApiResult<ScreenplayV2Workspace>>;
  getScreenplayV2Workspace: (data: {
    projectId: EntityId;
  }) => Promise<ApiResult<ScreenplayV2Workspace>>;
  getScreenplayV2Revision: (data: {
    revisionId: string;
    view?: 'summary' | 'full';
  }) => Promise<ApiResult<ScreenplayV2RevisionDetail>>;
  listScreenplayV2RevisionHistory: (data: {
    projectId: EntityId;
    role: ScreenplayV2DeliverableRole;
    cursor?: string;
    limit?: number;
  }) => Promise<ApiResult<{
    projectId: EntityId;
    deliverableId: string;
    role: ScreenplayV2DeliverableRole;
    items: ScreenplayV2RevisionSummary[];
    nextCursor: string | null;
  }>>;
  getScreenplayV2LatestReviewForDraft: (data: {
    projectId: EntityId;
    draftRevisionId: string;
  }) => Promise<ApiResult<ScreenplayV2RevisionDetail | null>>;
  createScreenplayV2WorkingCopyFromRevision: (data: {
    commandId: string;
    projectId: EntityId;
    revisionId: string;
    expectedProjectRevision: number;
    expectedWorkingCopyRevision?: number;
  }) => Promise<ApiResult<ScreenplayV2WorkingCopy>>;
  updateScreenplayV2WorkingCopy: (data: {
    workingCopyId: string;
    expectedRevision: number;
    content: Record<string, unknown>;
  }) => Promise<ApiResult<ScreenplayV2WorkingCopy>>;
  publishScreenplayV2WorkingCopy: (data: {
    commandId: string;
    workingCopyId: string;
    expectedProjectRevision: number;
    expectedWorkingCopyRevision: number;
  }) => Promise<ApiResult<{
    revision: ScreenplayV2RevisionSummary;
    workspace: ScreenplayV2Workspace;
  }>>;
  acceptScreenplayV2Revision: (data: {
    commandId: string;
    projectId: EntityId;
    revisionId: string;
    expectedProjectRevision: number;
    confirmInvalidation?: boolean;
  }) => Promise<ApiResult<{
    acceptedRevisionId: string;
    projectRevision: number;
    invalidatedHeads: Array<{ role: ScreenplayV2DeliverableRole; revisionId: string }>;
    workspace: ScreenplayV2Workspace;
  }>>;
  adjudicateScreenplayV2Review: (data: {
    commandId: string;
    projectId: EntityId;
    expectedProjectRevision: number;
    reviewRevisionId: string;
    decisions: Array<{
      issueId: string;
      status: ScreenplayV2ReviewFindingStatus;
      note?: string;
    }>;
  }) => Promise<ApiResult<ScreenplayV2Workspace>>;
  finalizeScreenplayV2Project: (data: {
    commandId: string;
    projectId: EntityId;
    expectedProjectRevision: number;
    draftRevisionId: string;
    reviewRevisionId: string;
  }) => Promise<ApiResult<ScreenplayV2Workspace>>;
  updateScreenplayV2Project: (data: {
    commandId: string;
    projectId: EntityId;
    expectedProjectRevision: number;
    title: string;
  }) => Promise<ApiResult<ScreenplayV2Workspace>>;
  archiveScreenplayV2Project: (data: {
    commandId: string;
    projectId: EntityId;
    expectedProjectRevision: number;
  }) => Promise<ApiResult<ScreenplayV2Workspace>>;
  restoreScreenplayV2Project: (data: {
    commandId: string;
    projectId: EntityId;
    expectedProjectRevision: number;
  }) => Promise<ApiResult<ScreenplayV2Workspace>>;
  deleteScreenplayV2Project: (data: {
    commandId: string;
    projectId: EntityId;
    expectedProjectRevision: number;
  }) => Promise<ApiResult<{ projectId: EntityId; deleted: true }>>;
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
  analyzeChapterStoryMemory: (data: {
    bookId: EntityId;
    chapterId: EntityId;
    modelId?: string;
  }) => Promise<ApiResult<StoryMemoryAnalysisReceipt>>;
  reviewStoryMemoryDelta: (data: {
    deltaId: string;
  }) => Promise<ApiResult<StoryMemoryEvolutionReview>>;
  getStoryMemoryEvolutionReview: (data: {
    deltaId: string;
  }) => Promise<ApiResult<StoryMemoryEvolutionReview>>;
  getStoryMemoryVersions: (data: {
    bookId: EntityId;
    memoryKey: string;
  }) => Promise<ApiResult<StoryMemoryVersionView[]>>;
  listStoryMemoryEvolutionReviews: (data: {
    bookId: EntityId;
    statuses?: Array<'open' | 'resolved' | 'stale'>;
  }) => Promise<ApiResult<StoryMemoryEvolutionReview[]>>;
  resolveStoryMemoryEvolutionReview: (data: {
    deltaId: string;
    resolutions: Record<string, 'accepted' | 'rejected'>;
  }) => Promise<ApiResult<StoryMemoryEvolutionResolutionReceipt>>;
  getStoryBackground: (data: { bookId: EntityId }) => Promise<ApiResult<{ book_id: EntityId; content: string; update_time?: string } | null>>;
  saveStoryBackground: (data: { bookId: EntityId; content: string }) => Promise<ApiResult<void>>;
  openAndReadTextFile: () => Promise<ApiResult<string>>;
  pickNovelSourceTextFile: () => Promise<ApiResult<NovelSourcePickedFile>>;
  pickStoryBackgroundAttachments: (data: { bookId: EntityId }) => Promise<ApiResult<StoryBackgroundAttachment[]>>;
  getStoryBackgroundAttachments: (data: { bookId: EntityId }) => Promise<ApiResult<StoryBackgroundAttachment[]>>;
  deleteStoryBackgroundAttachment: (data: { id: number }) => Promise<ApiResult<void>>;
  openStoryBackgroundAttachment: (data: { storedPath: string }) => Promise<string>;
  listWritingMethods: (data?: { includeArchived?: boolean }) => Promise<ApiResult<WritingMethod[]>>;
  getWritingMethod: (data: { methodId: string }) => Promise<ApiResult<WritingMethod>>;
  createWritingMethod: (data: { name: string; description?: string; methodType: WritingMethodType; tags?: string[]; markdown?: string; metadata?: Record<string, unknown> }) => Promise<ApiResult<WritingMethod>>;
  updateWritingMethodDraft: (data: { methodId: string; expectedDraftRevision: number; name: string; description?: string; methodType: WritingMethodType; tags?: string[]; markdown?: string; metadata?: Record<string, unknown> }) => Promise<ApiResult<WritingMethod>>;
  publishWritingMethod: (data: { methodId: string }) => Promise<ApiResult<WritingMethodRevision>>;
  copyWritingMethod: (data: { methodId: string }) => Promise<ApiResult<WritingMethod>>;
  setWritingMethodStatus: (data: { methodId: string; status: 'active' | 'archived' }) => Promise<ApiResult<WritingMethod>>;
  deleteWritingMethod: (data: { methodId: string }) => Promise<ApiResult<void>>;
  listWritingSchemes: (data?: { includeArchived?: boolean }) => Promise<ApiResult<WritingScheme[]>>;
  getWritingScheme: (data: { schemeId: string }) => Promise<ApiResult<WritingScheme>>;
  createWritingScheme: (data: { name: string; description?: string; memberRevisionIds?: string[] }) => Promise<ApiResult<WritingScheme>>;
  updateWritingSchemeDraft: (data: { schemeId: string; expectedDraftRevision: number; name: string; description?: string; memberRevisionIds: string[] }) => Promise<ApiResult<WritingScheme>>;
  publishWritingScheme: (data: { schemeId: string }) => Promise<ApiResult<WritingSchemeRevision>>;
  copyWritingScheme: (data: { schemeId: string }) => Promise<ApiResult<WritingScheme>>;
  setWritingSchemeStatus: (data: { schemeId: string; status: 'active' | 'archived' }) => Promise<ApiResult<WritingScheme>>;
  deleteWritingScheme: (data: { schemeId: string }) => Promise<ApiResult<void>>;
  listBookWritingMethodBindings: (data: { bookId: EntityId }) => Promise<ApiResult<BookWritingMethodBinding[]>>;
  bindBookWritingMethod: (data: { bookId: EntityId; bindingType: 'method' | 'scheme'; revisionId: string }) => Promise<ApiResult<BookWritingMethodBinding>>;
  reorderBookWritingMethodBindings: (data: { bookId: EntityId; bindingIds: string[] }) => Promise<ApiResult<BookWritingMethodBinding[]>>;
  upgradeBookWritingMethodBinding: (data: { bookId: EntityId; bindingId: string; revisionId: string }) => Promise<ApiResult<BookWritingMethodBinding>>;
  unbindBookWritingMethod: (data: { bookId: EntityId; bindingId: string }) => Promise<ApiResult<void>>;
  createWritingMethodCandidates: (data: { analysisId: string; craftCardIds?: string[] }) => Promise<ApiResult<WritingMethodCandidateBatch>>;
  getWritingMethodCandidateBatch: (data: { schemeId: string }) => Promise<ApiResult<WritingMethodCandidateBatch>>;
  publishWritingMethodCandidateBatch: (data: { schemeId: string; methodIds: string[] }) => Promise<ApiResult<{ methodRevisions: WritingMethodRevision[]; schemeRevision: WritingSchemeRevision; bindingChanged: false }>>;
  previewNovelSourceImport: (data: NovelSourcePickedFile) => Promise<ApiResult<NovelSourceImportPreview>>;
  confirmNovelSourceImport: (data: NovelSourcePickedFile & {
    title: string;
    workId?: string;
    expectedContentDigest: string;
    confirmSingleSection: boolean;
    rightsConfirmed: boolean;
    modelDataBoundaryConfirmed: boolean;
  }) => Promise<ApiResult<NovelSourceRevision>>;
  freezeBookAsNovelSource: (data: { bookId: EntityId }) => Promise<ApiResult<NovelSourceRevision>>;
  listNovelSources: (data?: { includeArchived?: boolean }) => Promise<ApiResult<NovelSourceWork[]>>;
  getNovelSource: (data: { workId: string }) => Promise<ApiResult<NovelSourceWork>>;
  archiveNovelSource: (data: { workId: string }) => Promise<ApiResult<NovelSourceWork>>;
  getNovelSourceRevision: (data: { revisionId: string }) => Promise<ApiResult<NovelSourceRevision>>;
  deleteNovelSourceRevision: (data: { revisionId: string }) => Promise<ApiResult<void>>;
  getNovelSourceSection: (data: { revisionId: string; sectionId: string }) => Promise<ApiResult<NovelSourceSection>>;
  searchNovelSourceSections: (data: { revisionId: string; query: string; limit?: number }) => Promise<ApiResult<Array<{ id: string; ordinal: number; title: string; excerpt: string }>>>;
  startNovelAnalysis: (data: { commandId: string; revisionId: string; runtime: ScreenplayConversationRuntimeInput }) => Promise<ApiResult<{ status: string; commandId: string; sectionCount: number }>>;
  listNovelAnalysisRuns: (data: { revisionId: string }) => Promise<ApiResult<NovelAnalysisRun[]>>;
  pauseNovelAnalysis: (data: { taskId: string; expectedTaskRevision?: number }) => Promise<ApiResult<NovelAnalysisRun>>;
  resumeNovelAnalysis: (data: { commandId: string; taskId: string; retryFailed: boolean; runtime: ScreenplayConversationRuntimeInput }) => Promise<ApiResult<{ status: string; taskId: string; commandId: string }>>;
  cancelNovelAnalysis: (data: { taskId: string }) => Promise<ApiResult<NovelAnalysisRun>>;
  getNovelAnalysisArtifact: (data: { artifactId: string }) => Promise<ApiResult<NovelAnalysisArtifact>>;
  reviewNovelAnalysisArtifact: (data: { commandId: string; artifactId: string; facts: NovelAnalysisFact[]; craftCards: NovelAnalysisCraftCard[] }) => Promise<ApiResult<NovelAnalysisArtifact>>;
  publishNovelAnalysisArtifact: (data: { artifactId: string }) => Promise<ApiResult<PublishedNovelAnalysis>>;
  listPublishedNovelAnalyses: (data: { revisionId: string }) => Promise<ApiResult<PublishedNovelAnalysis[]>>;
  previewContinuationCanon: (data: { sourceRevisionId: string; sourceAnalysisId: string; forkSectionId: string }) => Promise<ApiResult<ContinuationCanonPreview>>;
  createContinuation: (data: { title: string; sourceRevisionId: string; sourceAnalysisId: string; forkSectionId: string; expectedSnapshotDigest: string; enableVolume?: boolean; writingMethodBindings?: Array<{ bindingType: 'method' | 'scheme'; revisionId: string }> }) => Promise<ApiResult<ContinuationWorkspace>>;
  getContinuation: (data: { bookId: string }) => Promise<ApiResult<ContinuationWorkspace>>;
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
    resolution?: SettingDiffResolutionCommand;
  }) => Promise<ApiResult<{ id: number; characterId: number } | null>>;
  commitBackgroundSettingDiff: (data: {
    bookId: EntityId;
    content: string;
    beforeContent?: string;
    afterContent?: string;
    source?: string;
    acceptedSegments?: number;
    rejectedSegments?: number;
    resolution?: SettingDiffResolutionCommand;
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
    resolution?: SettingDiffResolutionCommand;
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
    commentary?: string;
    toolCallSegments?: {
      labels: string[];
      commentaryBlockIndex: number | null;
      completedToolCount?: number;
    }[];
    commentaryBlocks?: string[];
    commentaryDurationsMs?: number[];
    durationMs?: number;
    taskPlan?: AiTaskPlanChunk;
    contextCompaction?: AiContextCompactionState;
    contextBudget?: AiContextBudgetState;
    agentProcess?: Record<string, unknown>;
    agentRunId?: string;
    clientTurnId?: string;
    expectedConversationIds?: number[];
  }) => Promise<ApiResult<{ id: number | null }>>;
  getConversations: (data: {
    sessionId: number;
  }) => Promise<ApiResult<Conversation[]>>;
  deleteConversationsAfterTurn: (data: {
    sessionId: number;
    keepTurnCount: number;
    expectedConversationIds?: number[];
    retireConversationIds?: number[];
    retireRunIds?: string[];
    expectedRunIds?: string[];
    retireClientTurnIds?: string[];
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
  listUnifiedMemories: (data: {
    bookId: EntityId;
    query?: string;
    statuses?: UnifiedMemoryStatus[];
    kinds?: UnifiedMemoryItem['kind'][];
    sources?: UnifiedMemorySource[];
    limit?: number;
  }) => Promise<ApiResult<UnifiedMemoryPage>>;
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
    apiProvider?: AiApiProvider;
    model?: string;
  }) => Promise<ApiResult<string>>;
  listModels: (data: {
    apiKey: string;
    baseURL?: string;
    apiProvider?: AiApiProvider;
  }) => Promise<ApiResult<string[]>>;
  getAgentRunSnapshot: (data: {
    runId: string;
    after?: number;
    limit?: number;
  }) => Promise<ApiResult<AiAgentRunSnapshot>>;
  getAgentRunDiagnostics: (data: {
    runId: string;
  }) => Promise<ApiResult<AiAgentRunDiagnostics>>;
  maintainAgentArtifacts: () => Promise<
    ApiResult<AiArtifactMaintenanceResult>
  >;
  getAgentRunStabilityTrend: (data: {
    runId: string;
    scope?: "auto" | "session" | "book" | "screenplay_project" | "global";
    limit?: number;
  }) => Promise<ApiResult<AiAgentRunStabilityTrendReport>>;
  getLatestSessionAgentRun: (data: {
    sessionId: number;
  }) => Promise<ApiResult<{
    request?: AiWritingChatRequestReceipt;
    prompt: string;
    snapshot: AiAgentRunSnapshot | null;
  } | null>>;
  captureAiErrorReport: (data: {
    streamId: string;
    agentRunId?: string;
    sessionId?: number;
    conversationId?: number;
    bookId?: EntityId | null;
    chapterId?: EntityId | null;
    source?: string;
    errorCode?: string;
    errorMessage: string;
    model?: string;
    diagnostics?: Record<string, unknown>;
  }) => Promise<ApiResult<AiErrorReport>>;
  listAiErrorReports: (data?: {
    status?: AiErrorReportStatus;
    limit?: number;
  }) => Promise<ApiResult<AiErrorReport[]>>;
  getAiErrorReport: (data: {
    reportId: string;
  }) => Promise<ApiResult<AiErrorReport>>;
  submitAiErrorReport: (data: {
    reportId: string;
    userNote?: string;
  }) => Promise<ApiResult<AiErrorReport>>;
  cancelAgentRun: (data: {
    runId: string;
  }) => Promise<ApiResult<{
    status: 'cancel_requested' | 'canceled';
    newlyRequested: boolean;
    childrenCanceled: number;
    terminalized: boolean;
  }>>;
  cancelWritingChatRequest: (data: {
    requestId: string;
  }) => Promise<ApiResult<AiWritingChatRequestReceipt | null>>;
  aiChatStream: (data: {
    /** Renderer-generated identifier used to isolate concurrent streams. */
    streamId?: string;
    /** Durable Writing request-receipt protocol version. */
    requestReceiptVersion?: 1;
    apiKey: string;
    baseURL?: string;
    locale?: string;
    /** 默认 openai：OpenAI 兼容 provider；anthropic 使用官方 Messages API */
    apiProvider?: AiApiProvider;
    sessionId?: number;
    messages: Array<{ role: string; content: string; tool_calls?: unknown[]; reasoning_content?: string } | { role: "tool"; tool_call_id: string; content: string }>;
    options?: {
      model?: string;
      /** 内置模型 profile id；高级自定义为空并走通用协议适配。 */
      model_profile?: string;
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
    /** 与界面「关联章节」一致，后端预取内容直接注入 system */
    associatedChapterIds?: EntityId[];
    associatedOutlineIds?: EntityId[];
    /** AiContextBar 勾选的设定/伏笔 id，后端前置 fetch 后注入 system */
    selectedMemoryIds?: (number | string)[];
    selectedForeshadowingIds?: (number | string)[];
    writingMethodOverrides?: WritingMethodOverrides;
    chatAgentMode?: ChatAgentMode;
    contextWindow?: AiContextWindow;
    /** Immutable durable history frontier for request reservation/claim. */
    expectedConversationIds?: number[];
    expectedRunIds?: string[];
  }) => string;
  abortAiStream: (streamId?: string) => void;
  onAiChunk: (
    callback: (chunk: {
      streamId?: string;
      /** Raw canonical PurrA output event. Transport adds only streamId. */
      eventId?: string;
      outputStreamId?: string | null;
      runId?: string;
      turnId?: string | null;
      invocationId?: string | null;
      sequence?: number;
      source?: "provider" | "runtime" | "tool" | "domain";
      kind?: string;
      channel?: "commentary" | "final" | "operation" | "lifecycle" | "error" | "diagnostic" | "delegation";
      visibility?: "public" | "private" | "diagnostic";
      payload?: Record<string, unknown>;
      occurredAt?: string;
      emittedAt?: string;
      /** Transport terminal metadata; never Assistant-authored content. */
      runResult?: {
        runId: string;
        status: "done" | "failed" | "blocked" | "canceled" | string;
        errorCode?: string | null;
      };
      /** Product request receipt; distinct from an Agent Run terminal. */
      requestReceipt?: AiWritingChatRequestReceipt;
      /** Authoritative pre-Run request settlement. */
      requestResult?: AiWritingChatRequestReceipt;
      delta?: string;
      done?: boolean;
      aborted?: boolean;
      /** False when execution settled without a product-level final answer. */
      finalResponseExpected?: boolean;
      /** Authoritative completed Run text supplied during snapshot recovery. */
      finalResponse?: string;
      error?: string;
      /** 本地自动创建的脱敏错误报告。 */
      errorReport?: AiErrorReport;
      model?: string;
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
      longTaskDispatched?: {
        runId: string;
        taskId: string;
        kind?: string;
        taskTitle?: string;
        message?: string;
        projectId?: EntityId;
        status: AiLongTaskStatus;
        totalUnits: number;
        completedUnits: number;
        estimatedScenes?: number;
      };
      longTaskProgress?: {
        runId: string;
        taskId: string;
        status: AiLongTaskStatus;
        revision: number;
        totalUnits: number;
        completedUnits: number;
        failedUnits: number;
        updateTime?: string | null;
        units: Array<{
          id: string;
          position: number;
          plannerStepId?: string;
          kind?: string;
          title?: string;
          status: AiLongTaskUnitStatus;
          attempt: number;
          maxAttempts: number;
          runId?: string | null;
          outputRef?: string | null;
          errorCode?: string | null;
          updateTime?: string | null;
        }>;
      };
    }) => void,
    streamId?: string,
  ) => () => void;
  debugLog: (payload: unknown) => void;
  // 设置
  getSettings: () => Promise<ApiResult<GeneralSettings>>;
  setSettings: (data: Partial<GeneralSettings>) => Promise<ApiResult<void>>;
}

export interface GeneralSettings {
  sync_outline_chapter: boolean;
  /** 自定义 AI 模型配置列表，用于对话与模型选择 */
  ai_model_configs?: AiModelConfig[];
  /** 开启后，AI 接受的改动会尝试用模型提炼待确认的长期记忆候选。 */
  memory_intelligence_enabled?: boolean;
  /** 可选：指定用于记忆提炼的模型配置 id；为空时使用第一个可用模型配置。 */
  memory_intelligence_model_id?: string;
  /** 用户确认 AI 正文改动后，自动生成结构化 Story Memory 候选。 */
  story_memory_analysis_enabled?: boolean;
  /** 可选：指定用于 Story Memory 章节分析的模型配置 id。 */
  story_memory_analysis_model_id?: string;
  /** 仅对满足低风险白名单的 Story Memory 候选启用自动应用。 */
  story_memory_auto_apply_enabled?: boolean;
  /** 自动应用候选的最低模型置信度，默认 0.95。 */
  story_memory_auto_apply_min_confidence?: number;
  /** 自动应用允许的 Story Memory 类型白名单。 */
  story_memory_auto_apply_kinds?: Array<StoryMemoryEvolutionDecision['kind']>;
}

export type AiContextWindow = '32k' | '64k' | '128k' | '200k' | '256k' | '300k' | '1m';

export type AiBuiltinProviderId = 'zai' | 'deepseek' | 'moonshot' | 'minimax' | 'mimo';
export type AiApiProvider = 'openai' | 'anthropic' | 'zai';

/** 单条 AI 模型配置（可自定义，用于设置页与对话模型下拉） */
export interface AiModelConfig {
  id: string;
  /** 来自内置目录时记录预设 id；旧配置与高级自定义配置不需要该字段。 */
  presetId?: string;
  /** 内置目录中的服务商 id，仅用于设置页展示与后续目录升级。 */
  providerId?: AiBuiltinProviderId;
  /**
   * API 协议：openai 为 OpenAI 兼容 Chat Completions；anthropic 为 Anthropic Messages API；zai 为官方 Z.ai SDK。
   * 未设置时按 openai 处理。
   */
  apiProvider?: AiApiProvider;
  /** 模型名称（如 API 模型 id 或正式名称） */
  name: string;
  /** 昵称，选模型时优先显示；为空则显示 name */
  nickname?: string;
  /** 模型能力：是否支持思考模式；不随本次启用/关闭而变化。 */
  supportsThinking: boolean;
  thinkingOnly: boolean;
  /** 当前模型是否以 thinking 模式请求，可在模型选择器中切换。 */
  thinkingEnabled?: boolean;
  /** 当前模型上下文窗口，用于历史、记忆和关联上下文预算。 */
  contextWindow?: AiContextWindow;
  /** @deprecated Agent 输出预算现由后端按任务解析；仅保留以读取旧配置。 */
  outputTokenBudget?: number;
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
