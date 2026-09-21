import React from 'react'

/** 工作区停靠布局状态。AI 居中，左右各使用一个可调整宽度的组合面板。 */
export type PanelKey = 'left' | 'conversation' | 'right'

export interface FloatingState {
  open: boolean
  x: number
  y: number
  width: number
}

const WORKSPACE_PANEL_STORAGE_KEY = 'purrtypos_workspace_layout_v7'

export interface PersistedPanelState {
  left: FloatingState
  conversation: FloatingState
  right: FloatingState
}

function defaultRightX(width: number): number {
  if (typeof window === 'undefined') return 800
  return Math.max(0, window.innerWidth - width - 12)
}

const DEFAULT_STATE: PersistedPanelState = {
  // 章节列表默认展开；收起后侧边保留常显窄条入口（点击展开 / 悬停预览）。
  left: { open: true, x: 0, y: 0, width: 300 },
  // AI 区域内部导航，只通过 AI 内部按钮收起/展开。
  conversation: { open: true, x: 0, y: 0, width: 220 },
  // 正文与所有辅助功能共享一个右侧多标签面板。
  right: { open: true, x: defaultRightX(560), y: 0, width: 560 },
}

function loadPanelState(): PersistedPanelState {
  try {
    const raw = localStorage.getItem(WORKSPACE_PANEL_STORAGE_KEY)
    if (raw) {
      const persisted = JSON.parse(raw) as Partial<PersistedPanelState>
      const merge = (key: PanelKey): FloatingState => {
        const fallback = DEFAULT_STATE[key]
        const value = persisted[key]
        if (!value || typeof value !== 'object') return fallback
        return {
          open: !!value.open,
          x: typeof value.x === 'number' ? value.x : fallback.x,
          y: typeof value.y === 'number' ? value.y : fallback.y,
          width: typeof value.width === 'number' ? value.width : fallback.width,
        }
      }
      return {
        left: merge('left'),
        conversation: merge('conversation'),
        right: merge('right'),
      }
    }
  } catch {
    // 损坏的持久化状态直接回退默认布局。
  }
  return DEFAULT_STATE
}

function savePanelState(state: PersistedPanelState) {
  try {
    localStorage.setItem(WORKSPACE_PANEL_STORAGE_KEY, JSON.stringify(state))
  } catch {
    // localStorage 不可用时不影响工作区使用。
  }
}

export function usePanelLayout() {
  const [panelState, setPanelState] = React.useState<PersistedPanelState>(() => loadPanelState())

  React.useEffect(() => {
    savePanelState(panelState)
  }, [panelState])

  const updateFloating = React.useCallback(
    (key: PanelKey, patch: Partial<FloatingState>) => {
      setPanelState((prev) => ({ ...prev, [key]: { ...prev[key], ...patch } }))
    },
    [],
  )

  const toggleFloating = React.useCallback((key: PanelKey) => {
    setPanelState((prev) => ({
      ...prev,
      [key]: { ...prev[key], open: !prev[key].open },
    }))
  }, [])

  const closeFloating = React.useCallback((key: PanelKey) => {
    setPanelState((prev) => ({ ...prev, [key]: { ...prev[key], open: false } }))
  }, [])

  return { panelState, updateFloating, toggleFloating, closeFloating }
}
