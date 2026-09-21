const { contextBridge, ipcRenderer } = require('electron')

// Electron exposes only operating-system capabilities. Business data, settings,
// AI streaming, and all other application services are called through the
// renderer's standard HTTP/SSE service layer.
contextBridge.exposeInMainWorld('purrDesktop', {
  showNotification: (data) => ipcRenderer.invoke('show-agent-notification', data),
  refreshAgentPowerSaveState: () => ipcRenderer.invoke('refresh-agent-power-save-state'),
  selectNovelKnowledge: (bookId) => ipcRenderer.invoke('novel-knowledge-select', bookId),
  openNovelKnowledge: (args) => ipcRenderer.invoke('novel-knowledge-open', args),
  openNovelKnowledgeLibrary: (bookId) => ipcRenderer.invoke('novel-knowledge-open-library', bookId),
  openFilePath: (filePath) => ipcRenderer.invoke('open-file-path', filePath),
  writeExportFiles: (data) => ipcRenderer.invoke('write-export-files', data),
  writeSingleTextFile: (data) => ipcRenderer.invoke('write-single-text-file', data),
  writeScreenplayFile: (data) => ipcRenderer.invoke('write-screenplay-file', data),
  exportScreenplayPdf: (data) => ipcRenderer.invoke('export-screenplay-pdf', data),
  exportEpub: (data) => ipcRenderer.invoke('export-epub', data),
  exportDatabase: () => ipcRenderer.invoke('export-database'),
  importDatabase: () => ipcRenderer.invoke('import-database'),
  openDatabaseDirectory: () => ipcRenderer.invoke('open-database-directory'),
  openAndReadTextFile: () => ipcRenderer.invoke('open-and-read-text-file'),
  pickNovelSourceTextFile: (options) => ipcRenderer.invoke('pick-novel-source-text-file', options),
  pickStoryBackgroundAttachments: (data) =>
    ipcRenderer.invoke('story-background-pick-attachments', data),
  openStoryBackgroundAttachment: (data) =>
    ipcRenderer.invoke('story-background-open-attachment', data),
})
