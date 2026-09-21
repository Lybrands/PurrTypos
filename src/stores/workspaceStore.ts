import type { LexicalEditor } from 'lexical'
import { create } from 'zustand'
import type { Chapter, EntityId } from '../types'
import { EDITOR_TAB_KEY, type WorkspaceUtilityTab } from '../Workspace/utilityPanelTypes.ts'
import type { OpenSettingPanelDetail } from '../Workspace/SettingPanel'
import { useActiveChapterStore } from './activeChapterStore.ts'
import { updatePanelLayout, usePanelLayoutStore } from './panelLayoutStore.ts'

/** 写作目录快照（fetcher 由 Workspace 注入，store 不依赖服务层） */
export interface WritingChaptersSnapshot {
  outlineId: EntityId
  chapters: Chapter[]
}

let writingChaptersFetcher:
  | ((bookId: EntityId | null) => Promise<WritingChaptersSnapshot | null>)
  | null = null

/** Workspace 挂载时注册目录拉取器（services 链留在组件层） */
export function registerWritingChaptersFetcher(
  fetcher: (bookId: EntityId | null) => Promise<WritingChaptersSnapshot | null>,
): void {
  writingChaptersFetcher = fetcher
}

/**
 * 工作台共享状态（替代原 WorkspaceContext 的 25 字段单一 value）。
 *
 * 消费约定：**必须用窄 selector 订阅**（下面的 useXxx hooks 或
 * `useWorkspaceStore(s => s.field)`），禁止整店订阅——搜索击键、正文输入的
 * contentVersion 等高频字段只应重渲染真正订阅它的组件。
 * actions 引用恒定，可直接 select 或经 getState() 非响应式调用。
 */

const EDITOR_TAB_KEY_DEFAULT = EDITOR_TAB_KEY

interface WorkspaceState {
  // ── 书籍身份（挂载期由 Workspace 设置）──────────────────────────
  bookId: EntityId | null
  bookTitle: string
  enableVolume: boolean
  setBookIdentity(payload: {
    bookId: EntityId | null
    bookTitle: string
    enableVolume: boolean
  }): void

  // ── 写作章节 ────────────────────────────────────────────────────
  writingChapters: Chapter[]
  writingOutlineId: EntityId | null
  activeChapterId: EntityId | null
  activeChapterTitle: string
  setActiveChapter(id: EntityId | null, title: string): void
  setChaptersData(outlineId: EntityId, chapters: Chapter[]): void
  /** 重新拉取写作目录（返回是否成功；失败时保留现有目录） */
  reloadWritingChapters(bookId: EntityId | null): Promise<boolean>

  // ── 右侧组合面板（正文 + 功能标签）───────────────────────────────
  utilityTabs: WorkspaceUtilityTab[]
  activeRightTabKey: string
  settingOpenRequest: OpenSettingPanelDetail | null
  openUtilityTab(tab: WorkspaceUtilityTab): void
  toggleUtilityTab(tab: WorkspaceUtilityTab): void
  closeUtilityTab(key: string): void
  setActiveRightTabKey(key: string): void
  setSettingOpenRequest(request: OpenSettingPanelDetail | null): void
  /** 切书时清空功能标签回到正文 */
  resetUtilityPanel(): void

  // ── 工作区搜索（高频字段，务必窄订阅）────────────────────────────
  searchQuery: string
  searchActiveIndex: number
  searchMatchTotal: number
  searchContentVersion: number
  setSearchQuery(query: string): void
  setSearchActiveIndex(next: number | ((current: number) => number)): void
  setSearchMatchTotal(total: number): void
  bumpSearchContentVersion(): void

  // ── 章节正文修订号（AI 落库 / diff 提交后 bump，EditorPanel 据此重拉）──
  chapterContentRevision: Record<string, number>
  bumpChapterContentRevision(chapterId: EntityId): void

  // ── 书籍总字数（低频展示）───────────────────────────────────────
  bookWordWanDisplay: string | null
  setBookWordWanDisplay(value: string | null): void
}

export const useWorkspaceStore = create<WorkspaceState>((set, get) => ({
  bookId: null,
  bookTitle: '',
  enableVolume: false,
  setBookIdentity: ({ bookId, bookTitle, enableVolume }) => {
    const prev = get()
    if (
      String(prev.bookId) === String(bookId) &&
      prev.bookTitle === bookTitle &&
      prev.enableVolume === enableVolume
    ) {
      return
    }
    set({ bookId, bookTitle, enableVolume })
  },

  writingChapters: [],
  writingOutlineId: null,
  activeChapterId: null,
  activeChapterTitle: '',
  setActiveChapter: (id, title) => {
    set({ activeChapterId: id, activeChapterTitle: title || '' })
    const { bookId } = get()
    if (bookId != null) {
      useActiveChapterStore
        .getState()
        .setActive(
          String(bookId),
          id == null ? null : { id, title: title || '' },
        )
    }
  },
  setChaptersData: (outlineId, chapters) => {
    set({ writingOutlineId: outlineId, writingChapters: chapters || [] })
  },
  reloadWritingChapters: async (bookId) => {
    if (!writingChaptersFetcher) return false
    const data = await writingChaptersFetcher(bookId)
    if (!data) return false
    set({ writingOutlineId: data.outlineId, writingChapters: data.chapters })
    return true
  },

  utilityTabs: [],
  activeRightTabKey: EDITOR_TAB_KEY_DEFAULT,
  settingOpenRequest: null,
  openUtilityTab: (tab) => {
    set((state) => {
      const existingIndex = state.utilityTabs.findIndex((item) => item.key === tab.key)
      const utilityTabs =
        existingIndex < 0
          ? [...state.utilityTabs, tab]
          : state.utilityTabs.map((item, index) =>
              index === existingIndex ? tab : item,
            )
      return { utilityTabs, activeRightTabKey: tab.key }
    })
    updatePanelLayout('right', { open: true })
  },
  toggleUtilityTab: (tab) => {
    const state = get()
    const rightOpen = usePanelLayoutStore.getState().right.open
    if (rightOpen && state.activeRightTabKey === tab.key) {
      set({ activeRightTabKey: EDITOR_TAB_KEY_DEFAULT })
      return
    }
    state.openUtilityTab(tab)
  },
  closeUtilityTab: (key) => {
    set((state) => {
      const closingIndex = state.utilityTabs.findIndex((tab) => tab.key === key)
      if (closingIndex < 0) return state
      const utilityTabs = state.utilityTabs.filter((tab) => tab.key !== key)
      const activeRightTabKey =
        state.activeRightTabKey !== key
          ? state.activeRightTabKey
          : utilityTabs[Math.min(closingIndex, utilityTabs.length - 1)]?.key ??
            EDITOR_TAB_KEY_DEFAULT
      return { utilityTabs, activeRightTabKey }
    })
  },
  setActiveRightTabKey: (key) => set({ activeRightTabKey: key }),
  setSettingOpenRequest: (request) => set({ settingOpenRequest: request }),
  resetUtilityPanel: () =>
    set({ utilityTabs: [], activeRightTabKey: EDITOR_TAB_KEY_DEFAULT, settingOpenRequest: null }),

  searchQuery: '',
  searchActiveIndex: 0,
  searchMatchTotal: 0,
  searchContentVersion: 0,
  setSearchQuery: (query) =>
    set((state) =>
      state.searchQuery === query
        ? state
        : { searchQuery: query, searchActiveIndex: 0 },
    ),
  setSearchActiveIndex: (next) =>
    set((state) => ({
      searchActiveIndex:
        typeof next === 'function' ? next(state.searchActiveIndex) : next,
    })),
  setSearchMatchTotal: (total) => set({ searchMatchTotal: total }),
  bumpSearchContentVersion: () =>
    set((state) => ({ searchContentVersion: state.searchContentVersion + 1 })),

  chapterContentRevision: {},
  bumpChapterContentRevision: (chapterId) =>
    set((state) => {
      const key = String(chapterId)
      return {
        chapterContentRevision: {
          ...state.chapterContentRevision,
          [key]: (state.chapterContentRevision[key] ?? 0) + 1,
        },
      }
    }),

  bookWordWanDisplay: null,
  setBookWordWanDisplay: (value) => {
    if (get().bookWordWanDisplay !== value) set({ bookWordWanDisplay: value })
  },
}))

// ── 窄 selector hooks（消费侧统一从这里取，避免宽订阅）──────────────
export const useBookId = () => useWorkspaceStore((s) => s.bookId)
export const useBookTitle = () => useWorkspaceStore((s) => s.bookTitle)
export const useEnableVolume = () => useWorkspaceStore((s) => s.enableVolume)
export const useWritingChapters = () => useWorkspaceStore((s) => s.writingChapters)
export const useActiveChapterId = () => useWorkspaceStore((s) => s.activeChapterId)
export const useActiveChapterTitle = () => useWorkspaceStore((s) => s.activeChapterTitle)
export const useWritingOutlineId = () => useWorkspaceStore((s) => s.writingOutlineId)
export const useUtilityTabs = () => useWorkspaceStore((s) => s.utilityTabs)
export const useActiveRightTabKey = () => useWorkspaceStore((s) => s.activeRightTabKey)
export const useSettingOpenRequest = () => useWorkspaceStore((s) => s.settingOpenRequest)
export const useSearchQuery = () => useWorkspaceStore((s) => s.searchQuery)
export const useSearchActiveIndex = () => useWorkspaceStore((s) => s.searchActiveIndex)
export const useSearchMatchTotal = () => useWorkspaceStore((s) => s.searchMatchTotal)
export const useSearchContentVersion = () =>
  useWorkspaceStore((s) => s.searchContentVersion)
export const useBookWordWanDisplay = () =>
  useWorkspaceStore((s) => s.bookWordWanDisplay)

// ── 非组件调用入口 ────────────────────────────────────────────────
/** 内容变化后刷新搜索匹配数（写作区、大纲 Markdown 等渲染方调用） */
export function notifyWorkspaceSearchContentChanged(): void {
  useWorkspaceStore.getState().bumpSearchContentVersion()
}
export function setActiveChapter(id: EntityId | null, title: string): void {
  useWorkspaceStore.getState().setActiveChapter(id, title)
}
export function reloadWritingChapters(bookId: EntityId | null): Promise<boolean> {
  return useWorkspaceStore.getState().reloadWritingChapters(bookId)
}
/** AI 流 / diff 提交后通知正文已服务端落库，EditorPanel 重拉该章 */
export function notifyChapterContentUpdated(chapterId: EntityId): void {
  useWorkspaceStore.getState().bumpChapterContentRevision(chapterId)
}

// ── 编辑器实例 ref（非响应式，模块级共享）──────────────────────────
export const lexicalEditorRef: { current: LexicalEditor | null } = { current: null }

export function __resetWorkspaceStoreForTests(): void {
  useWorkspaceStore.setState({
    bookId: null,
    bookTitle: '',
    enableVolume: false,
    writingChapters: [],
    writingOutlineId: null,
    activeChapterId: null,
    activeChapterTitle: '',
    utilityTabs: [],
    activeRightTabKey: EDITOR_TAB_KEY_DEFAULT,
    settingOpenRequest: null,
    searchQuery: '',
    searchActiveIndex: 0,
    searchMatchTotal: 0,
    searchContentVersion: 0,
    chapterContentRevision: {},
    bookWordWanDisplay: null,
  })
}
