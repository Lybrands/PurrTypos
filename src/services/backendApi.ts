import type {
  AiErrorReport,
  ElectronAPI,
} from '../types'
import {
  apiDelete,
  apiGet,
  apiPatch,
  apiPost,
  apiPut,
  backendBaseUrl,
  requestJson,
} from './httpClient'
import {
  markAiDebugAbortRequested,
  recordAiDebugConversationSaved,
  recordAiDebugChunk,
  startAiDebugRun,
} from '../components/AiDevInspector/store'
import { isCanonicalOutputEvent } from '../agent-runtime/canonicalOutput'
import { presentAgentRunError } from '../agent-runtime/agentErrorPresentation'

export type PlatformApiKey =
  | 'openXmindFile'
  | 'parseXmind'
  | 'openFilePath'
  | 'readFileBuffer'
  | 'writeExportFiles'
  | 'writeSingleTextFile'
  | 'writeScreenplayFile'
  | 'exportScreenplayPdf'
  | 'exportEpub'
  | 'exportDatabase'
  | 'importDatabase'
  | 'openDatabaseDirectory'
  | 'openAndReadTextFile'
  | 'pickStoryBackgroundAttachments'
  | 'openStoryBackgroundAttachment'

export type BackendApi = Omit<ElectronAPI, PlatformApiKey>

type AiChunk = Parameters<ElectronAPI['onAiChunk']>[0] extends (chunk: infer T) => void ? T : never
type AiStreamRequest = Parameters<ElectronAPI['aiChatStream']>[0]

let aiChunkListeners: Array<(chunk: AiChunk) => void> = []
const aiAbortControllers = new Map<string, AbortController>()
let latestAiStreamId: string | null = null

const apiPostIdempotent = <T>(path: string, body: unknown, commandId: string) =>
  requestJson<T>(`/api${path}`, {
    method: 'POST',
    headers: { 'Idempotency-Key': commandId },
    body: JSON.stringify(body),
  })

const apiPatchIdempotent = <T>(path: string, body: unknown, commandId: string) =>
  requestJson<T>(`/api${path}`, {
    method: 'PATCH',
    headers: { 'Idempotency-Key': commandId },
    body: JSON.stringify(body),
  })

function aiErrorReportSource(streamId: string): string {
  if (streamId.startsWith('chat-')) return 'workspace_chat'
  if (streamId.startsWith('inline-edit-')) return 'inline_edit'
  if (streamId.startsWith('editor-float-')) return 'editor_rewrite'
  if (streamId.startsWith('ghost-completion-')) return 'ghost_completion'
  return 'ai_chat_stream'
}

function aiErrorReportDiagnostics(data: AiStreamRequest): Record<string, unknown> {
  return {
    provider: data.apiProvider || 'openai',
    agentMode: data.chatAgentMode || '',
    taskType: data.chatAgentMode === 'agent' ? '写作 Agent 任务' : '普通对话',
    toolsEnabled: data.enableAgentTools === true,
    thinkingEnabled: data.options?.thinking?.type === 'enabled',
    contextWindow: data.contextWindow || data.options?.context_window || '',
    messageCount: data.messages.length,
    associatedChapterCount: data.associatedChapterIds?.length || 0,
    associatedOutlineCount: data.associatedOutlineIds?.length || 0,
    selectedMemoryCount: data.selectedMemoryIds?.length || 0,
    selectedForeshadowingCount: data.selectedForeshadowingIds?.length || 0,
  }
}

export const backendApi: BackendApi = {
  getDatabaseInfo: () => apiGet('/database/info'),

  getBooks: () => apiGet('/books'),
  createBook: (data) => apiPost('/books', data),
  deleteBook: (data) => apiDelete(`/books/${data.bookId}`),
  renameBook: (data) => apiPut(`/books/${data.bookId}/rename`, { title: data.title }),
  getBookWordCount: (data) => apiGet(`/books/${data.bookId}/word-count`),

  listScreenplayProjects: (data = {}) =>
    apiGet(`/screenplay/v2/projects${data.includeArchived ? '?includeArchived=true' : ''}`),
  getOrCreateScreenplaySession: (data) =>
    apiPut(`/screenplay/v2/projects/${data.projectId}/agent-sessions/current`, {}),
  listScreenplaySessions: (data) =>
    apiGet(`/screenplay/v2/projects/${data.projectId}/agent-sessions${data.includeClosed ? '?includeClosed=true' : ''}`),
  createScreenplaySession: (data) => apiPostIdempotent(
    `/screenplay/v2/projects/${data.projectId}/agent-sessions`,
    {},
    data.commandId,
  ),
  submitScreenplayConversationTurn: (data) => apiPostIdempotent(
    `/screenplay/v2/projects/${data.projectId}/conversation/turns`,
    {
      sessionId: data.sessionId,
      content: data.content,
      runtime: data.runtime,
      ...(data.stageCommand ? { stageCommand: data.stageCommand } : {}),
    },
    data.commandId,
  ),
  getScreenplayConversationSnapshot: (data) => apiGet(
    `/screenplay/v2/projects/${data.projectId}/conversation/snapshot?sessionId=${data.sessionId}`,
  ),
  listScreenplayConversationEvents: (data) => {
    const params = new URLSearchParams({
      sessionId: String(data.sessionId),
      after: String(data.after ?? 0),
      limit: String(data.limit ?? 100),
      follow: 'false',
    })
    return apiGet(
      `/screenplay/v2/projects/${data.projectId}/conversation/events?${params.toString()}`,
    )
  },
  watchScreenplayConversationEvents: (data) => {
    const params = new URLSearchParams({
      sessionId: String(data.sessionId),
      after: String(Math.max(0, data.after)),
      chunkAfter: String(Math.max(0, data.chunkAfter ?? 0)),
      limit: '500',
      follow: 'true',
    })
    const source = new EventSource(
      `${backendBaseUrl}/api/screenplay/v2/projects/${encodeURIComponent(data.projectId)}/conversation/events?${params.toString()}`,
    )
    source.onmessage = (message) => {
      try {
        const event = JSON.parse(message.data)
        if (event && typeof event === 'object' && (
          Number.isFinite(Number(event.cursor))
          || (
            event.kind === 'agent_chunks'
            && Number.isFinite(Number(event.nextCursor))
            && Array.isArray(event.chunks)
          )
        )) {
          data.onEvent(event)
        }
      } catch {
        // Snapshot polling remains the recovery path for a malformed notice.
      }
    }
    return () => source.close()
  },
  cancelScreenplayConversationTurn: (data) => apiPostIdempotent(
    `/screenplay/v2/conversation/turns/${data.turnId}/cancel`,
    {},
    data.commandId,
  ),
  resumeScreenplayConversationOperation: (data) => apiPostIdempotent(
    `/screenplay/v2/conversation/operations/${data.operationId}/resume`,
    {
      expectedOperationRevision: data.expectedOperationRevision,
      runtime: data.runtime,
    },
    data.commandId,
  ),
  truncateScreenplayConversationFromTurn: (data) => apiDelete(
    `/screenplay/v2/conversation/turns/${data.turnId}/and-after`,
  ),
  createScreenplayV2Project: (data) => apiPostIdempotent(
    '/screenplay/v2/projects',
    {
      title: data.title,
      format: data.format,
      source: data.source,
      brief: data.brief,
    },
    data.commandId,
  ),
  getScreenplayV2Workspace: (data) =>
    apiGet(`/screenplay/v2/projects/${data.projectId}/workspace`),
  getScreenplayV2Revision: (data) => {
    const view = data.view || 'full'
    return apiGet(`/screenplay/v2/revisions/${data.revisionId}?view=${view}`)
  },
  listScreenplayV2RevisionHistory: (data) => {
    const params = new URLSearchParams()
    if (data.cursor) params.set('cursor', data.cursor)
    if (data.limit) params.set('limit', String(data.limit))
    const query = params.toString()
    return apiGet(
      `/screenplay/v2/projects/${data.projectId}/deliverables/${data.role}/revisions${query ? `?${query}` : ''}`,
    )
  },
  getScreenplayV2LatestReviewForDraft: (data) => apiGet(
    `/screenplay/v2/projects/${data.projectId}/draft-revisions/${data.draftRevisionId}/latest-review`,
  ),
  createScreenplayV2WorkingCopyFromRevision: (data) => apiPostIdempotent(
    `/screenplay/v2/projects/${data.projectId}/revisions/${data.revisionId}/working-copy`,
    {
      expectedProjectRevision: data.expectedProjectRevision,
      expectedWorkingCopyRevision: data.expectedWorkingCopyRevision,
    },
    data.commandId,
  ),
  updateScreenplayV2WorkingCopy: (data) => apiPatch(
    `/screenplay/v2/working-copies/${data.workingCopyId}`,
    {
      expectedRevision: data.expectedRevision,
      content: data.content,
    },
  ),
  publishScreenplayV2WorkingCopy: (data) => apiPostIdempotent(
    `/screenplay/v2/working-copies/${data.workingCopyId}/publish`,
    {
      expectedProjectRevision: data.expectedProjectRevision,
      expectedWorkingCopyRevision: data.expectedWorkingCopyRevision,
    },
    data.commandId,
  ),
  acceptScreenplayV2Revision: (data) => apiPostIdempotent(
    `/screenplay/v2/projects/${data.projectId}/revisions/${data.revisionId}/accept`,
    {
      expectedProjectRevision: data.expectedProjectRevision,
      confirmInvalidation: data.confirmInvalidation === true,
    },
    data.commandId,
  ),
  adjudicateScreenplayV2Review: (data) => apiPostIdempotent(
    `/screenplay/v2/projects/${data.projectId}/review-decisions`,
    {
      expectedProjectRevision: data.expectedProjectRevision,
      reviewRevisionId: data.reviewRevisionId,
      decisions: data.decisions,
    },
    data.commandId,
  ),
  finalizeScreenplayV2Project: (data) => apiPostIdempotent(
    `/screenplay/v2/projects/${data.projectId}/finalize`,
    {
      expectedProjectRevision: data.expectedProjectRevision,
      draftRevisionId: data.draftRevisionId,
      reviewRevisionId: data.reviewRevisionId,
    },
    data.commandId,
  ),
  updateScreenplayV2Project: (data) => apiPatchIdempotent(
    `/screenplay/v2/projects/${data.projectId}`,
    {
      expectedProjectRevision: data.expectedProjectRevision,
      title: data.title,
    },
    data.commandId,
  ),
  archiveScreenplayV2Project: (data) => apiPostIdempotent(
    `/screenplay/v2/projects/${data.projectId}/archive`,
    { expectedProjectRevision: data.expectedProjectRevision },
    data.commandId,
  ),
  restoreScreenplayV2Project: (data) => apiPostIdempotent(
    `/screenplay/v2/projects/${data.projectId}/restore`,
    { expectedProjectRevision: data.expectedProjectRevision },
    data.commandId,
  ),
  deleteScreenplayV2Project: (data) => apiPostIdempotent(
    `/screenplay/v2/projects/${data.projectId}/delete`,
    { expectedProjectRevision: data.expectedProjectRevision },
    data.commandId,
  ),
  getCharacters: (data) => apiGet(`/books/${data.bookId}/characters`),
  createCharacter: (data) => apiPost(`/books/${data.bookId}/characters`, { data: data.data }),
  updateCharacter: (data) => apiPut(`/characters/${data.id}`, { data: data.data }),
  deleteCharacter: (data) => apiDelete(`/characters/${data.id}`),

  getSettingEntities: (data) =>
    apiGet(`/books/${data.bookId}/setting-entities${data.type ? `?type=${data.type}` : ''}`),
  createSettingEntity: (data) => apiPost(`/books/${data.bookId}/setting-entities`, {
    entityType: data.entityType,
    name: data.name,
    tags: data.tags || '',
    profileMd: data.profileMd || '',
  }),
  updateSettingEntity: (data) => apiPut(`/setting-entities/${data.id}`, data.data ?? {}),
  deleteSettingEntity: (data) => apiDelete(`/setting-entities/${data.id}`),

  getCharacterOptions: (data) => apiGet(`/character-options?category=${data.category}`),
  addCharacterOption: (data) => apiPost('/character-options', data),
  updateCharacterOption: (data) => apiPut(`/character-options/${data.id}`, { value: data.value }),
  deleteCharacterOption: (data) => apiDelete(`/character-options/${data.id}`),

  saveOutline: (data) => apiPost('/outlines', data),
  getOutlines: (typeFilter) => apiGet(`/outlines${typeFilter ? `?type=${typeFilter}` : ''}`),
  getVolumeOutlines: (bookId) => apiGet(`/outlines/volume/${bookId}`),
  getOutlineByWritingChapter: (id) => apiGet(`/outlines/by-writing-chapter/${id}`),
  getOutlineForChapter: (id) => apiGet(`/outlines/for-chapter/${id}`),
  getGlobalOutline: (bookId) => apiGet(`/outlines/global/${bookId}`),
  ensureGlobalOutline: (bookId) => apiPost(`/outlines/global/${bookId}/ensure`, {}),
  getWritingOutline: (bookId) => apiGet(`/outlines/writing/${bookId}`),
  getChapterOutlines: (bookId) => apiGet(`/outlines/chapter/${bookId}`),
  getAssociableOutlines: (bookId) => apiGet(`/outlines/associable/${bookId}`),
  deleteOutline: (data) => apiDelete(`/outlines/${data.outlineId}`),
  updateOutline: (data) => apiPut(`/outlines/${data.outlineId}`, data),
  listOutlineHistory: (data) =>
    apiGet(`/outlines/${data.outlineId}/history${data.limit ? `?limit=${data.limit}` : ''}`),
  getOutlineHistory: (data) => apiGet(`/outlines/history/${data.historyId}`),
  restoreOutlineHistory: (data) => apiPost(`/outlines/history/${data.historyId}/restore`, {}),

  saveChapters: (data) => apiPost(`/chapters/${data.outlineId}`, { chapters: data.chapters }),
  getChapters: (data) => apiGet(`/chapters/${data.outlineId}`),
  addChapter: (data) => apiPost(`/chapters/${data.outlineId}/add`, data),
  deleteChapter: (data) => apiDelete(`/chapters/${data.id}`),
  renameChapter: (data) => apiPut(`/chapters/${data.id}/rename`, { title: data.title }),
  updateChapterProgress: (data) =>
    apiPut(`/chapters/${data.id}/progress`, { progress: data.progress }),

  saveArticle: (data) =>
    apiPut(`/articles/${data.chapterId}`, { content: data.content, source: data.source }),
  getArticle: (data) => apiGet(`/articles/${data.chapterId}`),
  analyzeChapterStoryMemory: (data) =>
    apiPost(`/books/${data.bookId}/chapters/${data.chapterId}/story-memory/analyze`, {
      modelId: data.modelId || null,
    }),
  reviewStoryMemoryDelta: (data) =>
    apiPost(`/story-memory/deltas/${data.deltaId}/evolution-review`, {}),
  getStoryMemoryEvolutionReview: (data) =>
    apiGet(`/story-memory/deltas/${data.deltaId}/evolution-review`),
  getStoryMemoryVersions: (data) =>
    apiGet(`/books/${data.bookId}/story-memory/versions?memoryKey=${encodeURIComponent(data.memoryKey)}`),
  listStoryMemoryEvolutionReviews: (data) => {
    const statuses = data.statuses || []
    const query = statuses.map((value) => `status=${encodeURIComponent(value)}`).join('&')
    return apiGet(`/books/${data.bookId}/story-memory/evolution-reviews${query ? `?${query}` : ''}`)
  },
  resolveStoryMemoryEvolutionReview: (data) =>
    apiPost(`/story-memory/deltas/${data.deltaId}/evolution-review/resolve`, {
      resolutions: data.resolutions,
    }),

  getStoryBackground: (data) => apiGet(`/story-background/${data.bookId}`),
  saveStoryBackground: (data) =>
    apiPut(`/story-background/${data.bookId}`, { content: data.content }),
  getStoryBackgroundAttachments: (data) =>
    apiGet(`/story-background/${data.bookId}/attachments`),
  deleteStoryBackgroundAttachment: (data) =>
    apiDelete(`/story-background/attachments/${data.id}`),

  getBookStyle: (data) => apiGet(`/book-style/${data.bookId}`),
  saveBookStyle: (data) => apiPut(`/book-style/${data.bookId}`, {
    pov: data.pov || '',
    tone: data.tone || '',
    pace: data.pace || '',
    banned_rules: data.banned_rules || '',
    reference_chapter_ids: data.reference_chapter_ids || '',
    free_notes: data.free_notes || '',
  }),

  commitChapterDiff: (data) => apiPost(`/chapter-diff/${data.chapterId}/commit`, {
    content: data.content,
    before_text: data.beforeText || '',
    after_text: data.afterText || '',
    source: data.source || 'ai_rewrite',
    accepted_segments: data.acceptedSegments || 0,
    rejected_segments: data.rejectedSegments || 0,
  }),
  listChapterDiff: (data) =>
    apiGet(`/chapter-diff/${data.chapterId}?limit=${data.limit ?? 50}`),
  getChapterDiff: (data) => apiGet(`/chapter-diff/by-id/${data.diffId}`),
  rollbackChapterDiff: (data) =>
    apiPost(`/chapter-diff/by-id/${data.diffId}/rollback`, {}),

  commitCharacterSettingDiff: (data) =>
    apiPost(`/setting-diff/character/${data.characterId}/commit`, {
      name: data.name,
      tags: data.tags,
      profileMd: data.profileMd,
      before: data.before,
      after: data.after,
      source: data.source || 'ai_tool',
      accepted_segments: data.acceptedSegments || 0,
      rejected_segments: data.rejectedSegments || 0,
    }),
  commitBackgroundSettingDiff: (data) =>
    apiPost(`/setting-diff/background/${data.bookId}/commit`, {
      content: data.content,
      before_content: data.beforeContent || '',
      after_content: data.afterContent || '',
      source: data.source || 'ai_tool',
      accepted_segments: data.acceptedSegments || 0,
      rejected_segments: data.rejectedSegments || 0,
    }),
  listCharacterSettingHistory: (data) =>
    apiGet(`/setting-diff/character/${data.characterId}/history?limit=${data.limit ?? 50}`),
  getCharacterSettingHistory: (data) =>
    apiGet(`/setting-diff/character/history/${data.historyId}`),
  rollbackCharacterSettingHistory: (data) =>
    apiPost(`/setting-diff/character/history/${data.historyId}/rollback`, {}),
  listBackgroundSettingHistory: (data) =>
    apiGet(`/setting-diff/background/${data.bookId}/history?limit=${data.limit ?? 50}`),
  getBackgroundSettingHistory: (data) =>
    apiGet(`/setting-diff/background/history/${data.historyId}`),
  rollbackBackgroundSettingHistory: (data) =>
    apiPost(`/setting-diff/background/history/${data.historyId}/rollback`, {}),
  commitEntitySettingDiff: (data) =>
    apiPost(`/setting-diff/entity/${data.entityId}/commit`, {
      name: data.name,
      tags: data.tags,
      profileMd: data.profileMd,
      before: data.before,
      after: data.after,
      source: data.source || 'ai_tool',
      accepted_segments: data.acceptedSegments || 0,
      rejected_segments: data.rejectedSegments || 0,
    }),
  listEntitySettingHistory: (data) =>
    apiGet(`/setting-diff/entity/${data.entityId}/history?limit=${data.limit ?? 50}`),
  getEntitySettingHistory: (data) =>
    apiGet(`/setting-diff/entity/history/${data.historyId}`),
  rollbackEntitySettingHistory: (data) =>
    apiPost(`/setting-diff/entity/history/${data.historyId}/rollback`, {}),

  createSession: (data) => apiPost('/sessions', data),
  getSessions: (data) => {
    const params = new URLSearchParams()
    params.set('bookId', data.bookId)
    if (data.chapterId != null) params.set('chapterId', data.chapterId)
    if (data.includeClosed) params.set('includeClosed', 'true')
    if (data.scope) params.set('scope', data.scope)
    return apiGet(`/sessions?${params}`)
  },
  setSessionClosed: (data) => apiPut(`/sessions/${data.sessionId}/close`, {}),
  setSessionReopened: (data) => apiPut(`/sessions/${data.sessionId}/reopen`, {}),
  deleteSession: (data) => apiDelete(`/sessions/${data.sessionId}`),
  updateSessionTitle: (data) =>
    apiPut(`/sessions/${data.sessionId}/title`, { title: data.title }),

  saveConversation: async (data) => {
    const response = await apiPost<{ id: number | null }>('/conversations', data)
    const conversationId = response.data?.id
    if (response.success && typeof conversationId === 'number') {
      recordAiDebugConversationSaved({
        conversationId,
        sessionId: data.sessionId,
        agentRunId: data.agentRunId,
        prompt: data.prompt,
        response: data.response,
      })
    }
    return response
  },
  getConversations: (data) => apiGet(`/conversations/${data.sessionId}`),
  deleteConversationsAfterTurn: (data) =>
    apiDelete(`/conversations/${data.sessionId}/after-turn?keepTurnCount=${data.keepTurnCount}`),

  saveAiFavorite: (data) => apiPost('/ai-favorites', data),
  getAiFavorites: () => apiGet('/ai-favorites'),
  deleteAiFavorite: (data) => apiDelete(`/ai-favorites/${data.id}`),

  listPromptTemplates: () => apiGet('/prompt-templates'),
  createPromptTemplate: (data) => apiPost('/prompt-templates', data),
  updatePromptTemplate: (data) => apiPut(`/prompt-templates/${data.id}`, data.data ?? {}),
  deletePromptTemplate: (data) => apiDelete(`/prompt-templates/${data.id}`),
  reorderPromptTemplates: (data) => apiPost('/prompt-templates/reorder', { ids: data.ids }),

  addSparkIdea: (data) => apiPost('/spark-ideas', data),
  updateSparkIdea: (data) => apiPut(`/spark-ideas/${data.id}`, { data: data.data }),
  deleteSparkIdea: (data) => apiDelete(`/spark-ideas/${data.id}`),
  getSparkIdeasByBook: (data) =>
    apiGet(`/spark-ideas/by-book?bookId=${data.bookId}${data.layer ? `&layer=${data.layer}` : ''}`),
  getSparkIdeasByIds: (data) => apiPost('/spark-ideas/by-ids', data),
  getSparkIdeasForPrompt: (data) => apiPost('/spark-ideas/for-prompt', data),

  addForeshadowing: (data) => apiPost('/foreshadowing', data),
  updateForeshadowing: (data) =>
    apiPut(`/foreshadowing/${data.id}`, { data: data.data }),
  deleteForeshadowing: (data) => apiDelete(`/foreshadowing/${data.id}`),
  getForeshadowingByBook: (data) =>
    apiGet(`/foreshadowing/by-book?bookId=${data.bookId}${data.status ? `&status=${data.status}` : ''}`),
  getForeshadowingByIds: (data) => apiPost('/foreshadowing/by-ids', data),
  getForeshadowingForPrompt: (data) => apiPost('/foreshadowing/for-prompt', data),

  createMemory: (data) => apiPost('/memories', data),
  updateMemory: (data) => apiPut(`/memories/${data.id}`, { data: data.data }),
  archiveMemory: (data) => apiPost(`/memories/${data.id}/archive`, {}),
  searchMemories: (data) => apiPost('/memories/search', data),
  listUnifiedMemories: (data) => {
    const params = new URLSearchParams()
    if (data.query) params.set('q', data.query)
    for (const value of data.statuses || []) params.append('status', value)
    for (const value of data.kinds || []) params.append('kind', value)
    for (const value of data.sources || []) params.append('source', value)
    if (data.limit) params.set('limit', String(data.limit))
    const query = params.toString()
    return apiGet(`/books/${data.bookId}/memories/unified${query ? `?${query}` : ''}`)
  },
  getMemoriesByIds: (data) => apiPost('/memories/by-ids', data),
  linkMemories: (data) => apiPost('/memories/link', data),
  buildMemoryContext: (data) => apiPost('/memories/context', data),

  generateSessionTitle: (data) => apiPost('/ai/title', data),
  listModels: (data) => apiPost('/ai/models', data),
  getAgentRunSnapshot: (data) => {
    const params = new URLSearchParams()
    if (data.after != null) params.set('after', String(data.after))
    if (data.limit != null) params.set('limit', String(data.limit))
    const query = params.toString()
    return apiGet(`/ai/agent-runs/${encodeURIComponent(data.runId)}${query ? `?${query}` : ''}`)
  },
  getAgentRunDiagnostics: (data) =>
    apiGet(`/ai/agent-runs/${encodeURIComponent(data.runId)}/diagnostics`),
  maintainAgentArtifacts: () => apiPost('/ai/artifacts/maintenance', {}),
  getAgentRunStabilityTrend: (data) => {
    const params = new URLSearchParams()
    if (data.scope) params.set('scope', data.scope)
    if (data.limit != null) params.set('limit', String(data.limit))
    const query = params.toString()
    return apiGet(`/ai/agent-runs/${encodeURIComponent(data.runId)}/stability-trend${query ? `?${query}` : ''}`)
  },
  getLatestSessionAgentRun: (data) =>
    apiGet(`/ai/session-runs/latest?sessionId=${encodeURIComponent(data.sessionId)}`),
  captureAiErrorReport: (data) => apiPost('/ai/error-reports', data),
  listAiErrorReports: (data = {}) => {
    const params = new URLSearchParams()
    if (data.status) params.set('status', data.status)
    if (data.limit != null) params.set('limit', String(data.limit))
    const query = params.toString()
    return apiGet(`/ai/error-reports${query ? `?${query}` : ''}`)
  },
  getAiErrorReport: (data) =>
    apiGet(`/ai/error-reports/${encodeURIComponent(data.reportId)}`),
  submitAiErrorReport: (data) =>
    apiPost(`/ai/error-reports/${encodeURIComponent(data.reportId)}/submit`, {
      userNote: data.userNote || null,
    }),
  cancelAgentRun: (data) =>
    apiPost(`/ai/agent-runs/${encodeURIComponent(data.runId)}/cancel`, {}),
  createAgentDelegation: (data) =>
    apiPost(`/ai/agent-runs/${encodeURIComponent(data.runId)}/delegations`, {
      agentRole: data.agentRole,
      objective: data.objective,
      input: data.input || {},
      required: data.required !== false,
      priority: data.priority || 0,
    }),
  resolveAiToolApproval: (data) =>
    apiPost(`/ai/tool-approvals/${data.approvalId}`, {
      approved: Boolean(data.approved),
    }),

  aiChatStream: (data) => {
    const streamId = data.streamId || `ai-${Date.now()}-${Math.random().toString(36).slice(2)}`
    const requestData = { ...data }
    delete requestData.streamId
    const abortController = new AbortController()
    aiAbortControllers.set(streamId, abortController)
    latestAiStreamId = streamId
    startAiDebugRun(streamId, data)
    let observedAgentRunId: string | undefined
    let observedErrorCode: string | undefined
    let observedTaskType: string | undefined
    let receivedVisibleOutput = false

    const attachErrorReport = async (
      chunk: AiChunk,
      errorMessage = chunk.error,
      errorCode = observedErrorCode,
    ): Promise<void> => {
      if (!errorMessage || chunk.aborted || chunk.errorReport) return
      try {
        const response = await apiPost<AiErrorReport>('/ai/error-reports', {
          streamId,
          agentRunId: observedAgentRunId,
          sessionId: data.sessionId,
          bookId: data.bookId || null,
          chapterId: data.chapterId || null,
          source: aiErrorReportSource(streamId),
          errorCode,
          errorMessage,
          model: chunk.model || data.options?.model,
          diagnostics: {
            ...aiErrorReportDiagnostics(data),
            ...(observedTaskType ? { taskType: observedTaskType } : {}),
          },
        })
        if (response.success && response.data?.id) {
          chunk.errorReport = response.data
        }
      } catch {
        // Error capture must never replace or delay the original terminal error.
      }
    }

    fetch(`${backendBaseUrl}/api/ai/chat/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(requestData),
      signal: abortController.signal,
    }).then(async (response) => {
      if (!response.ok || !response.body) {
        throw new Error(`AI 请求失败 (${response.status})`)
      }
      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let receivedTerminalChunk = false
      const processLines = async (lines: string[]) => {
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          const payload = line.slice(6).trim()
          if (payload === '[DONE]') continue
          try {
            const chunk = JSON.parse(payload) as AiChunk
            const transport = chunk as AiChunk & {
              done?: boolean
              aborted?: boolean
              error?: string
              longTaskDispatched?: { taskId?: string }
              runResult?: {
                runId: string
                status: string
                errorCode?: string | null
              }
            }
            if (abortController.signal.aborted && transport.done) {
              transport.aborted = true
            }
            observedAgentRunId =
              (isCanonicalOutputEvent(chunk) ? chunk.runId : undefined) ||
              observedAgentRunId
            observedErrorCode =
              transport.runResult?.errorCode ||
              (isCanonicalOutputEvent(chunk)
                && chunk.kind === 'run.lifecycle'
                && typeof chunk.payload.errorCode === 'string'
                ? chunk.payload.errorCode
                : undefined) ||
              observedErrorCode
            if (chunk.longTaskDispatched?.taskId) {
              observedTaskType = chunk.longTaskDispatched.kind === 'screenplay_draft_generation'
                ? '持久化长任务 · 剧本正文分批创作'
                : `持久化长任务 · ${chunk.longTaskDispatched.kind || '通用任务'}`
            }
            if (
              (isCanonicalOutputEvent(chunk)
                && chunk.visibility === 'public'
                && chunk.source === 'provider'
                && chunk.kind === 'provider.content_delta'
                && typeof chunk.payload.delta === 'string'
                && chunk.payload.delta.trim())
              ||
              transport.longTaskDispatched?.taskId
            ) {
              receivedVisibleOutput = true
            }
            if (transport.done || transport.error) receivedTerminalChunk = true
            if (
              transport.done
              && transport.runResult
              && ['failed', 'blocked'].includes(transport.runResult.status)
            ) {
              await attachErrorReport(
                transport,
                presentAgentRunError(
                  transport.runResult.status,
                  transport.runResult.errorCode,
                ),
                transport.runResult.errorCode || undefined,
              )
            } else if (transport.error) {
              await attachErrorReport(transport)
            } else if (
              transport.done &&
              !transport.aborted &&
              !receivedVisibleOutput
            ) {
              await attachErrorReport(
                chunk,
                '模型未返回可见内容。',
                'empty_model_response',
              )
            }
            recordAiDebugChunk(streamId, chunk)
            aiChunkListeners.forEach((listener) => listener({ ...chunk, streamId }))
          } catch {
            // Ignore malformed/incomplete SSE events; the next event can still be valid.
          }
        }
      }
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() || ''
        await processLines(lines)
      }
      if (buffer.trim()) await processLines(buffer.split('\n'))
      if (!receivedTerminalChunk) {
        const terminalChunk = abortController.signal.aborted
          ? { done: true, aborted: true, streamId }
          : { done: true, streamId }
        recordAiDebugChunk(streamId, terminalChunk)
        aiChunkListeners.forEach((listener) => listener(terminalChunk))
      }
    }).catch(async (error) => {
      const chunk = abortController.signal.aborted
        ? { done: true, aborted: true, streamId }
        : { error: error instanceof Error ? error.message : String(error), streamId }
      await attachErrorReport(chunk)
      recordAiDebugChunk(streamId, chunk)
      aiChunkListeners.forEach((listener) => listener(chunk))
    }).finally(() => {
      if (aiAbortControllers.get(streamId) === abortController) {
        aiAbortControllers.delete(streamId)
      }
      if (latestAiStreamId === streamId) {
        latestAiStreamId = Array.from(aiAbortControllers.keys()).at(-1) || null
      }
    })
    return streamId
  },

  abortAiStream: (streamId) => {
    const targetId = streamId || latestAiStreamId
    const controller = targetId ? aiAbortControllers.get(targetId) : null
    if (!controller || !targetId) return
    markAiDebugAbortRequested(targetId)
    controller.abort()
    aiAbortControllers.delete(targetId)
    if (latestAiStreamId === targetId) {
      latestAiStreamId = Array.from(aiAbortControllers.keys()).at(-1) || null
    }
  },

  onAiChunk: (callback, streamId) => {
    const handler = (chunk: AiChunk) => {
      if (!streamId || chunk.streamId === streamId) callback(chunk)
    }
    aiChunkListeners.push(handler)
    return () => {
      aiChunkListeners = aiChunkListeners.filter((listener) => listener !== handler)
    }
  },

  debugLog: (payload) => {
    fetch(`${backendBaseUrl}/api/debug-log`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }).catch(() => {})
  },

  getSettings: () => apiGet('/settings'),
  setSettings: (data) => apiPut('/settings', { data }),

  getStoryHealth: (data) => apiGet(`/dashboard/health?bookId=${data.bookId}`),
  getWritingStats: (data) =>
    apiGet(`/dashboard/writing-stats?bookId=${data.bookId}`),
  setWritingGoal: (data) => apiPost('/dashboard/writing-goal', {
    bookId: String(data.bookId),
    dailyWords: data.dailyWords || 0,
  }),
}
