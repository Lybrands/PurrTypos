import { create } from 'zustand'

/**
 * 设定数据失效通知：AI 写工具 / 设定 diff 提交改动了人物、实体或背景后，
 * 各设定面板按 kind 订阅修订号自行重载。
 * 替代原 `setting-updated` CustomEvent 扇出（4 监听者）。
 */

export type SettingsKind = 'character' | 'entity' | 'background'

interface SettingsInvalidationState {
  /** 全局序号（任何 kind 变化都会递增；供“任何设定变了都要刷”的消费者） */
  seq: number
  revisions: Record<SettingsKind, number>
  lastUpdate?: { kind: SettingsKind; id?: string | number; name?: string }
  notify(kind: SettingsKind, detail?: { id?: string | number; name?: string }): void
}

export const useSettingsInvalidationStore = create<SettingsInvalidationState>(
  (set) => ({
    seq: 0,
    revisions: { character: 0, entity: 0, background: 0 },
    notify: (kind, detail) =>
      set((state) => ({
        seq: state.seq + 1,
        revisions: { ...state.revisions, [kind]: state.revisions[kind] + 1 },
        lastUpdate: { kind, id: detail?.id, name: detail?.name },
      })),
  }),
)

/** 非组件调用入口（AI 流副作用 / 设定 diff 提交） */
export function notifySettingsUpdated(
  kind: SettingsKind,
  detail?: { id?: string | number; name?: string },
): void {
  useSettingsInvalidationStore.getState().notify(kind, detail)
}

/** 订阅某类设定的修订号（仅该类变更时变化） */
export function useSettingsRevision(kind: SettingsKind): number {
  return useSettingsInvalidationStore((state) => state.revisions[kind])
}

export function __resetSettingsInvalidationStoreForTests(): void {
  useSettingsInvalidationStore.setState({
    seq: 0,
    revisions: { character: 0, entity: 0, background: 0 },
    lastUpdate: undefined,
  })
}
