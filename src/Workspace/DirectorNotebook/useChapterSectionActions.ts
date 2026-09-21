import { services } from '@/services'
import React from 'react'
import { usePurrToast } from '@/purr-components'
import type { Chapter, EntityId, Outline } from '../../types'
import type { ExportFormat } from '../../components/ExportModal'
import { buildExportEntries, buildSingleTxtContent } from '../../utils/exportBooks'
import type { ExportChapter } from '../../utils/exportBooks'

interface CreateItemOptions {
  title: string
  parentId?: EntityId | null
  isVolume?: boolean
  resetInput?: () => void
}

interface UseChapterSectionActionsOptions {
  bookId?: EntityId | null
  bookTitle: string
  writingOutlineId?: EntityId | null
  activeChapterId?: EntityId | null
  enableVolume: boolean
  chaptersById: Map<EntityId, Chapter>
  /** 有序章节行（含卷行），用于删除当前章后回退到上一章 */
  chapters: Chapter[]
  onChapterSelect: (chapterId: EntityId, title: string) => void
  onChaptersChange: (outlineId: EntityId, chapters: Chapter[]) => void
  onItemCreated?: (chapterId: EntityId, title: string, isVolume: boolean, parentWritingChapterId: EntityId | null) => void
  onWritingChapterDeleted?: (writingChapterId: EntityId) => void
  onExportSuccess: () => void
}

async function ensureDefaultOutline(bookId?: EntityId | null): Promise<Outline | null> {
  const result = await services.outlines.getWritingOutline(bookId)
  return result.success && result.data ? result.data : null
}

/**
 * Encapsulates the chapter list's Electron API mutations and export workflow.
 * Visual state remains in ChapterSection so the navigation component stays presentational.
 */
export function useChapterSectionActions({
  bookId,
  bookTitle,
  writingOutlineId,
  activeChapterId,
  enableVolume,
  chaptersById,
  chapters,
  onChapterSelect,
  onChaptersChange,
  onItemCreated,
  onWritingChapterDeleted,
  onExportSuccess,
}: UseChapterSectionActionsOptions) {
  const message = usePurrToast()
  const addingRef = React.useRef(false)
  const [exportLoading, setExportLoading] = React.useState(false)

  const getOutlineId = React.useCallback(async (): Promise<EntityId | null> => {
    if (writingOutlineId) return writingOutlineId
    const outline = await ensureDefaultOutline(bookId)
    return outline?.id ?? null
  }, [bookId, writingOutlineId])

  const reloadChapters = React.useCallback(async (outlineId: EntityId) => {
    const result = await services.chapters.getChapters({ outlineId })
    if (result.success) onChaptersChange(outlineId, result.data)
  }, [onChaptersChange])

  const createItem = React.useCallback(async ({
    title,
    parentId,
    isVolume,
    resetInput,
  }: CreateItemOptions) => {
    if (addingRef.current) return
    addingRef.current = true
    try {
      const outlineId = await getOutlineId()
      if (!outlineId) return
      const result = await services.chapters.addChapter({
        outlineId,
        title,
        parentId: parentId ?? undefined,
        ...(isVolume ? { isVolume: true } : {}),
      })
      if (!result.success) return

      await reloadChapters(outlineId)
      resetInput?.()
      if (!isVolume) onChapterSelect(result.data.id, result.data.title)
      onItemCreated?.(result.data.id, title, !!isVolume, parentId ?? null)
    } finally {
      addingRef.current = false
    }
  }, [getOutlineId, onChapterSelect, onItemCreated, reloadChapters])

  const renameChapter = React.useCallback(async (
    chapter: Chapter,
    rawTitle: string,
    finishEditing: () => void,
  ) => {
    const title = rawTitle.trim()
    if (!title || title === chapter.title) {
      finishEditing()
      return
    }

    await services.chapters.renameChapter({ id: chapter.id, title })
    finishEditing()
    if (writingOutlineId) await reloadChapters(writingOutlineId)
    if (activeChapterId === chapter.id) onChapterSelect(chapter.id, title)
  }, [activeChapterId, onChapterSelect, reloadChapters, writingOutlineId])

  const deleteByIds = React.useCallback(async (
    ids: EntityId[],
    notifyOutlineDeleted: boolean,
  ) => {
    for (const id of ids) await services.chapters.deleteChapter({ id })
    if (writingOutlineId) await reloadChapters(writingOutlineId)
    if (activeChapterId != null && ids.includes(activeChapterId)) {
      // 删除的是当前章：回退到渲染顺序中它前面最近的存活章节
      // （没有则取后面最近的），避免会话面板落到无章节的悬空状态。
      const deletedIds = new Set(ids.map(String))
      const volumeIds = new Set(
        chapters.filter((c) => c.parent_id != null).map((c) => String(c.parent_id)),
      )
      const survivors = chapters.filter(
        (c) => !deletedIds.has(String(c.id)) && !volumeIds.has(String(c.id)),
      )
      const firstDeletedIndex = chapters.findIndex((c) => deletedIds.has(String(c.id)))
      const fallback =
        survivors.filter((_c, index) => index < firstDeletedIndex).at(-1)
        ?? survivors[0]
        ?? null
      if (fallback) onChapterSelect(fallback.id, fallback.title)
      else onChapterSelect('', '')
    }
    if (notifyOutlineDeleted) {
      for (const id of ids) onWritingChapterDeleted?.(id)
    }
  }, [activeChapterId, chapters, onChapterSelect, onWritingChapterDeleted, reloadChapters, writingOutlineId])

  const exportChapters = React.useCallback(async (
    selectedIds: EntityId[],
    format: ExportFormat,
    exportAsZip: boolean,
  ) => {
    if (selectedIds.length === 0) {
      message.warning('请至少选择一章')
      return
    }

    setExportLoading(true)
    try {
      if (format === 'epub') {
        if (bookId == null) {
          message.error('未找到当前书籍')
          return
        }
        const result = await services.exports.exportEpub({
          bookId,
          chapterIds: selectedIds,
          defaultName: bookTitle,
        })
        if (result.success) {
          message.success('EPUB 导出成功')
          onExportSuccess()
        } else if (result.error !== 'canceled') {
          message.error(result.error || '导出失败')
        }
        return
      }

      const chaptersWithContent: ExportChapter[] = await Promise.all(
        selectedIds.map(async (chapterId) => {
          const chapter = chaptersById.get(chapterId)
          const result = await services.articles.getArticle({ chapterId })
          const content = result.success && result.data?.content != null
            ? String(result.data.content)
            : ''
          const volumeTitle = enableVolume && chapter?.parent_id != null
            ? chaptersById.get(chapter.parent_id)?.title
            : undefined
          const volumeId = enableVolume ? chapter?.parent_id ?? undefined : undefined
          return { title: chapter?.title ?? '', content, volumeTitle, volumeId }
        }),
      )
      const bookData = { title: bookTitle, chapters: chaptersWithContent }

      if (format === 'txt-single') {
        const result = await services.files.writeSingleTextFile({
          defaultName: `${bookTitle || '导出'}.txt`,
          content: buildSingleTxtContent(bookData),
        })
        if (result.success) {
          message.success('导出成功')
          onExportSuccess()
        } else if (result.error !== 'canceled') {
          message.error(result.error || '导出失败')
        }
        return
      }

      const entries = buildExportEntries([bookData], format)
      const result = await services.files.writeExportFiles({ entries, exportAsZip })
      if (result.success) {
        message.success('导出成功')
        onExportSuccess()
      } else if (result.error !== 'canceled') {
        message.error(result.error || '导出失败')
      }
    } finally {
      setExportLoading(false)
    }
  }, [bookId, bookTitle, chaptersById, enableVolume, message, onExportSuccess])

  return {
    createItem,
    deleteByIds,
    exportChapters,
    exportLoading,
    renameChapter,
  }
}
