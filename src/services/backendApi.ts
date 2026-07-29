import type { ElectronAPI } from '../types'
import { apiDelete, apiGet, apiPost, apiPut, backendBaseUrl } from './httpClient'

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

let aiChunkListeners: Array<(chunk: AiChunk) => void> = []
const aiAbortControllers = new Map<string, AbortController>()
let latestAiStreamId: string | null = null

export const backendApi: BackendApi = {
  getDatabaseInfo: () => apiGet('/database/info'),

  getBooks: () => apiGet('/books'),
  createBook: (data) => apiPost('/books', data),
  deleteBook: (data) => apiDelete(`/books/${data.bookId}`),
  renameBook: (data) => apiPut(`/books/${data.bookId}/rename`, { title: data.title }),
  getBookWordCount: (data) => apiGet(`/books/${data.bookId}/word-count`),

  listScreenplayProjects: (data = {}) =>
    apiGet(`/screenplay-projects${data.includeArchived ? '?includeArchived=true' : ''}`),
  getScreenplayProject: (data) => apiGet(`/screenplay-projects/${data.projectId}`),
  getOrCreateScreenplaySession: (data) =>
    apiPost(`/screenplay-projects/${data.projectId}/agent-session`, {}),
  createScreenplayProject: (data) => apiPost('/screenplay-projects', data),
  updateScreenplayProject: (data) =>
    apiPut(`/screenplay-projects/${data.projectId}`, data.patch || {}),
  deleteScreenplayProject: (data) =>
    apiDelete(`/screenplay-projects/${data.projectId}`),
  listScreenplayDocuments: (data) => {
    const params = new URLSearchParams()
    if (data.kind) params.set('kind', data.kind)
    if (data.status) params.set('status', data.status)
    const query = params.toString()
    return apiGet(`/screenplay-projects/${data.projectId}/documents${query ? `?${query}` : ''}`)
  },
  getScreenplayDocument: (data) =>
    apiGet(`/screenplay-documents/${data.documentId}`),
  createScreenplayDocument: (data) =>
    apiPost(`/screenplay-projects/${data.projectId}/documents`, {
      kind: data.kind,
      title: data.title,
      contentJson: data.contentJson || {},
      contentText: data.contentText || '',
      derivedFromIds: data.derivedFromIds || [],
      sourceRunId: data.sourceRunId || null,
    }),
  listScreenplaySourceRefs: (data) => {
    const params = new URLSearchParams()
    if (data.documentId) params.set('documentId', data.documentId)
    if (data.agentRunId) params.set('agentRunId', data.agentRunId)
    const query = params.toString()
    return apiGet(`/screenplay-projects/${data.projectId}/source-refs${query ? `?${query}` : ''}`)
  },
  updateScreenplayDocument: (data) =>
    apiPut(`/screenplay-documents/${data.documentId}`, data.patch || {}),
  acceptScreenplayDocument: (data) =>
    apiPost(`/screenplay-documents/${data.documentId}/accept`, {}),
  restoreScreenplayDocument: (data) =>
    apiPost(`/screenplay-documents/${data.documentId}/restore`, {}),
  deleteScreenplayDocument: (data) =>
    apiDelete(`/screenplay-documents/${data.documentId}`),

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

  saveConversation: (data) => apiPost('/conversations', data),
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
      const processLines = (lines: string[]) => {
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          const payload = line.slice(6).trim()
          if (payload === '[DONE]') continue
          try {
            const chunk = JSON.parse(payload) as AiChunk
            if (abortController.signal.aborted && chunk.done) chunk.aborted = true
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
        processLines(lines)
      }
      if (buffer.trim()) processLines(buffer.split('\n'))
      const terminalChunk = abortController.signal.aborted
        ? { done: true, aborted: true, streamId }
        : { done: true, streamId }
      aiChunkListeners.forEach((listener) => listener(terminalChunk))
    }).catch((error) => {
      const chunk = abortController.signal.aborted
        ? { done: true, aborted: true, streamId }
        : { error: error instanceof Error ? error.message : String(error), streamId }
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
