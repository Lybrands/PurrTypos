import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import { loggedJsonStorage } from './persistStorage.ts'

/**
 * 工作台停靠面板布局（左章节栏 / 会话栏 / 右正文组合面板）。
 * 替代原 usePanelLayout 手写 localStorage（v7 key 沿用，数据兼容）。
 */

export type PanelKey = 'left' | 'conversation' | 'right'

export interface FloatingState {
  open: boolean
  x: number
  y: number
  width: number
}

function defaultRightX(width: number): number {
  if (typeof window === 'undefined') return 800
  return Math.max(0, window.innerWidth - width - 12)
}

const DEFAULTS = {
  // 章节列表默认展开；收起后侧边保留隐形悬停热区（顶栏图标 + 快捷键兜底入口）。
  left: { open: true, x: 0, y: 0, width: 300 },
  // AI 区域内部导航，只通过 AI 内部按钮收起/展开。
  conversation: { open: true, x: 0, y: 0, width: 220 },
  // 正文与所有辅助功能共享一个右侧多标签面板。
  right: { open: true, x: 0, y: 0, width: 560 },
} satisfies Record<PanelKey, FloatingState>

interface PanelLayoutState {
  left: FloatingState
  conversation: FloatingState
  right: FloatingState
  updatePanel: (key: PanelKey, patch: Partial<FloatingState>) => void
}

export const usePanelLayoutStore = create<PanelLayoutState>()(
  persist(
    (set) => ({
      ...DEFAULTS,
      right: { ...DEFAULTS.right, x: defaultRightX(DEFAULTS.right.width) },
      updatePanel: (key, patch) =>
        set((state) => ({ [key]: { ...state[key], ...patch } }) as Pick<PanelLayoutState, PanelKey>),
    }),
    {
      name: 'purrtypos_workspace_layout_v7',
      storage: loggedJsonStorage,
      partialize: (state) => ({
        left: state.left,
        conversation: state.conversation,
        right: state.right,
      }),
      merge: (persisted, current) => {
        const value = persisted as Partial<Record<PanelKey, FloatingState>> | undefined
        const merged = { ...current } as PanelLayoutState
        for (const key of ['left', 'conversation', 'right'] as const) {
          const incoming = value?.[key]
          if (!incoming || typeof incoming !== 'object') continue
          merged[key] = {
            open: !!incoming.open,
            x: typeof incoming.x === 'number' ? incoming.x : DEFAULTS[key].x,
            y: typeof incoming.y === 'number' ? incoming.y : DEFAULTS[key].y,
            width: typeof incoming.width === 'number' ? incoming.width : DEFAULTS[key].width,
          }
        }
        return merged
      },
    },
  ),
)

/** 非组件调用入口 */
export const updatePanelLayout = (key: PanelKey, patch: Partial<FloatingState>) =>
  usePanelLayoutStore.getState().updatePanel(key, patch)

export function __resetPanelLayoutStoreForTests(): void {
  usePanelLayoutStore.setState({ ...DEFAULTS })
}
