const { contextBridge, ipcRenderer } = require('electron')

const BACKEND_URL = 'http://127.0.0.1:18321'

// Module-level callback registry for AI stream chunks.
// ipcRenderer.emit/on does not work in sandboxed Electron preloads;
// this module-level array is the reliable alternative.
let _aiChunkListeners = []

async function apiFetch(path, options = {}, retries = 3) {
  for (let attempt = 0; attempt <= retries; attempt++) {
    try {
      const res = await fetch(`${BACKEND_URL}${path}`, {
        headers: { 'Content-Type': 'application/json', ...options.headers },
        ...options,
      })
      if (!res.ok && attempt < retries) {
        await new Promise((r) => setTimeout(r, 300 * (attempt + 1)))
        continue
      }
      return await res.json()
    } catch (err) {
      if (attempt >= retries) return { success: false, error: err.message }
      await new Promise((r) => setTimeout(r, 300 * (attempt + 1)))
    }
  }
}

async function apiGet(path) {
  return apiFetch(`/api${path}`)
}

async function apiPost(path, body) {
  return apiFetch(`/api${path}`, { method: 'POST', body: JSON.stringify(body) })
}

async function apiPut(path, body) {
  return apiFetch(`/api${path}`, { method: 'PUT', body: JSON.stringify(body) })
}

async function apiDelete(path) {
  return apiFetch(`/api${path}`, { method: 'DELETE' })
}

let _activeAiAbortController = null

contextBridge.exposeInMainWorld('electronAPI', {
  // ─── File operations — IPC (need Electron native) ──────────────
  openXmindFile: () => ipcRenderer.invoke('open-xmind-file'),
  parseXmind: (filePath) => ipcRenderer.invoke('parse-xmind', filePath),
  openFilePath: (filePath) => ipcRenderer.invoke('open-file-path', filePath),
  readFileBuffer: (filePath) => ipcRenderer.invoke('read-file-buffer', filePath),
  writeExportFiles: (data) => ipcRenderer.invoke('write-export-files', data),
  writeSingleTextFile: (data) => ipcRenderer.invoke('write-single-text-file', data),
  exportEpub: (data) => ipcRenderer.invoke('export-epub', data),
  exportDatabase: () => ipcRenderer.invoke('export-database'),
  importDatabase: () => ipcRenderer.invoke('import-database'),
  getDatabaseInfo: () => apiGet('/database/info'),
  openDatabaseDirectory: () => ipcRenderer.invoke('open-database-directory'),
  openAndReadTextFile: () => ipcRenderer.invoke('open-and-read-text-file'),
  pickStoryBackgroundAttachments: (data) => ipcRenderer.invoke('story-background-pick-attachments', data),
  openStoryBackgroundAttachment: (data) => ipcRenderer.invoke('story-background-open-attachment', data),

  // ─── Books — HTTP ──────────────────────────────────────────────
  getBooks: () => apiGet('/books'),
  createBook: (data) => apiPost('/books', data),
  deleteBook: (data) => apiDelete(`/books/${data.bookId}`),
  renameBook: (data) => apiPut(`/books/${data.bookId}/rename`, { title: data.title }),
  getBookWordCount: (data) => apiGet(`/books/${data.bookId}/word-count`),

  // ─── Characters — HTTP ─────────────────────────────────────────
  getCharacters: (data) => apiGet(`/books/${data.bookId}/characters`),
  createCharacter: (data) => apiPost(`/books/${data.bookId}/characters`, { data: data.data }),
  updateCharacter: (data) => apiPut(`/characters/${data.id}`, { data: data.data }),
  deleteCharacter: (data) => apiDelete(`/characters/${data.id}`),

  // ─── Setting entities（世界设定实体）— HTTP ─────────────────────
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

  // ─── Character options — HTTP ──────────────────────────────────
  getCharacterOptions: (data) => apiGet(`/character-options?category=${data.category}`),
  addCharacterOption: (data) => apiPost('/character-options', data),
  updateCharacterOption: (data) => apiPut(`/character-options/${data.id}`, { value: data.value }),
  deleteCharacterOption: (data) => apiDelete(`/character-options/${data.id}`),

  // ─── Outlines — HTTP ───────────────────────────────────────────
  saveOutline: (data) => apiPost('/outlines', data),
  getOutlines: (typeFilter) => apiGet(`/outlines${typeFilter ? '?type=' + typeFilter : ''}`),
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
  listOutlineHistory: (data) => apiGet(`/outlines/${data.outlineId}/history${data && data.limit ? `?limit=${data.limit}` : ''}`),
  getOutlineHistory: (data) => apiGet(`/outlines/history/${data.historyId}`),
  restoreOutlineHistory: (data) => apiPost(`/outlines/history/${data.historyId}/restore`, {}),

  // ─── Chapters — HTTP ───────────────────────────────────────────
  saveChapters: (data) => apiPost(`/chapters/${data.outlineId}`, { chapters: data.chapters }),
  getChapters: (data) => apiGet(`/chapters/${data.outlineId}`),
  addChapter: (data) => apiPost(`/chapters/${data.outlineId}/add`, data),
  deleteChapter: (data) => apiDelete(`/chapters/${data.chapterId || data.id}`),
  renameChapter: (data) => apiPut(`/chapters/${data.chapterId || data.id}/rename`, { title: data.title }),
  updateChapterProgress: (data) => apiPut(`/chapters/${data.chapterId}/progress`, { progress: data.progress }),

  // ─── Articles — HTTP ───────────────────────────────────────────
  saveArticle: (data) => apiPut(`/articles/${data.chapterId}`, { content: data.content, source: data.source }),
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
    const statuses = Array.isArray(data.statuses) ? data.statuses : []
    const query = statuses.map((value) => `status=${encodeURIComponent(value)}`).join('&')
    return apiGet(`/books/${data.bookId}/story-memory/evolution-reviews${query ? `?${query}` : ''}`)
  },
  resolveStoryMemoryEvolutionReview: (data) =>
    apiPost(`/story-memory/deltas/${data.deltaId}/evolution-review/resolve`, {
      resolutions: data.resolutions,
    }),

  // ─── Story background — HTTP ───────────────────────────────────
  getStoryBackground: (data) => apiGet(`/story-background/${data.bookId}`),
  saveStoryBackground: (data) => apiPut(`/story-background/${data.bookId}`, { content: data.content }),
  getStoryBackgroundAttachments: (data) => apiGet(`/story-background/${data.bookId}/attachments`),
  deleteStoryBackgroundAttachment: (data) => apiDelete(`/story-background/attachments/${data.id}`),

  // ─── Book style — HTTP ─────────────────────────────────────────
  getBookStyle: (data) => apiGet(`/book-style/${data.bookId}`),
  saveBookStyle: (data) => apiPut(`/book-style/${data.bookId}`, {
    pov: data.pov || '',
    tone: data.tone || '',
    pace: data.pace || '',
    banned_rules: data.banned_rules || '',
    reference_chapter_ids: data.reference_chapter_ids || '',
    free_notes: data.free_notes || '',
  }),

  // ─── Chapter diff history — HTTP ───────────────────────────────
  commitChapterDiff: (data) => apiPost(`/chapter-diff/${data.chapterId}/commit`, {
    content: data.content,
    before_text: data.beforeText || '',
    after_text: data.afterText || '',
    source: data.source || 'ai_rewrite',
    accepted_segments: data.acceptedSegments || 0,
    rejected_segments: data.rejectedSegments || 0,
  }),
  listChapterDiff: (data) => apiGet(`/chapter-diff/${data.chapterId}?limit=${data.limit ?? 50}`),
  getChapterDiff: (data) => apiGet(`/chapter-diff/by-id/${data.diffId}`),
  rollbackChapterDiff: (data) => apiPost(`/chapter-diff/by-id/${data.diffId}/rollback`, {}),

  // ─── Setting diff history — HTTP ───────────────────────────────
  commitCharacterSettingDiff: (data) => apiPost(`/setting-diff/character/${data.characterId}/commit`, {
    name: data.name,
    tags: data.tags,
    profileMd: data.profileMd,
    before: data.before,
    after: data.after,
    source: data.source || 'ai_tool',
    accepted_segments: data.acceptedSegments || 0,
    rejected_segments: data.rejectedSegments || 0,
  }),
  commitBackgroundSettingDiff: (data) => apiPost(`/setting-diff/background/${data.bookId}/commit`, {
    content: data.content,
    before_content: data.beforeContent || '',
    after_content: data.afterContent || '',
    source: data.source || 'ai_tool',
    accepted_segments: data.acceptedSegments || 0,
    rejected_segments: data.rejectedSegments || 0,
  }),
  listCharacterSettingHistory: (data) =>
    apiGet(`/setting-diff/character/${data.characterId}/history?limit=${data.limit ?? 50}`),
  getCharacterSettingHistory: (data) => apiGet(`/setting-diff/character/history/${data.historyId}`),
  rollbackCharacterSettingHistory: (data) =>
    apiPost(`/setting-diff/character/history/${data.historyId}/rollback`, {}),
  listBackgroundSettingHistory: (data) =>
    apiGet(`/setting-diff/background/${data.bookId}/history?limit=${data.limit ?? 50}`),
  getBackgroundSettingHistory: (data) => apiGet(`/setting-diff/background/history/${data.historyId}`),
  rollbackBackgroundSettingHistory: (data) =>
    apiPost(`/setting-diff/background/history/${data.historyId}/rollback`, {}),
  commitEntitySettingDiff: (data) => apiPost(`/setting-diff/entity/${data.entityId}/commit`, {
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
  getEntitySettingHistory: (data) => apiGet(`/setting-diff/entity/history/${data.historyId}`),
  rollbackEntitySettingHistory: (data) =>
    apiPost(`/setting-diff/entity/history/${data.historyId}/rollback`, {}),

  // ─── Sessions — HTTP ───────────────────────────────────────────
  createSession: (data) => apiPost('/sessions', data),
  getSessions: (data) => {
    const params = new URLSearchParams()
    if (data.bookId != null) params.set('bookId', data.bookId)
    if (data.chapterId != null) params.set('chapterId', data.chapterId)
    if (data.includeClosed) params.set('includeClosed', 'true')
    if (data.scope) params.set('scope', data.scope)
    return apiGet(`/sessions?${params}`)
  },
  setSessionClosed: (data) => apiPut(`/sessions/${data.sessionId}/close`, {}),
  setSessionReopened: (data) => apiPut(`/sessions/${data.sessionId}/reopen`, {}),
  deleteSession: (data) => apiDelete(`/sessions/${data.sessionId}`),
  updateSessionTitle: (data) => apiPut(`/sessions/${data.sessionId}/title`, { title: data.title }),

  // ─── Conversations — HTTP ──────────────────────────────────────
  saveConversation: (data) => apiPost('/conversations', data),
  getConversations: (data) => apiGet(`/conversations/${data.sessionId}`),
  deleteConversationsAfterTurn: (data) =>
    apiDelete(`/conversations/${data.sessionId}/after-turn?keepTurnCount=${data.keepTurnCount}`),

  // ─── AI favorites — HTTP ───────────────────────────────────────
  saveAiFavorite: (data) => apiPost('/ai-favorites', data),
  getAiFavorites: () => apiGet('/ai-favorites'),
  deleteAiFavorite: (data) => apiDelete(`/ai-favorites/${data.id}`),

  // ─── Prompt templates — HTTP ───────────────────────────────────
  listPromptTemplates: () => apiGet('/prompt-templates'),
  createPromptTemplate: (data) => apiPost('/prompt-templates', data),
  updatePromptTemplate: (data) => apiPut(`/prompt-templates/${data.id}`, data.data ?? {}),
  deletePromptTemplate: (data) => apiDelete(`/prompt-templates/${data.id}`),
  reorderPromptTemplates: (data) => apiPost('/prompt-templates/reorder', { ids: data.ids }),

  // ─── Spark ideas — HTTP ────────────────────────────────────────
  addSparkIdea: (data) => apiPost('/spark-ideas', data),
  updateSparkIdea: (data) => apiPut(`/spark-ideas/${data.id}`, { data: data.data }),
  deleteSparkIdea: (data) => apiDelete(`/spark-ideas/${data.id}`),
  getSparkIdeasByBook: (data) =>
    apiGet(`/spark-ideas/by-book?bookId=${data.bookId}${data.layer ? '&layer=' + data.layer : ''}`),
  getSparkIdeasByIds: (data) => apiPost('/spark-ideas/by-ids', data),
  getSparkIdeasForPrompt: (data) => apiPost('/spark-ideas/for-prompt', data),

  // ─── Foreshadowing — HTTP ──────────────────────────────────────
  addForeshadowing: (data) => apiPost('/foreshadowing', data),
  updateForeshadowing: (data) => apiPut(`/foreshadowing/${data.id}`, { data: data.data }),
  deleteForeshadowing: (data) => apiDelete(`/foreshadowing/${data.id}`),
  getForeshadowingByBook: (data) =>
    apiGet(`/foreshadowing/by-book?bookId=${data.bookId}${data.status ? '&status=' + data.status : ''}`),
  getForeshadowingByIds: (data) => apiPost('/foreshadowing/by-ids', data),
  getForeshadowingForPrompt: (data) => apiPost('/foreshadowing/for-prompt', data),

  // ─── Long-term memories — HTTP ─────────────────────────────────
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

  // ─── AI — HTTP ─────────────────────────────────────────────────
  generateSessionTitle: (data) => apiPost('/ai/title', data),
  listModels: (data) => apiPost('/ai/models', data),
  getAgentRunSnapshot: (data) => {
    const params = new URLSearchParams()
    if (data.after != null) params.set('after', String(data.after))
    if (data.limit != null) params.set('limit', String(data.limit))
    const query = params.toString()
    return apiGet(
      `/ai/agent-runs/${encodeURIComponent(data.runId)}${query ? `?${query}` : ''}`,
    )
  },
  cancelAgentRun: (data) => apiPost(
    `/ai/agent-runs/${encodeURIComponent(data.runId)}/cancel`,
    {},
  ),
  createAgentDelegation: (data) => apiPost(
    `/ai/agent-runs/${encodeURIComponent(data.runId)}/delegations`,
    {
      agentRole: data.agentRole,
      objective: data.objective,
      input: data.input || {},
      required: data.required !== false,
      priority: data.priority || 0,
    },
  ),
  resolveAiToolApproval: (data) => apiPost(`/ai/tool-approvals/${data.approvalId}`, {
    approved: Boolean(data.approved),
  }),

  // ─── AI streaming — fetch + ReadableStream SSE ─────────────────
  aiChatStream: (data) => {
    const abortController = new AbortController()
    _activeAiAbortController = abortController

    fetch(`${BACKEND_URL}/api/ai/chat/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
      signal: abortController.signal,
    })
      .then(async (response) => {
        const reader = response.body.getReader()
        const decoder = new TextDecoder()
        let buffer = ''
        const _processLines = (lines) => {
          for (const line of lines) {
            if (line.startsWith('data: ')) {
              const payload = line.slice(6).trim()
              if (payload === '[DONE]') continue
              try {
                const chunk = JSON.parse(payload)
                _aiChunkListeners.forEach(cb => { try { cb(chunk) } catch (_e) {} })
              } catch {}
            }
          }
        }
        while (true) {
          const { done, value } = await reader.read()
          if (done) break
          buffer += decoder.decode(value, { stream: true })
          const lines = buffer.split('\n')
          buffer = lines.pop() || ''
          _processLines(lines)
        }
        // Flush any remaining data left in the buffer (last SSE event
        // may lack a trailing newline when the connection closes).
        if (buffer.trim()) {
          _processLines(buffer.split('\n'))
        }
        _aiChunkListeners.forEach(cb => { try { cb({ done: true }) } catch (_e) {} })
      })
      .catch((err) => {
        if (abortController.signal.aborted) {
          _aiChunkListeners.forEach(cb => { try { cb({ done: true, aborted: true }) } catch (_e) {} })
        } else {
          _aiChunkListeners.forEach(cb => { try { cb({ error: err.message }) } catch (_e) {} })
        }
      })
      .finally(() => {
        if (_activeAiAbortController === abortController) {
          _activeAiAbortController = null
        }
      })
  },

  abortAiStream: () => {
    if (_activeAiAbortController) {
      _activeAiAbortController.abort()
      _activeAiAbortController = null
    }
  },

  onAiChunk: (callback) => {
    const handler = (chunk) => callback(chunk)
    _aiChunkListeners.push(handler)
    return () => { _aiChunkListeners = _aiChunkListeners.filter(h => h !== handler) }
  },

  debugLog: (payload) => {
    fetch(`${BACKEND_URL}/api/debug-log`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }).catch(() => {})
  },

  // ─── Settings — HTTP ───────────────────────────────────────────
  getSettings: () => apiGet('/settings'),
  setSettings: (data) => apiPut('/settings', { data }),

  // ─── Dashboard（故事健康度 / 写作统计）— HTTP ───────────────────
  getStoryHealth: (data) => apiGet(`/dashboard/health?bookId=${data.bookId}`),
  getWritingStats: (data) => apiGet(`/dashboard/writing-stats?bookId=${data.bookId}`),
  setWritingGoal: (data) => apiPost('/dashboard/writing-goal', {
    bookId: String(data.bookId),
    dailyWords: data.dailyWords || 0,
  }),
})
