/**
 * 书籍导出：构建「每本书一个文件夹、每章一个文件」的导出条目，供主进程写入磁盘或 zip
 */

import { getWritingOutlineWithChapters, batchGetChapterContents } from '../Workspace/utils'
import { shortUuid } from './common'
import type { EntityId } from '../types'

/** 单章数据（标题 + 正文；带卷时可选卷名与卷 id 用于子目录） */
export interface ExportChapter {
  title: string
  content: string
  /** 带卷书籍时，该章所属卷名（用作书名下的子目录名） */
  volumeTitle?: string
  /** 带卷书籍时，该章所属卷的 id（parent_id），用于区分不同卷，保证同卷章节进同一目录） */
  volumeId?: EntityId
}

/** 单本书导出数据（书名 + 章节列表） */
export interface ExportBookData {
  title: string
  chapters: ExportChapter[]
}

/** 一条导出文件（相对路径 + 内容） */
export interface ExportEntry {
  path: string
  content: string
}

/** 文件名非法字符替换为下划线 */
export function sanitizeFileName(name: string): string {
  return String(name).replace(/[/\\:*?"<>|]/g, '_').trim() || '未命名'
}

/**
 * 根据书籍数据与格式，生成导出文件列表。
 * 不带卷：书名/章节名.ext
 * 带卷：书名/卷名/章节名.ext（每卷一个子目录，卷内章节在该目录下）
 */
export function buildExportEntries(
  books: ExportBookData[],
  format: 'md' | 'txt'
): ExportEntry[] {
  const ext = format === 'md' ? '.md' : '.txt'
  const entries: ExportEntry[] = []

  for (const book of books) {
    const folderName = `${sanitizeFileName(book.title)}_${Date.now()}_${shortUuid()}`
    const volumeIdToDirName = new Map<string, string>()
    const usedVolumeDirNames = new Set<string>()
    const fileSeenPerDir = new Map<string, Map<string, number>>()

    for (const ch of book.chapters) {
      const head = format === 'md' ? `# ${ch.title}\n\n` : `${ch.title}\n\n`
      const body = ch.content + (ch.content && !ch.content.endsWith('\n') ? '\n' : '')

      let fullDir: string
      if (ch.volumeTitle != null && ch.volumeId != null) {
        let actualVolDir = volumeIdToDirName.get(ch.volumeId)
        if (actualVolDir == null) {
          const base = sanitizeFileName(ch.volumeTitle)
          let name = base
          let n = 0
          while (usedVolumeDirNames.has(name)) name = `${base}_${++n}`
          usedVolumeDirNames.add(name)
          actualVolDir = name
          volumeIdToDirName.set(ch.volumeId, actualVolDir)
        }
        fullDir = `${folderName}/${actualVolDir}`
      } else {
        fullDir = folderName
      }

      if (!fileSeenPerDir.has(fullDir)) fileSeenPerDir.set(fullDir, new Map())
      const seen = fileSeenPerDir.get(fullDir)!
      const rawName = sanitizeFileName(ch.title) || '未命名'
      const n = seen.get(rawName) ?? 0
      seen.set(rawName, n + 1)
      const baseName = n > 0 ? `${rawName}_${n}` : rawName
      const fileName = baseName + ext
      entries.push({ path: `${fullDir}/${fileName}`, content: head + body })
    }

    if (book.chapters.length === 0) {
      const placeholder = `${folderName}/（暂无章节）${ext}`
      entries.push({ path: placeholder, content: '' })
    }
  }

  return entries
}

/**
 * 整本书合并为单个 TXT 内容（投稿 / 发布平台格式）：
 * 卷标题独立一行（如有），每章「章节标题 + 空行 + 正文」，章间空两行。
 */
export function buildSingleTxtContent(book: ExportBookData): string {
  const parts: string[] = []
  let lastVolumeId: EntityId | null | undefined = undefined
  for (const ch of book.chapters) {
    if (ch.volumeTitle != null && ch.volumeId !== lastVolumeId) {
      parts.push(ch.volumeTitle)
      lastVolumeId = ch.volumeId
    }
    const body = (ch.content || '').replace(/\r\n/g, '\n').trim()
    parts.push(`${ch.title}\n\n${body}`)
  }
  return parts.join('\n\n\n') + '\n'
}

/**
 * 根据选中的书籍 ID 拉取导出所需数据（写作大纲、章节、正文）
 * 依赖运行环境中的 window.electronAPI，使用方法库批量获取
 */
export async function fetchExportData(bookIds: EntityId[]): Promise<ExportBookData[]> {
  const api = typeof window !== 'undefined' && (window as unknown as { electronAPI?: { getBooks: () => Promise<{ success: boolean; data?: { id: string; title: string; enable_volume?: number }[] }> } }).electronAPI
  if (!api) return []

  const booksRes = await api.getBooks()
  if (!booksRes.success || !booksRes.data) return []
  const books = booksRes.data.filter((b) => bookIds.includes(b.id))

  const result: ExportBookData[] = []

  for (const book of books) {
    const data = await getWritingOutlineWithChapters(book.id)
    const chapters: ExportChapter[] = []
    const enableVolume = !!(book.enable_volume != null && book.enable_volume !== 0)

    if (data && data.chapters.length > 0) {
      const chapterList = data.chapters
      const idToChapter = new Map(chapterList.map((c) => [c.id, c]))
      const writableChapters = enableVolume
        ? chapterList.filter((c) => c.parent_id != null)
        : chapterList
      if (writableChapters.length > 0) {
        const titleMap = new Map(writableChapters.map((c) => [c.id, c.title]))
        const contents = await batchGetChapterContents(
          writableChapters.map((c) => c.id),
          titleMap,
          999999,
        )
        const contentById = new Map(contents.map((c) => [c.chapterId, c.content]))
        for (const ch of writableChapters) {
          const volumeTitle = enableVolume && ch.parent_id != null ? idToChapter.get(ch.parent_id)?.title : undefined
          const volumeId = enableVolume ? ch.parent_id ?? undefined : undefined
          chapters.push({
            title: ch.title,
            content: contentById.get(ch.id) ?? '',
            volumeTitle,
            volumeId,
          })
        }
      }
    }

    result.push({ title: book.title, chapters })
  }

  return result
}
