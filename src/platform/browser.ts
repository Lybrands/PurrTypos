import JSZip from 'jszip'
import type { ApiResult, ElectronAPI, StoryBackgroundAttachment, XmindSheet } from '../types'
import { backendBaseUrl } from '../services/httpClient'
import type { PlatformApi } from './types'

const selectedFiles = new Map<string, File>()

function failure<T>(error: string): ApiResult<T> {
  return { success: false, data: undefined as T, error }
}

function chooseFiles(accept = '', multiple = false): Promise<File[]> {
  return new Promise((resolve) => {
    const input = document.createElement('input')
    input.type = 'file'
    input.accept = accept
    input.multiple = multiple
    input.style.display = 'none'
    document.body.appendChild(input)
    let settled = false
    const finish = (files: File[]) => {
      if (settled) return
      settled = true
      input.remove()
      resolve(files)
    }
    input.addEventListener('change', () => finish(Array.from(input.files || [])), { once: true })
    window.addEventListener('focus', () => {
      window.setTimeout(() => {
        if (!input.files?.length) finish([])
      }, 300)
    }, { once: true })
    input.click()
  })
}

function safeName(name: string) {
  return String(name || 'download').replace(/[/\\:*?"<>|]/g, '_')
}

function downloadBlob(blob: Blob, fileName: string) {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = safeName(fileName)
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 1_000)
}

async function downloadBackendFile(
  path: string,
  options: RequestInit,
  fileName: string,
): Promise<ApiResult<{ path: string }>> {
  try {
    const response = await fetch(`${backendBaseUrl}${path}`, options)
    const contentType = response.headers.get('content-type') || ''
    if (!response.ok || contentType.includes('application/json')) {
      const body = await response.json().catch(() => ({})) as { error?: string; detail?: string }
      return failure(body.error || body.detail || '导出失败')
    }
    downloadBlob(await response.blob(), fileName)
    return { success: true, data: { path: fileName } }
  } catch (error) {
    return failure(error instanceof Error ? error.message : String(error))
  }
}

export const browserPlatformApi: PlatformApi = {
  openXmindFile: async () => {
    const [file] = await chooseFiles('.xmind')
    if (!file) return null
    const token = `browser-file://${crypto.randomUUID()}/${file.name}`
    selectedFiles.set(token, file)
    return token
  },

  parseXmind: async (filePath) => {
    const file = selectedFiles.get(filePath)
    if (!file) return failure<XmindSheet[]>('浏览器文件授权已失效，请重新选择文件')
    try {
      const zip = await JSZip.loadAsync(await file.arrayBuffer())
      const contentEntry = zip.file('content.json')
      if (!contentEntry) return failure<XmindSheet[]>('XMind 文件中不存在 content.json')
      const content = JSON.parse(await contentEntry.async('text')) as XmindSheet[]
      return { success: true, data: content }
    } catch (error) {
      return failure(error instanceof Error ? error.message : String(error))
    }
  },

  readFileBuffer: async (filePath) => {
    const file = selectedFiles.get(filePath)
    if (!file) return failure<Uint8Array>('浏览器文件授权已失效，请重新选择文件')
    return { success: true, data: new Uint8Array(await file.arrayBuffer()) }
  },

  openFilePath: async () => failure<void>('浏览器不能直接打开本地路径，请使用下载或上传功能'),

  writeExportFiles: async ({ entries, exportAsZip }) => {
    if (!entries.length) return failure<void>('没有可导出的内容')
    if (!exportAsZip && entries.length === 1) {
      downloadBlob(new Blob([entries[0].content], { type: 'text/plain;charset=utf-8' }), entries[0].path)
      return { success: true, data: undefined }
    }
    const zip = new JSZip()
    entries.forEach((entry) => zip.file(entry.path, entry.content || ''))
    downloadBlob(await zip.generateAsync({ type: 'blob' }), '书籍导出.zip')
    return { success: true, data: undefined }
  },

  writeSingleTextFile: async ({ defaultName, content }) => {
    downloadBlob(new Blob([content], { type: 'text/plain;charset=utf-8' }), defaultName)
    return { success: true, data: { path: defaultName } }
  },

  writeScreenplayFile: async ({ defaultName, content, format }) => {
    const extension = format === 'markdown' ? 'md' : format
    const fileName = defaultName.toLowerCase().endsWith(`.${extension}`)
      ? defaultName
      : `${defaultName}.${extension}`
    downloadBlob(new Blob([content], { type: 'text/plain;charset=utf-8' }), fileName)
    return { success: true, data: { path: fileName } }
  },

  exportScreenplayPdf: ({ projectId, defaultName }) =>
    downloadBackendFile(
      `/api/screenplay-projects/${encodeURIComponent(projectId)}/export/pdf`,
      { method: 'POST' },
      `${defaultName.replace(/\.pdf$/i, '')}.pdf`,
    ),

  exportEpub: ({ bookId, chapterIds, defaultName }) =>
    downloadBackendFile(
      '/api/export/epub',
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ bookId, chapterIds: chapterIds || null }),
      },
      `${defaultName || '书籍'}.epub`,
    ),

  exportDatabase: async () => {
    const result = await downloadBackendFile(
      '/api/database/export',
      { method: 'POST' },
      `purrtypos-backup-${new Date().toISOString().slice(0, 10)}.db`,
    )
    return result.success
      ? { success: true, data: undefined }
      : { success: false, data: undefined, error: result.error }
  },

  importDatabase: async () => {
    const [file] = await chooseFiles('.db,application/x-sqlite3')
    if (!file) return failure('canceled')
    try {
      const response = await fetch(
        `${backendBaseUrl}/api/database/import?fileName=${encodeURIComponent(file.name)}`,
        {
        method: 'POST',
        headers: { 'Content-Type': 'application/octet-stream' },
        body: file,
        },
      )
      return await response.json()
    } catch (error) {
      return failure(error instanceof Error ? error.message : String(error))
    }
  },

  openDatabaseDirectory: async () =>
    failure<void>('浏览器无法打开数据库目录，可以使用“导出数据库”下载备份'),

  openAndReadTextFile: async () => {
    const [file] = await chooseFiles('.txt,.md,.markdown,text/plain,text/markdown')
    if (!file) return failure<string>('canceled')
    try {
      return { success: true, data: await file.text() }
    } catch (error) {
      return failure(error instanceof Error ? error.message : String(error))
    }
  },

  pickNovelSourceTextFile: async () => {
    const [file] = await chooseFiles('.txt,.md,.markdown,text/plain,text/markdown')
    if (!file) return failure('canceled')
    const extension = (`.${file.name.split('.').pop() || ''}`).toLowerCase()
    if (!['.txt', '.md', '.markdown'].includes(extension)) {
      return failure('首版只支持 TXT、Markdown 文件')
    }
    try {
      return {
        success: true,
        data: {
          fileName: file.name,
          extension: extension as '.txt' | '.md' | '.markdown',
          byteCount: file.size,
          content: await file.text(),
        },
      }
    } catch (error) {
      return failure(error instanceof Error ? error.message : String(error))
    }
  },

  pickStoryBackgroundAttachments: async ({ bookId }) => {
    const files = await chooseFiles('', true)
    if (!files.length) return failure<StoryBackgroundAttachment[]>('canceled')
    try {
      let result: ApiResult<StoryBackgroundAttachment[]> = failure('上传失败')
      for (const file of files) {
        const response = await fetch(
          `${backendBaseUrl}/api/story-background/${encodeURIComponent(bookId)}/attachments/upload?fileName=${encodeURIComponent(file.name)}`,
          {
            method: 'POST',
            headers: { 'Content-Type': 'application/octet-stream' },
            body: file,
          },
        )
        result = await response.json()
        if (!result.success) return result
      }
      return result
    } catch (error) {
      return failure(error instanceof Error ? error.message : String(error))
    }
  },

  openStoryBackgroundAttachment: async ({ storedPath }) => {
    window.open(
      `${backendBaseUrl}/api/story-background/attachments/content?storedPath=${encodeURIComponent(storedPath)}`,
      '_blank',
      'noopener,noreferrer',
    )
    return ''
  },
}

export type BrowserPlatformApi = Pick<ElectronAPI, keyof PlatformApi>
