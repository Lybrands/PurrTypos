import { create } from 'zustand'

/**
 * 批注修订号 store：批注增删改后 bump 对应章节的修订号，
 * `useChapterAnnotations` 订阅修订号触发重载。
 * 替代原 `chapter-annotations-changed` CustomEvent——多 EditorPanel 挂载
 * （停靠 / 悬停预览）天然共享同一份数据。
 */

interface AnnotationsState {
  /** `${bookId}::${chapterId}` → 修订号 */
  revision: Record<string, number>
  bumpRevision: (bookId: string | number, chapterId: string | number) => void
}

const chapterKey = (bookId: string | number, chapterId: string | number) =>
  `${bookId}::${chapterId}`

export const useAnnotationsStore = create<AnnotationsState>((set) => ({
  revision: {},
  bumpRevision: (bookId, chapterId) =>
    set((state) => {
      const key = chapterKey(bookId, chapterId)
      return {
        revision: { ...state.revision, [key]: (state.revision[key] ?? 0) + 1 },
      }
    }),
}))

/** 非组件调用入口（批注弹层保存等） */
export function bumpAnnotationsRevision(
  bookId: string | number,
  chapterId: string | number,
): void {
  useAnnotationsStore.getState().bumpRevision(bookId, chapterId)
}

/** 订阅某章节的修订号（组件用；返回值仅在该章节 bump 时变化） */
export function useAnnotationsRevision(
  bookId: string | number | null,
  chapterId: string | number | null,
): number {
  return useAnnotationsStore((state) =>
    bookId == null || chapterId == null
      ? 0
      : state.revision[chapterKey(bookId, chapterId)] ?? 0,
  )
}

export function __resetAnnotationsStoreForTests(): void {
  useAnnotationsStore.setState({ revision: {} })
}
