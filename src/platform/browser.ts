import JSZip from 'jszip'
import type { ApiResult, StoryBackgroundAttachment } from '../types'
import { backendBaseUrl } from '../services/httpClient'
import type { PlatformApi } from './types'

function failure<T>(error: string): ApiResult<T> {
  return { success: false, data: undefined as T, error }
}

function chooseFiles(accept = '', multiple = false, directory = false): Promise<File[]> {
  return new Promise((resolve) => {
    const input = document.createElement('input')
    input.type = 'file'
    input.accept = accept
    input.multiple = multiple
    if (directory) input.setAttribute('webkitdirectory', '')
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

const NOVEL_SOURCE_EXTENSIONS = new Set(['.txt', '.md', '.markdown'])
const MAX_NOVEL_SOURCE_BYTES = 30 * 1024 * 1024
const MAX_NOVEL_SOURCE_DOCUMENTS = 2_000

function sourceExtension(fileName: string) {
  const match = fileName.toLowerCase().match(/\.[^.]+$/)
  return match?.[0] ?? ''
}

function sourceDocumentTitle(fileName: string) {
  const parts = fileName.replace(/\\/g, '/').split('/')
  const leaf = parts.pop()?.replace(/\.(?:txt|md|markdown)$/i, '') || '未命名章节'
  return [...parts, leaf].join(' · ').replace(/^#+\s*/, '').trim()
}

function isIgnoredArchivePath(fileName: string) {
  const normalized = fileName.replace(/\\/g, '/')
  const parts = normalized.split('/')
  return normalized.startsWith('/') || parts.some((part) => !part || part === '..' || part === '__MACOSX' || part.startsWith('.'))
}

async function decodeSourceBytes(bytes: Uint8Array, fileName: string) {
  try {
    return new TextDecoder('utf-8', { fatal: true }).decode(bytes)
  } catch {
    throw new Error(`${fileName} 不是有效的 UTF-8 文本`)
  }
}

async function composeSourceDocuments(
  sourceName: string,
  importKind: 'file' | 'folder' | 'archive',
  documents: Array<{ name: string; bytes: Uint8Array }>,
  skippedFileCount: number,
) {
  if (!documents.length) throw new Error('没有找到可导入的 TXT 或 Markdown 文稿')
  if (documents.length > MAX_NOVEL_SOURCE_DOCUMENTS) throw new Error(`文稿数量不能超过 ${MAX_NOVEL_SOURCE_DOCUMENTS} 个`)
  const sorted = [...documents].sort((left, right) => left.name.localeCompare(right.name, 'zh-CN', { numeric: true, sensitivity: 'base' }))
  const byteCount = sorted.reduce((total, document) => total + document.bytes.byteLength, 0)
  if (byteCount > MAX_NOVEL_SOURCE_BYTES) throw new Error('来源内容超过 30 MB 容量限制')
  const decoded = await Promise.all(sorted.map(async (document) => ({
    name: document.name,
    content: (await decodeSourceBytes(document.bytes, document.name)).trim(),
  })))
  const content = importKind === 'file'
    ? decoded[0].content
    : decoded.filter((document) => document.content).map((document) => (
        `# ${sourceDocumentTitle(document.name)}\n\n${document.content}`
      )).join('\n\n')
  if (!content.trim()) throw new Error('来源正文不能为空')
  return {
    fileName: sourceName,
    extension: (importKind === 'file' ? sourceExtension(sourceName) : '.md') as '.txt' | '.md' | '.markdown',
    byteCount,
    content,
    importKind,
    documentCount: sorted.length,
    skippedFileCount,
  }
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
      `/api/screenplay/v2/projects/${encodeURIComponent(projectId)}/export/pdf`,
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
      `purrtypos-backup-${new Date().toISOString().slice(0, 10)}.purrbackup`,
    )
    return result.success
      ? { success: true, data: undefined }
      : { success: false, data: undefined, error: result.error }
  },

  importDatabase: async () => {
    const [file] = await chooseFiles('.purrbackup,application/zip')
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
    failure<void>('浏览器无法打开数据目录，可以使用“导出完整备份”下载数据'),

  openAndReadTextFile: async () => {
    const [file] = await chooseFiles('.txt,.md,.markdown,text/plain,text/markdown')
    if (!file) return failure<string>('canceled')
    try {
      return { success: true, data: await file.text() }
    } catch (error) {
      return failure(error instanceof Error ? error.message : String(error))
    }
  },

  pickNovelSourceTextFile: async ({ mode = 'file' } = {}) => {
    try {
      if (mode === 'folder') {
        const files = await chooseFiles('.txt,.md,.markdown,text/plain,text/markdown', true, true)
        if (!files.length) return failure('canceled')
        const sourceName = files[0].webkitRelativePath.split('/')[0] || '来源文件夹'
        const supported = files.filter((file) => NOVEL_SOURCE_EXTENSIONS.has(sourceExtension(file.name)))
        return {
          success: true,
          data: await composeSourceDocuments(
            sourceName,
            'folder',
            await Promise.all(supported.map(async (file) => ({
              name: file.webkitRelativePath || file.name,
              bytes: new Uint8Array(await file.arrayBuffer()),
            }))),
            files.length - supported.length,
          ),
        }
      }

      const [file] = await chooseFiles('.txt,.md,.markdown,.zip,text/plain,text/markdown,application/zip')
      if (!file) return failure('canceled')
      const extension = sourceExtension(file.name)
      if (extension !== '.zip' && !NOVEL_SOURCE_EXTENSIONS.has(extension)) {
        return failure('只支持 TXT、Markdown 文件、文件夹或 ZIP 压缩包')
      }
      if (extension !== '.zip') {
        return {
          success: true,
          data: await composeSourceDocuments(file.name, 'file', [{
            name: file.name,
            bytes: new Uint8Array(await file.arrayBuffer()),
          }], 0),
        }
      }

      if (file.size > MAX_NOVEL_SOURCE_BYTES) throw new Error('ZIP 压缩包不能超过 30 MB')
      const zip = await JSZip.loadAsync(await file.arrayBuffer())
      const entries = Object.values(zip.files).filter((entry) => {
        const originalName = (entry as typeof entry & { unsafeOriginalName?: string }).unsafeOriginalName || entry.name
        return !entry.dir && !isIgnoredArchivePath(originalName) && NOVEL_SOURCE_EXTENSIONS.has(sourceExtension(entry.name))
      })
      if (entries.length > MAX_NOVEL_SOURCE_DOCUMENTS) throw new Error(`文稿数量不能超过 ${MAX_NOVEL_SOURCE_DOCUMENTS} 个`)
      const declaredBytes = entries.reduce((total, entry) => (
        total + Number((entry as typeof entry & { _data?: { uncompressedSize?: number } })._data?.uncompressedSize || 0)
      ), 0)
      if (declaredBytes > MAX_NOVEL_SOURCE_BYTES) throw new Error('压缩包解压后的来源内容超过 30 MB 容量限制')
      const visibleFiles = Object.values(zip.files).filter((entry) => !entry.dir && !isIgnoredArchivePath(entry.name))
      return {
        success: true,
        data: await composeSourceDocuments(
          file.name,
          'archive',
          await Promise.all(entries.map(async (entry) => ({
            name: entry.name,
            bytes: await entry.async('uint8array'),
          }))),
          visibleFiles.length - entries.length,
        ),
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
