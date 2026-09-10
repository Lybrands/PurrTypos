import type {
  AiErrorReport,
  AiWritingChatRequestReceipt,
  ApiResult,
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
import {
  canonicalProviderTextDelta,
  isCanonicalOutputEvent,
} from '../agent-runtime/canonicalOutput'
import { presentAgentRunError } from '../agent-runtime/agentErrorPresentation'
import {
  resolveRootRunBinding,
  resolveTerminalRootOwnership,
} from '../agent-runtime/rootOwnership'
import { recoverDurableAgentStream } from './durableAgentStreamRecovery'
import { consumeAgentEventStream, waitForAgentRetry } from './agentEventStream'
import {
  reserveWritingChatRequest,
  replayWritingChatPostUntilObserved,
  type WritingChatRequestReservation,
} from './writingChatRequestReceipt'

export type PlatformApiKey =
  | 'openFilePath'
  | 'writeExportFiles'
  | 'writeSingleTextFile'
  | 'writeScreenplayFile'
  | 'exportScreenplayPdf'
  | 'exportEpub'
  | 'exportDatabase'
  | 'importDatabase'
  | 'openDatabaseDirectory'
  | 'openAndReadTextFile'
  | 'pickNovelSourceTextFile'
  | 'pickStoryBackgroundAttachments'
  | 'openStoryBackgroundAttachment'

export type BackendApi = Omit<ElectronAPI, PlatformApiKey>

type AiChunk = Parameters<ElectronAPI['onAiChunk']>[0] extends (chunk: infer T) => void ? T : never
type AiStreamRequest = Parameters<ElectronAPI['aiChatStream']>[0]

let aiChunkListeners: Array<(chunk: AiChunk) => void> = []
const aiAbortControllers = new Map<string, AbortController>()
const writingRequestReservations = new Map<
  string,
  Promise<WritingChatRequestReservation>
>()
let latestAiStreamId: string | null = null

async function responseMessage(response: Response): Promise<string> {
  try {
    const payload = await response.json() as {
      detail?: unknown
      error?: unknown
    }
    const value = payload.detail ?? payload.error
    if (typeof value === 'string' && value.trim()) return value
  } catch {
    // Fall back to the HTTP status below.
  }
  return `AI 请求失败 (${response.status})`
}

async function sendWritingRequestReservation(
  requestId: string,
  body: AiStreamRequest,
): Promise<WritingChatRequestReservation> {
  const response = await fetch(
    `${backendBaseUrl}/api/ai/chat/requests/${encodeURIComponent(requestId)}`,
    {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    },
  )
  if (!response.ok) {
    const error = await responseMessage(response)
    if (response.status >= 400 && response.status < 500) {
      return { kind: 'rejected', error, status: response.status }
    }
    throw new Error(error)
  }
  const payload = await response.json() as {
    success?: boolean
    data?: AiWritingChatRequestReceipt
    error?: string
  }
  if (!payload.success || !payload.data) {
    throw new Error(payload.error || 'Writing Agent 请求回执缺失')
  }
  return { kind: 'accepted', receipt: payload.data }
}

async function sendWritingRequestCancel(
  requestId: string,
): Promise<ApiResult<AiWritingChatRequestReceipt | null>> {
  while (true) {
    try {
      const response = await fetch(
        `${backendBaseUrl}/api/ai/chat/requests/${encodeURIComponent(requestId)}/cancel`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: '{}',
        },
      )
      if (!response.ok) {
        const error = await responseMessage(response)
        if (response.status >= 400 && response.status < 500) {
          return { success: false, data: null, error }
        }
        throw new Error(error)
      }
      return await response.json() as ApiResult<AiWritingChatRequestReceipt>
    } catch {
      // The cancel endpoint is idempotent. A dropped response is ambiguous, so
      // replay the same request identity until an explicit result is observed.
      await new Promise<void>((resolve) => globalThis.setTimeout(resolve, 500))
    }
  }
}

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
    thinkingMode: data.options?.thinking?.type ?? 'provider-default',
    reasoningEffort: data.options?.reasoning_effort ?? 'provider-default',
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
  watchScreenplayConversationEvents: (data) => {
    const controller = new AbortController()
    void consumeAgentEventStream({
      url: after => `${backendBaseUrl}/api/screenplay/v2/projects/${encodeURIComponent(data.projectId)}/conversation/events?sessionId=${data.sessionId}&chunkAfter=${after}&limit=500`,
      after: data.chunkAfter, signal: controller.signal, onEvent: data.onEvent,
    }).catch(error => data.onError?.(error instanceof Error ? error : new Error(String(error))))
    return () => controller.abort()
  },
  consumeNovelAnalysisEvents: data => consumeAgentEventStream({
    url: after => `${backendBaseUrl}/api/novel-source-revisions/${encodeURIComponent(data.revisionId)}/analysis-events?after=${after}&limit=500`,
    signal: data.signal, onEvent: data.onEvent,
  }),
  consumeAgentRunEvents: data => consumeAgentEventStream({
    url: after => `${backendBaseUrl}/api/ai/agent-runs/${encodeURIComponent(data.runId)}/events?sessionId=${data.sessionId}&after=${after}`,
    after: data.after, signal: data.signal, onEvent: data.onEvent,
  }),
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
  deleteCharacter: (data) => apiDelete(`/characters/${data.id}?baseRevision=${encodeURIComponent(data.baseRevision || "")}`),

  getSettingEntities: (data) =>
    apiGet(`/books/${data.bookId}/setting-entities${data.type ? `?type=${data.type}` : ''}`),
  createSettingEntity: (data) => apiPost(`/books/${data.bookId}/setting-entities`, {
    entityType: data.entityType,
    name: data.name,
    tags: data.tags || '',
    profileMd: data.profileMd || '',
  }),
  updateSettingEntity: (data) => apiPut(`/setting-entities/${data.id}`, data.data ?? {}),
  deleteSettingEntity: (data) => apiDelete(`/setting-entities/${data.id}?baseRevision=${encodeURIComponent(data.baseRevision || "")}`),

  getCharacterOptions: (data) => apiGet(`/character-options?category=${data.category}`),
  addCharacterOption: (data) => apiPost('/character-options', data),
  updateCharacterOption: (data) => apiPut(`/character-options/${data.id}`, { value: data.value }),
  deleteCharacterOption: (data) => apiDelete(`/character-options/${data.id}`),

  saveOutline: (data) => apiPost('/outlines', data),
  getVolumeOutlines: (bookId) => apiGet(`/outlines/volume/${bookId}`),
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
  getChapters: (data) => apiGet(`/chapters/${data.outlineId}`),
  addChapter: (data) => apiPost(`/chapters/${data.outlineId}/add`, data),
  deleteChapter: (data) => apiDelete(`/chapters/${data.id}`),
  renameChapter: (data) => apiPut(`/chapters/${data.id}/rename`, { title: data.title }),

  saveArticle: (data) =>
    apiPut(`/articles/${data.chapterId}`, { content: data.content, source: data.source }),
  getArticle: (data) => apiGet(`/articles/${data.chapterId}`),
  getStoryMemoryVersions: (data) =>
    apiGet(`/books/${data.bookId}/story-memory/versions?memoryKey=${encodeURIComponent(data.memoryKey)}`),
  resolveStoryMemoryEvolutionReview: (data) =>
    apiPost(`/story-memory/deltas/${data.deltaId}/evolution-review/resolve`, {
      resolutions: data.resolutions,
    }),

  getStoryBackground: (data) => apiGet(`/story-background/${data.bookId}`),
  saveStoryBackground: (data) =>
    apiPut(`/story-background/${data.bookId}`, { content: data.content, baseRevision: data.baseRevision }),
  getStoryBackgroundAttachments: (data) =>
    apiGet(`/story-background/${data.bookId}/attachments`),
  deleteStoryBackgroundAttachment: (data) =>
    apiDelete(`/story-background/attachments/${data.id}`),

  previewNovelSourceImport: (data) => apiPost('/novel-sources/import/preview', data),
  confirmNovelSourceImport: (data) => apiPost('/novel-sources/import/confirm', data),
  freezeBookAsNovelSource: (data) => apiPost('/novel-sources/freeze-book', data),
  listNovelSources: (data = {}) => apiGet(
    `/novel-sources${data.includeArchived ? '?includeArchived=true' : ''}`,
  ),
  getNovelSource: (data) => apiGet(`/novel-sources/${data.workId}`),
  deleteNovelSource: (data) => apiDelete(`/novel-sources/${data.workId}`),
  getNovelSourceRevision: (data) => apiGet(
    `/novel-source-revisions/${data.revisionId}`,
  ),
  getNovelSourceSection: (data) => {
    const params = new URLSearchParams()
    if (data.startCharacter != null) params.set('startCharacter', String(data.startCharacter))
    if (data.characterLimit != null) params.set('characterLimit', String(data.characterLimit))
    const query = params.size ? `?${params.toString()}` : ''
    return apiGet(
      `/novel-source-revisions/${data.revisionId}/sections/${data.sectionId}${query}`,
    )
  },
  searchNovelSourceSections: (data) => apiGet(
    `/novel-source-revisions/${data.revisionId}/search?q=${encodeURIComponent(data.query)}&limit=${data.limit ?? 12}`,
  ),
  startNovelAnalysis: (data) => apiPostIdempotent(
    `/novel-source-revisions/${data.revisionId}/analyses`,
    { runtime: data.runtime, prompt: data.prompt, conversationId: data.conversationId },
    data.commandId,
  ),
  followUpNovelAnalysis: (data) => apiPostIdempotent(
    `/novel-source-revisions/${data.revisionId}/analysis-follow-ups`,
    { runtime: data.runtime, artifactId: data.artifactId, prompt: data.prompt, conversationId: data.conversationId, replaceRunId: data.replaceRunId },
    data.commandId,
  ),
  listNovelAnalysisRuns: (data) => apiGet(
    `/novel-source-revisions/${data.revisionId}/analysis-runs`,
  ),
  pauseNovelAnalysis: (data) => apiPost(
    `/novel-analysis-tasks/${data.taskId}/pause`,
    { expectedTaskRevision: data.expectedTaskRevision },
  ),
  resumeNovelAnalysis: (data) => apiPostIdempotent(
    `/novel-analysis-tasks/${data.taskId}/resume`,
    { runtime: data.runtime, retryFailed: data.retryFailed },
    data.commandId,
  ),
  cancelNovelAnalysis: (data) => apiPost(
    `/novel-analysis-tasks/${data.taskId}/cancel`,
    {},
  ),
  getNovelAnalysisArtifact: (data) => apiGet(
    `/novel-analysis-artifacts/${data.artifactId}`,
  ),
  reviewNovelAnalysisArtifact: (data) => apiPostIdempotent(
    `/novel-analysis-artifacts/${data.artifactId}/review`,
    { facts: data.facts, craftCards: data.craftCards, storyOverview: data.storyOverview, techniqueResult: data.techniqueResult },
    data.commandId,
  ),
  publishNovelAnalysisArtifact: (data) => apiPost(
    `/novel-analysis-artifacts/${data.artifactId}/publish`,
    {},
  ),
  listPublishedNovelAnalyses: (data) => apiGet(
    `/novel-source-revisions/${data.revisionId}/analyses`,
  ),
  getPublishedNovelAnalysis: (data) => apiGet(
    `/novel-source-analyses/${data.analysisId}`,
  ),
  previewContinuationCanon: (data) => apiPost('/continuations/canon-preview', data),
  createContinuation: (data) => apiPost('/continuations', data),
  getContinuation: (data) => apiGet(`/continuations/${data.bookId}`),

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
      resolution: data.resolution,
      baseRevision: data.baseRevision,
    }),
  commitBackgroundSettingDiff: (data) =>
    apiPost(`/setting-diff/background/${data.bookId}/commit`, {
      content: data.content,
      before_content: data.beforeContent || '',
      after_content: data.afterContent || '',
      source: data.source || 'ai_tool',
      accepted_segments: data.acceptedSegments || 0,
      rejected_segments: data.rejectedSegments || 0,
      resolution: data.resolution,
      baseRevision: data.baseRevision,
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
      resolution: data.resolution,
      baseRevision: data.baseRevision,
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
  deleteConversationsAfterTurn: (data) => {
    const query = new URLSearchParams({
      keepTurnCount: String(data.keepTurnCount),
    })
    if (data.retireConversationIds !== undefined) {
      query.set('retireConversationIds', data.retireConversationIds.join(','))
    }
    if (data.expectedConversationIds !== undefined) {
      query.set('expectedConversationIds', data.expectedConversationIds.join(','))
    }
    if (data.retireRunIds !== undefined) {
      query.set('retireRunIds', data.retireRunIds.join(','))
    }
    if (data.expectedRunIds !== undefined) {
      query.set('expectedRunIds', data.expectedRunIds.join(','))
    }
    if (data.retireClientTurnIds !== undefined) {
      query.set('retireClientTurnIds', data.retireClientTurnIds.join(','))
    }
    return apiDelete(
      `/conversations/${data.sessionId}/after-turn?${query.toString()}`,
    )
  },

  saveAiFavorite: (data) => apiPost('/ai-favorites', data),
  getAiFavorites: () => apiGet('/ai-favorites'),
  deleteAiFavorite: (data) => apiDelete(`/ai-favorites/${data.id}`),

  listPromptTemplates: () => apiGet('/prompt-templates'),
  createPromptTemplate: (data) => apiPost('/prompt-templates', data),
  updatePromptTemplate: (data) => apiPut(`/prompt-templates/${data.id}`, data.data ?? {}),
  deletePromptTemplate: (data) => apiDelete(`/prompt-templates/${data.id}`),

  addSparkIdea: (data) => apiPost('/spark-ideas', data),
  updateSparkIdea: (data) => apiPut(
    `/spark-ideas/${data.id}`,
    {
      bookId: data.bookId,
      ...('content' in data.data ? { content: data.data.content } : {}),
      ...('layer' in data.data ? { layer: data.data.layer } : {}),
      ...('chapter_id' in data.data ? { chapterId: data.data.chapter_id } : {}),
      ...('character_id' in data.data ? { characterId: data.data.character_id } : {}),
    },
  ),
  deleteSparkIdea: (data) => apiDelete(
    `/spark-ideas/${data.id}?bookId=${encodeURIComponent(String(data.bookId))}`,
  ),
  getSparkIdeasByBook: (data) =>
    apiGet(`/spark-ideas/by-book?bookId=${data.bookId}${data.layer ? `&layer=${data.layer}` : ''}`),

  addForeshadowing: (data) => apiPost('/foreshadowing', data),
  updateForeshadowing: (data) => apiPut(
    `/foreshadowing/${data.id}`,
    {
      bookId: data.bookId,
      ...('content' in data.data ? { content: data.data.content } : {}),
      ...('type' in data.data ? { type: data.data.type } : {}),
      ...('expected_chapter_id' in data.data
        ? { expectedChapterId: data.data.expected_chapter_id }
        : {}),
      ...('status' in data.data ? { status: data.data.status } : {}),
      ...('resolved_chapter_id' in data.data
        ? { resolvedChapterId: data.data.resolved_chapter_id }
        : {}),
    },
  ),
  deleteForeshadowing: (data) => apiDelete(
    `/foreshadowing/${data.id}?bookId=${encodeURIComponent(String(data.bookId))}`,
  ),
  getForeshadowingByBook: (data) =>
    apiGet(`/foreshadowing/by-book?bookId=${data.bookId}${data.status ? `&status=${data.status}` : ''}`),

  createMemory: (data) => apiPost('/memories', data),
  updateMemory: ({ id, ...data }) => apiPut(`/memories/${id}`, data),
  setMemoryState: ({ id, ...data }) => apiPost(`/memories/${id}/state`, data),
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
  linkMemories: (data) => apiPost('/memories/link', data),
  reviewMemory: ({ id, ...data }) => apiPost(`/memories/${encodeURIComponent(id)}/review`, data),
  resolveMemories: (data) => apiPost('/memories/resolve', data),
  deleteMemory: ({ id, ...data }) => apiPost(`/memories/${encodeURIComponent(id)}/delete`, data),
  getMemoryHistory: (data) => apiGet(
    `/memories/${encodeURIComponent(data.id)}/history?bookId=${encodeURIComponent(data.bookId)}`,
  ),
  getMemoryLinks: (data) => {
    const params = new URLSearchParams({ bookId: String(data.bookId) })
    if (data.limit) params.set('limit', String(data.limit))
    if (data.after) params.set('after', data.after)
    return apiGet(`/memories/${encodeURIComponent(data.id)}/links?${params.toString()}`)
  },
  buildMemoryContext: (data) => apiPost('/memories/context', data),

  generateSessionTitle: (data) => apiPost('/ai/title', data),
  getAgentRunSnapshot: (data) => {
    const params = new URLSearchParams()
    if (data.after != null) params.set('after', String(data.after))
    if (data.limit != null) params.set('limit', String(data.limit))
    const query = params.toString()
    return apiGet(`/ai/agent-runs/${encodeURIComponent(data.runId)}${query ? `?${query}` : ''}`)
  },
  getAgentRunPlannerDiagnostics: (data) =>
    apiGet(`/ai/agent-runs/${encodeURIComponent(data.runId)}/planner-diagnostics`),
  getAgentRunModelInputDiagnostics: (data) =>
    apiGet(`/ai/agent-runs/${encodeURIComponent(data.runId)}/model-input-diagnostics`),
  getAgentRunToolDiagnostics: (data) =>
    apiGet(`/ai/agent-runs/${encodeURIComponent(data.runId)}/tool-diagnostics?after=${data.after ?? 0}`),
  getLatestSessionAgentRun: (data) =>
    apiGet(`/ai/session-runs/latest?sessionId=${encodeURIComponent(data.sessionId)}`),
  submitAiErrorReport: (data) =>
    apiPost(`/ai/error-reports/${encodeURIComponent(data.reportId)}/submit`, {
      userNote: data.userNote || null,
    }),
  cancelAgentRun: (data) =>
    apiPost(`/ai/agent-runs/${encodeURIComponent(data.runId)}/cancel`, {}),
  cancelWritingChatRequest: async (data) => {
    const reservation = writingRequestReservations.get(data.requestId)
    if (reservation) {
      const result = await reservation
      if (result.kind !== 'accepted') {
        return { success: true, data: null }
      }
    }
    return sendWritingRequestCancel(data.requestId)
  },
  resolveAiToolApproval: (data) =>
    apiPost(`/ai/tool-approvals/${data.approvalId}`, {
      approved: Boolean(data.approved),
    }),

  aiChatStream: (data) => {
    const streamId = data.streamId || `ai-${Date.now()}-${Math.random().toString(36).slice(2)}`
    const requestData = { ...data }
    delete requestData.streamId
    if (data.chatAgentMode === 'agent') {
      requestData.streamId = streamId
      requestData.requestReceiptVersion = 1
    }
    const abortController = new AbortController()
    aiAbortControllers.set(streamId, abortController)
    latestAiStreamId = streamId
    startAiDebugRun(streamId, data)
    let observedAgentRunId: string | undefined
    let authoritativeAgentRunId: string | undefined
    let observedErrorCode: string | undefined
    let observedTaskType: string | undefined
    let receivedVisibleOutput = false
    let receivedTerminalChunk = false
    let lastCanonicalSequence = 0
    let requestStarted = false
    const durableReservation = (
      data.chatAgentMode === 'agent' && data.sessionId != null
        ? reserveWritingChatRequest({
            requestId: streamId,
            send: (requestId) => sendWritingRequestReservation(
              requestId,
              requestData,
            ),
            wait: () => new Promise<void>((resolve) => {
              globalThis.setTimeout(resolve, 500)
            }),
            isAborted: () => abortController.signal.aborted,
          })
        : undefined
    )
    if (durableReservation) {
      writingRequestReservations.set(streamId, durableReservation)
    }

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

    const deliverChunk = async (chunk: AiChunk): Promise<void> => {
      const transport = chunk as AiChunk & {
        done?: boolean
        aborted?: boolean
        error?: string
        longTaskDispatched?: { taskId?: string; kind?: string }
        runResult?: {
          runId: string
          status: string
          errorCode?: string | null
        }
        requestResult?: AiWritingChatRequestReceipt
      }
      if (abortController.signal.aborted && transport.done) {
        transport.aborted = true
      }
      const receiptBinding = resolveRootRunBinding(
        authoritativeAgentRunId,
        chunk.requestReceipt?.runId,
      )
      const rootRunId = receiptBinding.accepted
        ? receiptBinding.rootRunId
        : authoritativeAgentRunId
      const terminalOwnership = resolveTerminalRootOwnership(
        chunk,
        rootRunId,
        observedAgentRunId,
        streamId,
      )
      if (terminalOwnership.terminal && !terminalOwnership.accepted) return
      if (receiptBinding.accepted) {
        authoritativeAgentRunId = receiptBinding.rootRunId
        observedAgentRunId = receiptBinding.rootRunId
      }
      if (
        terminalOwnership.accepted
        && terminalOwnership.rootRunId
      ) {
        authoritativeAgentRunId = terminalOwnership.rootRunId
        observedAgentRunId = terminalOwnership.rootRunId
      }
      let canonicalBelongsToObservedRoot = false
      if (isCanonicalOutputEvent(chunk)) {
        canonicalBelongsToObservedRoot = Boolean(
          (!authoritativeAgentRunId && !observedAgentRunId)
          || chunk.runId === authoritativeAgentRunId
          || chunk.runId === observedAgentRunId
        )
        if (canonicalBelongsToObservedRoot) {
          observedAgentRunId = chunk.runId || observedAgentRunId
          lastCanonicalSequence = Math.max(lastCanonicalSequence, chunk.sequence)
        }
      }
      if (
        terminalOwnership.accepted
        && terminalOwnership.source === 'runResult'
        && transport.runResult?.runId
      ) {
        observedAgentRunId = transport.runResult.runId
      }
      observedErrorCode =
        (terminalOwnership.accepted
          && terminalOwnership.source === 'runResult'
          ? transport.runResult?.errorCode
          : undefined) ||
        (isCanonicalOutputEvent(chunk)
          && canonicalBelongsToObservedRoot
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
          && canonicalBelongsToObservedRoot
          && chunk.visibility === 'public'
          && chunk.source === 'provider'
          && canonicalProviderTextDelta(chunk).trim())
        || transport.longTaskDispatched?.taskId
      ) {
        receivedVisibleOutput = true
      }
      const acceptedTerminal = terminalOwnership.terminal
        && terminalOwnership.accepted
      const plainTransportTerminal = !terminalOwnership.terminal
        && Boolean(transport.done || transport.error)
      if (acceptedTerminal || plainTransportTerminal) {
        receivedTerminalChunk = true
      }
      if (
        acceptedTerminal
        && terminalOwnership.source === 'runResult'
        && transport.done
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
      } else if (plainTransportTerminal && transport.error) {
        await attachErrorReport(transport)
      } else if (
        (acceptedTerminal || plainTransportTerminal)
        && transport.done
        && !transport.aborted
        && !receivedVisibleOutput
        && transport.finalResponseExpected !== false
        && !transport.requestResult
      ) {
        await attachErrorReport(
          chunk,
          '模型未返回可见内容。',
          'empty_model_response',
        )
      }
      recordAiDebugChunk(streamId, chunk)
      aiChunkListeners.forEach((listener) => listener({ ...chunk, streamId }))
    }

    let recoveryAttempted = false
    const recoverDurableStream = () => {
      recoveryAttempted = true
      return recoverDurableAgentStream({
        runId: observedAgentRunId,
        sessionId: Number(data.sessionId),
        after: lastCanonicalSequence,
        getLatestRun: (sessionId) => apiGet(
          `/ai/session-runs/latest?sessionId=${encodeURIComponent(sessionId)}`
          + `&requestId=${encodeURIComponent(streamId)}`,
        ),
        subscribe: ({ runId, after, onEvent }) => backendApi.consumeAgentRunEvents({
          runId, after, sessionId: Number(data.sessionId),
          signal: abortController.signal, onEvent,
        }),
        wait: ms => waitForAgentRetry(ms, abortController.signal),
        isAborted: () => abortController.signal.aborted,
        emit: deliverChunk,
      })
    }

    Promise.resolve(durableReservation).then(async (reservation) => {
      if (reservation?.kind === 'rejected') {
        await deliverChunk({
          done: true,
          error: reservation.error,
          finalResponseExpected: false,
          requestResult: {
            requestId: streamId,
            sessionId: Number(data.sessionId),
            status: 'rejected',
            runId: null,
            cancelRequested: false,
            rejectionCode: `http_${reservation.status}`,
            revision: 0,
          },
        })
        return undefined
      }
      requestStarted = Boolean(reservation)
      const send = () => fetch(`${backendBaseUrl}/api/ai/chat/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(requestData),
        signal: abortController.signal,
      })
      return reservation
        ? replayWritingChatPostUntilObserved({
            send,
            accept: (response) => response.ok || response.status < 500,
            wait: () => new Promise<void>((resolve) => {
              globalThis.setTimeout(resolve, 500)
            }),
            isAborted: () => abortController.signal.aborted,
          })
        : send()
    }).then(async (response) => {
      if (!response) return
      if (!response.ok || !response.body) {
        throw new Error(`AI 请求失败 (${response.status})`)
      }
      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      const processLines = async (lines: string[]) => {
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          const payload = line.slice(6).trim()
          if (payload === '[DONE]') continue
          try {
            const chunk = JSON.parse(payload) as AiChunk
            await deliverChunk(chunk)
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
        if (
          data.chatAgentMode === 'agent'
          && data.sessionId != null
          && !abortController.signal.aborted
        ) {
          await recoverDurableStream()
        } else {
          await deliverChunk(abortController.signal.aborted
            ? { done: true, aborted: true, streamId }
            : { done: true, streamId })
        }
      }
    }).catch(async (error) => {
      if (
        data.chatAgentMode === 'agent'
        && data.sessionId != null
        && requestStarted
        && !abortController.signal.aborted
      ) {
        try {
          if (recoveryAttempted) throw error
          await recoverDurableStream()
        } catch {
          if (!abortController.signal.aborted) {
            await deliverChunk({
              transportError: '对话连接已中断，后台任务状态未改变。请重新进入此会话恢复连接。',
            })
          }
        }
        return
      }
      const chunk = abortController.signal.aborted
        ? { done: true, aborted: true, streamId }
        : { error: error instanceof Error ? error.message : String(error), streamId }
      await deliverChunk(chunk)
    }).finally(() => {
      if (aiAbortControllers.get(streamId) === abortController) {
        aiAbortControllers.delete(streamId)
      }
      if (latestAiStreamId === streamId) {
        latestAiStreamId = Array.from(aiAbortControllers.keys()).at(-1) || null
      }
      writingRequestReservations.delete(streamId)
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
