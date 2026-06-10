import React from 'react'
import type { EntityId } from '../../types'

/**
 * 当前活跃写作章节（按 bookId 持久化到 localStorage）。
 *
 * - 切书时自动恢复该书上次选中的章节；
 * - 选中变化时写回 localStorage（清空选中则删除条目）。
 */

const ACTIVE_CHAPTER_STORAGE_KEY = 'purrtypos_active_chapter_by_book'

interface ActiveChapterEntry {
  id: EntityId
  title: string
}

function loadActiveChapterMap(): Record<string, ActiveChapterEntry> {
  try {
    const raw = localStorage.getItem(ACTIVE_CHAPTER_STORAGE_KEY)
    if (!raw) return {}
    const obj = JSON.parse(raw)
    return obj && typeof obj === 'object' ? obj : {}
  } catch {
    return {}
  }
}

function saveActiveChapterMap(map: Record<string, ActiveChapterEntry>) {
  try {
    localStorage.setItem(ACTIVE_CHAPTER_STORAGE_KEY, JSON.stringify(map))
  } catch {
    // ignore
  }
}

export function useActiveChapter(bookId: EntityId | null | undefined) {
  const [activeChapterId, setActiveChapterId] = React.useState<EntityId | null>(() => {
    if (bookId == null) return null
    const entry = loadActiveChapterMap()[String(bookId)]
    return entry?.id ?? null
  })
  const [activeChapterTitle, setActiveChapterTitle] = React.useState<string>(() => {
    if (bookId == null) return ''
    return loadActiveChapterMap()[String(bookId)]?.title ?? ''
  })

  /** bookId 变化时：恢复该书之前选中的章节（如 localStorage 里有） */
  React.useEffect(() => {
    if (bookId == null) return
    const entry = loadActiveChapterMap()[String(bookId)]
    if (entry?.id != null) {
      setActiveChapterId(entry.id)
      setActiveChapterTitle(entry.title ?? '')
    } else {
      setActiveChapterId(null)
      setActiveChapterTitle('')
    }
  }, [bookId])

  /** 持久化当前活跃章节 */
  React.useEffect(() => {
    if (bookId == null) return
    const map = loadActiveChapterMap()
    if (activeChapterId == null) {
      delete map[String(bookId)]
    } else {
      map[String(bookId)] = { id: activeChapterId, title: activeChapterTitle }
    }
    saveActiveChapterMap(map)
  }, [bookId, activeChapterId, activeChapterTitle])

  /** 同时设置 id + 标题（Context 的 setActiveChapter） */
  const setActiveChapter = React.useCallback((id: EntityId, title: string) => {
    setActiveChapterId(id)
    setActiveChapterTitle(title || '')
  }, [])

  return { activeChapterId, activeChapterTitle, setActiveChapter }
}
