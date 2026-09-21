import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import type { EntityId } from '../types'
import { loggedJsonStorage } from './persistStorage.ts'

/**
 * 每本书的 AI 上下文勾选（记忆/伏笔 + 关联章节/大纲）。
 *
 * 替代原 useMemorySelection / useAssociatedContext 两处手写
 * localStorage 镜像——主 AI 面板与 Inline 弹层（两处挂载）从此共享
 * 同一份响应式数据，一侧勾选另一侧立即可见。
 */

export interface MemorySelectionEntry {
  longTerm: string[]
  memory: (number | string)[]
  foreshadowing: (number | string)[]
}

export interface AssociatedContextEntry {
  chapters: EntityId[]
  outlines: EntityId[]
}

export const EMPTY_MEMORY_SELECTION: MemorySelectionEntry = {
  longTerm: [],
  memory: [],
  foreshadowing: [],
}

export const EMPTY_ASSOCIATED_CONTEXT: AssociatedContextEntry = {
  chapters: [],
  outlines: [],
}

interface BookChatContext {
  memorySelection: MemorySelectionEntry
  associatedContext: AssociatedContextEntry
}

interface ChatContextState {
  byBook: Record<string, BookChatContext>
  setMemorySelection(bookKey: string, next: MemorySelectionEntry): void
  setAssociatedContext(bookKey: string, next: AssociatedContextEntry): void
}

const bookEntry = (
  byBook: Record<string, BookChatContext>,
  bookKey: string,
): BookChatContext =>
  byBook[bookKey] ?? {
    memorySelection: EMPTY_MEMORY_SELECTION,
    associatedContext: EMPTY_ASSOCIATED_CONTEXT,
  }

export const useChatContextStore = create<ChatContextState>()(
  persist(
    (set) => ({
      byBook: {},
      setMemorySelection: (bookKey, next) =>
        set((state) => ({
          byBook: {
            ...state.byBook,
            [bookKey]: { ...bookEntry(state.byBook, bookKey), memorySelection: next },
          },
        })),
      setAssociatedContext: (bookKey, next) =>
        set((state) => ({
          byBook: {
            ...state.byBook,
            [bookKey]: { ...bookEntry(state.byBook, bookKey), associatedContext: next },
          },
        })),
    }),
    {
      name: 'purrtypos_chat_context_v1',
      storage: loggedJsonStorage,
      partialize: (state) => ({ byBook: state.byBook }),
      merge: (persisted, current) => {
        const value = persisted as
          | { byBook?: Record<string, Partial<BookChatContext>> }
          | undefined
        const source = value?.byBook
        if (!source || typeof source !== 'object') return current
        const byBook: Record<string, BookChatContext> = {}
        for (const [key, partial] of Object.entries(source)) {
          if (!partial || typeof partial !== 'object') continue
          const memory = partial.memorySelection
          const associated = partial.associatedContext
          byBook[key] = {
            memorySelection: {
              longTerm: Array.isArray(memory?.longTerm) ? memory.longTerm.map(String) : [],
              memory: Array.isArray(memory?.memory) ? memory.memory : [],
              foreshadowing: Array.isArray(memory?.foreshadowing) ? memory.foreshadowing : [],
            },
            associatedContext: {
              chapters: Array.isArray(associated?.chapters) ? associated.chapters : [],
              outlines: Array.isArray(associated?.outlines) ? associated.outlines : [],
            },
          }
        }
        return { ...current, byBook }
      },
    },
  ),
)

// ── 窄 selector hooks ─────────────────────────────────────────────
export function useMemorySelectionEntry(
  bookId: EntityId | null | undefined,
): MemorySelectionEntry {
  const key = bookId == null ? '' : String(bookId)
  return useChatContextStore((state) =>
    key === '' ? EMPTY_MEMORY_SELECTION : bookEntry(state.byBook, key).memorySelection,
  )
}

export function useAssociatedContextEntry(
  bookId: EntityId | null | undefined,
): AssociatedContextEntry {
  const key = bookId == null ? '' : String(bookId)
  return useChatContextStore((state) =>
    key === '' ? EMPTY_ASSOCIATED_CONTEXT : bookEntry(state.byBook, key).associatedContext,
  )
}

// ── 非组件调用入口 ────────────────────────────────────────────────
export function writeMemorySelection(
  bookId: EntityId | null | undefined,
  next: MemorySelectionEntry,
): void {
  if (bookId == null) return
  useChatContextStore.getState().setMemorySelection(String(bookId), next)
}

export function writeAssociatedContext(
  bookId: EntityId | null | undefined,
  next: AssociatedContextEntry,
): void {
  if (bookId == null) return
  useChatContextStore.getState().setAssociatedContext(String(bookId), next)
}

export function __resetChatContextStoreForTests(): void {
  useChatContextStore.setState({ byBook: {} })
}
