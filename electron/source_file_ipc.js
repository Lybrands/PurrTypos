'use strict'

const fs = require('fs')
const path = require('path')
const AdmZip = require('adm-zip')

const SUPPORTED_EXTENSIONS = new Set(['.txt', '.md', '.markdown'])
const MAX_SOURCE_BYTES = 30 * 1024 * 1024
const MAX_DOCUMENTS = 2_000

function naturalCompare(left, right) {
  return left.localeCompare(right, 'zh-CN', { numeric: true, sensitivity: 'base' })
}

function isHiddenPath(filePath) {
  return filePath.split(/[\\/]/).some((part) => !part || part === '__MACOSX' || part.startsWith('.'))
}

function decodeUtf8(buffer, fileName) {
  try {
    return new TextDecoder('utf-8', { fatal: true }).decode(buffer)
  } catch {
    throw new Error(`${fileName} 不是有效的 UTF-8 文本`)
  }
}

function documentTitle(filePath, pathImpl = path) {
  const normalized = filePath.replace(/\\/g, '/')
  const parts = normalized.split('/')
  return parts
    .map((part, index) => index === parts.length - 1 ? pathImpl.parse(part).name : part)
    .join(' · ')
    .replace(/^#+\s*/, '')
    .trim() || '未命名章节'
}

function composeDocuments({ sourceName, importKind, documents, skippedFileCount = 0, pathImpl = path }) {
  if (!documents.length) throw new Error('没有找到可导入的 TXT 或 Markdown 文稿')
  if (documents.length > MAX_DOCUMENTS) throw new Error(`文稿数量不能超过 ${MAX_DOCUMENTS} 个`)
  const byteCount = documents.reduce((total, document) => total + document.buffer.byteLength, 0)
  if (byteCount > MAX_SOURCE_BYTES) throw new Error('来源内容超过 30 MB 容量限制')
  const sorted = [...documents].sort((left, right) => naturalCompare(left.name, right.name))
  const content = importKind === 'file'
    ? decodeUtf8(sorted[0].buffer, sorted[0].name)
    : sorted.map((document) => {
        const text = decodeUtf8(document.buffer, document.name).trim()
        return text ? `# ${documentTitle(document.name, pathImpl)}\n\n${text}` : ''
      }).filter(Boolean).join('\n\n')
  if (!content.trim()) throw new Error('来源正文不能为空')
  return {
    fileName: sourceName,
    extension: importKind === 'file' ? pathImpl.extname(sourceName).toLowerCase() : '.md',
    byteCount,
    content,
    importKind,
    documentCount: sorted.length,
    skippedFileCount,
  }
}

function readFolder(rootPath, fsImpl, pathImpl) {
  const documents = []
  let skippedFileCount = 0
  const visit = (directory) => {
    for (const entry of fsImpl.readdirSync(directory, { withFileTypes: true })) {
      if (entry.name.startsWith('.')) continue
      const absolutePath = pathImpl.join(directory, entry.name)
      if (entry.isDirectory()) visit(absolutePath)
      else if (entry.isFile()) {
        const relativePath = pathImpl.relative(rootPath, absolutePath)
        if (SUPPORTED_EXTENSIONS.has(pathImpl.extname(entry.name).toLowerCase())) {
          documents.push({ name: relativePath, buffer: fsImpl.readFileSync(absolutePath) })
        } else {
          skippedFileCount += 1
        }
      }
    }
  }
  visit(rootPath)
  return { documents, skippedFileCount }
}

function readArchive(filePath, fsImpl, pathImpl) {
  const zip = new AdmZip(fsImpl.readFileSync(filePath))
  const documents = []
  let skippedFileCount = 0
  let declaredBytes = 0
  for (const entry of zip.getEntries()) {
    if (entry.isDirectory || isHiddenPath(entry.entryName)) continue
    const parts = entry.entryName.replace(/\\/g, '/').split('/')
    if (parts.includes('..') || entry.entryName.startsWith('/')) {
      skippedFileCount += 1
      continue
    }
    if (!SUPPORTED_EXTENSIONS.has(pathImpl.extname(entry.entryName).toLowerCase())) {
      skippedFileCount += 1
      continue
    }
    declaredBytes += Number(entry.header?.size || 0)
    if (declaredBytes > MAX_SOURCE_BYTES) throw new Error('压缩包解压后的来源内容超过 30 MB 容量限制')
    documents.push({ name: entry.entryName, buffer: entry.getData() })
  }
  return { documents, skippedFileCount }
}

function registerNovelSourceFileIpc({
  ipcMain,
  dialog,
  getMainWindow,
  fsImpl = fs,
  pathImpl = path,
} = {}) {
  ipcMain.handle('pick-novel-source-text-file', async (_event, options = {}) => {
    try {
      const mode = options.mode === 'folder' ? 'folder' : 'file'
      const result = await dialog.showOpenDialog(getMainWindow(), mode === 'folder' ? {
        title: '选择小说来源文件夹',
        properties: ['openDirectory'],
      } : {
        title: '选择小说来源文件或压缩包',
        filters: [
          { name: '支持的来源', extensions: ['txt', 'md', 'markdown', 'zip'] },
          { name: 'TXT / Markdown', extensions: ['txt', 'md', 'markdown'] },
          { name: 'ZIP 压缩包', extensions: ['zip'] },
        ],
        properties: ['openFile'],
      })
      if (result.canceled || result.filePaths.length === 0) {
        return { success: false, error: 'canceled' }
      }
      const selectedPath = result.filePaths[0]
      if (mode === 'folder') {
        const { documents, skippedFileCount } = readFolder(selectedPath, fsImpl, pathImpl)
        return {
          success: true,
          data: composeDocuments({
            sourceName: pathImpl.basename(selectedPath),
            importKind: 'folder',
            documents,
            skippedFileCount,
            pathImpl,
          }),
        }
      }
      const extension = pathImpl.extname(selectedPath).toLowerCase()
      if (extension === '.zip') {
        const { documents, skippedFileCount } = readArchive(selectedPath, fsImpl, pathImpl)
        return {
          success: true,
          data: composeDocuments({
            sourceName: pathImpl.basename(selectedPath),
            importKind: 'archive',
            documents,
            skippedFileCount,
            pathImpl,
          }),
        }
      }
      if (!SUPPORTED_EXTENSIONS.has(extension)) {
        return { success: false, error: '只支持 TXT、Markdown 文件、文件夹或 ZIP 压缩包' }
      }
      return {
        success: true,
        data: composeDocuments({
          sourceName: pathImpl.basename(selectedPath),
          importKind: 'file',
          documents: [{ name: pathImpl.basename(selectedPath), buffer: fsImpl.readFileSync(selectedPath) }],
          pathImpl,
        }),
      }
    } catch (error) {
      return { success: false, error: error.message }
    }
  })
}

module.exports = { composeDocuments, registerNovelSourceFileIpc }
