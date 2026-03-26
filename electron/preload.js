const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('electronAPI', {
  // 文件操作
  openXmindFile: () => ipcRenderer.invoke('open-xmind-file'),
  parseXmind: (filePath) => ipcRenderer.invoke('parse-xmind', filePath),
  openFilePath: (filePath) => ipcRenderer.invoke('open-file-path', filePath),
  readFileBuffer: (filePath) => ipcRenderer.invoke('read-file-buffer', filePath),
  writeExportFiles: (data) => ipcRenderer.invoke('write-export-files', data),
  exportDatabase: () => ipcRenderer.invoke('export-database'),
  importDatabase: () => ipcRenderer.invoke('import-database'),
  getDatabaseInfo: () => ipcRenderer.invoke('db-get-database-info'),
  openDatabaseDirectory: () => ipcRenderer.invoke('open-database-directory'),

  // 书籍
  getBooks: () => ipcRenderer.invoke('db-get-books'),
  createBook: (data) => ipcRenderer.invoke('db-create-book', data),
  deleteBook: (data) => ipcRenderer.invoke('db-delete-book', data),
  renameBook: (data) => ipcRenderer.invoke('db-rename-book', data),

  // 人物
  getCharacters: (data) => ipcRenderer.invoke('db-get-characters', data),
  createCharacter: (data) => ipcRenderer.invoke('db-create-character', data),
  updateCharacter: (data) => ipcRenderer.invoke('db-update-character', data),
  deleteCharacter: (data) => ipcRenderer.invoke('db-delete-character', data),

  // 人物选项（性格 / 标签候选值）
  getCharacterOptions: (data) => ipcRenderer.invoke('db-get-character-options', data),
  addCharacterOption: (data) => ipcRenderer.invoke('db-add-character-option', data),
  updateCharacterOption: (data) => ipcRenderer.invoke('db-update-character-option', data),
  deleteCharacterOption: (data) => ipcRenderer.invoke('db-delete-character-option', data),

  // 大纲（支持 bookId 作用域）
  saveOutline: (data) => ipcRenderer.invoke('db-save-outline', data),
  getOutlines: (typeFilter) => ipcRenderer.invoke('db-get-outlines', typeFilter),
  getVolumeOutlines: (bookId) => ipcRenderer.invoke('db-get-volume-outlines', bookId),
  getOutlineByWritingChapter: (id) => ipcRenderer.invoke('db-get-outline-by-writing-chapter', id),
  getGlobalOutline: (bookId) => ipcRenderer.invoke('db-get-global-outline', bookId),
  ensureGlobalOutline: (bookId) => ipcRenderer.invoke('db-ensure-global-outline', bookId),
  getWritingOutline: (bookId) => ipcRenderer.invoke('db-get-writing-outline', bookId),
  getChapterOutlines: (bookId) => ipcRenderer.invoke('db-get-chapter-outlines', bookId),
  getOtherOutlines: (bookId) => ipcRenderer.invoke('db-get-other-outlines', bookId),
  deleteOutline: (data) => ipcRenderer.invoke('db-delete-outline', data),
  updateOutline: (data) => ipcRenderer.invoke('db-update-outline', data),

  // 章节
  saveChapters: (data) => ipcRenderer.invoke('db-save-chapters', data),
  getChapters: (data) => ipcRenderer.invoke('db-get-chapters', data),
  addChapter: (data) => ipcRenderer.invoke('db-add-chapter', data),
  deleteChapter: (data) => ipcRenderer.invoke('db-delete-chapter', data),
  renameChapter: (data) => ipcRenderer.invoke('db-rename-chapter', data),
  updateChapterProgress: (data) => ipcRenderer.invoke('db-update-chapter-progress', data),

  // 文档
  saveArticle: (data) => ipcRenderer.invoke('db-save-article', data),
  getArticle: (data) => ipcRenderer.invoke('db-get-article', data),
  getStoryBackground: (data) => ipcRenderer.invoke('db-get-story-background', data),
  saveStoryBackground: (data) => ipcRenderer.invoke('db-save-story-background', data),
  openAndReadTextFile: () => ipcRenderer.invoke('open-and-read-text-file'),
  pickStoryBackgroundAttachments: (data) => ipcRenderer.invoke('story-background-pick-attachments', data),
  getStoryBackgroundAttachments: (data) => ipcRenderer.invoke('db-get-story-background-attachments', data),
  deleteStoryBackgroundAttachment: (data) => ipcRenderer.invoke('db-delete-story-background-attachment', data),
  openStoryBackgroundAttachment: (data) => ipcRenderer.invoke('story-background-open-attachment', data),

  // AI 会话
  createSession: (data) => ipcRenderer.invoke('db-create-session', data),
  getSessions: (data) => ipcRenderer.invoke('db-get-sessions', data),
  setSessionClosed: (data) => ipcRenderer.invoke('db-set-session-closed', data),
  setSessionReopened: (data) => ipcRenderer.invoke('db-set-session-reopened', data),
  deleteSession: (data) => ipcRenderer.invoke('db-delete-session', data),
  saveConversation: (data) => ipcRenderer.invoke('db-save-conversation', data),
  getConversations: (data) => ipcRenderer.invoke('db-get-conversations', data),
  deleteConversationsAfterTurn: (data) => ipcRenderer.invoke('db-delete-conversations-after-turn', data),
  updateSessionTitle: (data) => ipcRenderer.invoke('db-update-session-title', data),
  saveAiFavorite: (data) => ipcRenderer.invoke('db-save-ai-favorite', data),
  getAiFavorites: () => ipcRenderer.invoke('db-get-ai-favorites'),
  deleteAiFavorite: (data) => ipcRenderer.invoke('db-delete-ai-favorite', data),
  // 长期记忆（五层）
  addMemory: (data) => ipcRenderer.invoke('db-add-memory', data),
  updateMemory: (data) => ipcRenderer.invoke('db-update-memory', data),
  deleteMemory: (data) => ipcRenderer.invoke('db-delete-memory', data),
  getMemoriesByBook: (data) => ipcRenderer.invoke('db-get-memories-by-book', data),
  getMemoriesByIds: (data) => ipcRenderer.invoke('db-get-memories-by-ids', data),
  getMemoriesForPrompt: (data) => ipcRenderer.invoke('db-get-memories-for-prompt', data),
  // 伏笔记忆
  addForeshadowing: (data) => ipcRenderer.invoke('db-add-foreshadowing', data),
  updateForeshadowing: (data) => ipcRenderer.invoke('db-update-foreshadowing', data),
  deleteForeshadowing: (data) => ipcRenderer.invoke('db-delete-foreshadowing', data),
  getForeshadowingByBook: (data) => ipcRenderer.invoke('db-get-foreshadowing-by-book', data),
  getForeshadowingByIds: (data) => ipcRenderer.invoke('db-get-foreshadowing-by-ids', data),
  getForeshadowingForPrompt: (data) => ipcRenderer.invoke('db-get-foreshadowing-for-prompt', data),
  generateSessionTitle: (data) => ipcRenderer.invoke('ai-generate-title', data),
  listModels: (data) => ipcRenderer.invoke('ai-list-models', data),

  // AI 调用（流式）
  aiChatStream: (data) => ipcRenderer.send('ai-chat-stream', data),
  abortAiStream: () => ipcRenderer.send('ai-abort-stream'),
  onAiChunk: (callback) => {
    const handler = (_, chunk) => callback(chunk)
    ipcRenderer.on('ai-chat-chunk', handler)
    return () => ipcRenderer.removeListener('ai-chat-chunk', handler)
  },
  debugLog: (payload) => ipcRenderer.send('debug-log', payload),

  // 通用配置
  getSettings: () => ipcRenderer.invoke('db-get-settings'),
  setSettings: (data) => ipcRenderer.invoke('db-set-settings', data),
})
