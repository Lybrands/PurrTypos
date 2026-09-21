import { create } from 'zustand'
import type { EntityId, ProposedSettingDiff, SettingDiffCardState } from '../types'

/**
 * AI 提议桥：AI 流副作用 / 会话恢复 ↔ Diff、SettingDiff 之间的类型化单向信道，
 * 替代原 5 个 CustomEvent（ai-propose-chapter-diff、ai-propose-setting-diff ×2、
 * setting-diff-resolved / owner-evicted / resolution-hydrated）。
 *
 * 每个信道都是「队列 + 排空」：生产端 push（同一轮连发多条不丢），
 * 消费端订阅队列长度、在 effect 中 drain 取走整段快照后按原顺序处理——
 * 与原 CustomEvent 的逐条同步投递语义一致。
 */

export interface ChapterDiffProposalPayload {
  chapterId: EntityId
  beforeText: string
  proposedText: string
  source?: string
}

export interface SettingDiffProposalPayload extends ProposedSettingDiff {
  /** 会话历史恢复的待处理提议：只还原，不重新计费/通知 */
  restoreOnly?: boolean
}

export interface SettingDiffHydrationPayload extends SettingDiffCardState {
  ownerSessionId?: number
}

interface AiProposalBridgeState {
  chapterDiffQueue: ChapterDiffProposalPayload[]
  settingDiffQueue: SettingDiffProposalPayload[]
  resolvedQueue: SettingDiffCardState[]
  evictQueue: Array<{ bookId: EntityId | null; sessionId: number }>
  hydrateQueue: SettingDiffHydrationPayload[]

  proposeChapterDiff(payload: ChapterDiffProposalPayload): void
  proposeSettingDiff(payload: SettingDiffProposalPayload): void
  resolveSettingDiff(card: SettingDiffCardState): void
  evictSettingDiffOwner(bookId: EntityId | null, sessionId: number): void
  hydrateSettingDiffResolution(card: SettingDiffHydrationPayload): void

  drainChapterDiff(): ChapterDiffProposalPayload[]
  drainSettingDiff(): SettingDiffProposalPayload[]
  drainResolved(): SettingDiffCardState[]
  drainEvictions(): Array<{ bookId: EntityId | null; sessionId: number }>
  drainHydrations(): SettingDiffHydrationPayload[]
}

export const useAiProposalBridge = create<AiProposalBridgeState>((set) => ({
  chapterDiffQueue: [],
  settingDiffQueue: [],
  resolvedQueue: [],
  evictQueue: [],
  hydrateQueue: [],

  proposeChapterDiff: (payload) =>
    set((state) => ({ chapterDiffQueue: [...state.chapterDiffQueue, payload] })),
  proposeSettingDiff: (payload) =>
    set((state) => ({ settingDiffQueue: [...state.settingDiffQueue, payload] })),
  resolveSettingDiff: (card) =>
    set((state) => ({ resolvedQueue: [...state.resolvedQueue, card] })),
  evictSettingDiffOwner: (bookId, sessionId) =>
    set((state) => ({ evictQueue: [...state.evictQueue, { bookId, sessionId }] })),
  hydrateSettingDiffResolution: (card) =>
    set((state) => ({ hydrateQueue: [...state.hydrateQueue, card] })),

  drainChapterDiff: (): ChapterDiffProposalPayload[] => {
    let drained: ChapterDiffProposalPayload[] = []
    set((state) => {
      drained = state.chapterDiffQueue
      return { chapterDiffQueue: [] }
    })
    return drained
  },
  drainSettingDiff: (): SettingDiffProposalPayload[] => {
    let drained: SettingDiffProposalPayload[] = []
    set((state) => {
      drained = state.settingDiffQueue
      return { settingDiffQueue: [] }
    })
    return drained
  },
  drainResolved: (): SettingDiffCardState[] => {
    let drained: SettingDiffCardState[] = []
    set((state) => {
      drained = state.resolvedQueue
      return { resolvedQueue: [] }
    })
    return drained
  },
  drainEvictions: (): Array<{ bookId: EntityId | null; sessionId: number }> => {
    let drained: Array<{ bookId: EntityId | null; sessionId: number }> = []
    set((state) => {
      drained = state.evictQueue
      return { evictQueue: [] }
    })
    return drained
  },
  drainHydrations: (): SettingDiffHydrationPayload[] => {
    let drained: SettingDiffHydrationPayload[] = []
    set((state) => {
      drained = state.hydrateQueue
      return { hydrateQueue: [] }
    })
    return drained
  },
}))

// ── 非组件调用入口（AI 流副作用 / 会话恢复 / 审阅完成）────────────
export function proposeChapterDiff(payload: ChapterDiffProposalPayload): void {
  useAiProposalBridge.getState().proposeChapterDiff(payload)
}
export function proposeSettingDiff(payload: SettingDiffProposalPayload): void {
  useAiProposalBridge.getState().proposeSettingDiff(payload)
}
export function resolveSettingDiff(card: SettingDiffCardState): void {
  useAiProposalBridge.getState().resolveSettingDiff(card)
}
export function evictSettingDiffOwner(bookId: EntityId | null, sessionId: number): void {
  useAiProposalBridge.getState().evictSettingDiffOwner(bookId, sessionId)
}
export function hydrateSettingDiffResolution(card: SettingDiffHydrationPayload): void {
  useAiProposalBridge.getState().hydrateSettingDiffResolution(card)
}

export function __resetAiProposalBridgeForTests(): void {
  useAiProposalBridge.setState({
    chapterDiffQueue: [],
    settingDiffQueue: [],
    resolvedQueue: [],
    evictQueue: [],
    hydrateQueue: [],
  })
}
