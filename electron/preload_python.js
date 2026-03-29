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
  getGlobalOutline: (bookId) => apiGet(`/outlines/global/${bookId}`),
  ensureGlobalOutline: (bookId) => apiPost(`/outlines/global/${bookId}/ensure`, {}),
  getWritingOutline: (bookId) => apiGet(`/outlines/writing/${bookId}`),
  getChapterOutlines: (bookId) => apiGet(`/outlines/chapter/${bookId}`),
  getAssociableOutlines: (bookId) => apiGet(`/outlines/associable/${bookId}`),
  deleteOutline: (data) => apiDelete(`/outlines/${data.outlineId}`),
  updateOutline: (data) => apiPut(`/outlines/${data.outlineId}`, data),

  // ─── Chapters — HTTP ───────────────────────────────────────────
  saveChapters: (data) => apiPost(`/chapters/${data.outlineId}`, { chapters: data.chapters }),
  getChapters: (data) => apiGet(`/chapters/${data.outlineId}`),
  addChapter: (data) => apiPost(`/chapters/${data.outlineId}/add`, data),
  deleteChapter: (data) => apiDelete(`/chapters/${data.chapterId || data.id}`),
  renameChapter: (data) => apiPut(`/chapters/${data.chapterId || data.id}/rename`, { title: data.title }),
  updateChapterProgress: (data) => apiPut(`/chapters/${data.chapterId}/progress`, { progress: data.progress }),

  // ─── Articles — HTTP ───────────────────────────────────────────
  saveArticle: (data) => apiPut(`/articles/${data.chapterId}`, { content: data.content }),
  getArticle: (data) => apiGet(`/articles/${data.chapterId}`),

  // ─── Story background — HTTP ───────────────────────────────────
  getStoryBackground: (data) => apiGet(`/story-background/${data.bookId}`),
  saveStoryBackground: (data) => apiPut(`/story-background/${data.bookId}`, { content: data.content }),
  getStoryBackgroundAttachments: (data) => apiGet(`/story-background/${data.bookId}/attachments`),
  deleteStoryBackgroundAttachment: (data) => apiDelete(`/story-background/attachments/${data.id}`),

  // ─── Sessions — HTTP ───────────────────────────────────────────
  createSession: (data) => apiPost('/sessions', data),
  getSessions: (data) => {
    const params = new URLSearchParams()
    if (data.bookId != null) params.set('bookId', data.bookId)
    if (data.chapterId != null) params.set('chapterId', data.chapterId)
    if (data.includeClosed) params.set('includeClosed', 'true')
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

  // ─── AI — HTTP ─────────────────────────────────────────────────
  generateSessionTitle: (data) => apiPost('/ai/title', data),
  listModels: (data) => apiPost('/ai/models', data),

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
        while (true) {
          const { done, value } = await reader.read()
          if (done) break
          buffer += decoder.decode(value, { stream: true })
          const lines = buffer.split('\n')
          buffer = lines.pop() || ''
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
})
