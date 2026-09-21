import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import { loggedJsonStorage } from './persistStorage.ts'
import type { EntityId } from '../types'

/**
 * 各书上次选中的写作章节（跨会话恢复）。
 * 替代原 useActiveChapter 手写 localStorage（key 沿用）。
 */

interface ActiveChapterEntry {
  id: EntityId
  title: string
}

interface ActiveChapterState {
  byBook: Record<string, ActiveChapterEntry>
  setActive: (bookKey: string, entry: ActiveChapterEntry | null) => void
}

export const useActiveChapterStore = create<ActiveChapterState>()(
  persist(
    (set) => ({
      byBook: {},
      setActive: (bookKey, entry) =>
        set((state) => {
          const byBook = { ...state.byBook }
          if (entry == null) delete byBook[bookKey]
          else byBook[bookKey] = entry
          return { byBook }
        }),
    }),
    {
      name: 'purrtypos_active_chapter_by_book',
      storage: loggedJsonStorage,
      partialize: (state) => ({ byBook: state.byBook }),
      merge: (persisted, current) => {
        const value = persisted as { byBook?: Record<string, ActiveChapterEntry> } | undefined
        return {
          ...current,
          byBook:
            value?.byBook && typeof value.byBook === 'object' ? value.byBook : {},
        }
      },
    },
  ),
)

/** 非组件读取：某书上一次选中的章节 */
export function restoreActiveChapter(
  bookId: EntityId | null | undefined,
): ActiveChapterEntry | null {
  if (bookId == null) return null
  return useActiveChapterStore.getState().byBook[String(bookId)] ?? null
}

export function __resetActiveChapterStoreForTests(): void {
  useActiveChapterStore.setState({ byBook: {} })
}
