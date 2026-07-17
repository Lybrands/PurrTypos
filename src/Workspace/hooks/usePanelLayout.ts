import React from 'react'

/** 工作区停靠布局状态。AI 居中，辅助面板和正文依次停靠在右侧。 */
export type PanelKey = 'left' | 'conversation' | 'utility' | 'editor'

export interface FloatingState {
  open: boolean
  x: number
  y: number
  width: number
}

const WORKSPACE_PANEL_STORAGE_KEY = 'purrtypos_workspace_layout_v5'

export interface PersistedPanelState {
  left: FloatingState
  conversation: FloatingState
  utility: FloatingState
  editor: FloatingState
}

function defaultRightX(width: number): number {
  if (typeof window === 'undefined') return 800
  return Math.max(0, window.innerWidth - width - 12)
}

const DEFAULT_STATE: PersistedPanelState = {
  // 默认使用窄轨道；悬停即可临时查看，点击则固定展开。
  left: { open: false, x: 0, y: 0, width: 300 },
  // AI 区域内部导航，只通过 AI 内部按钮收起/展开。
  conversation: { open: true, x: 0, y: 0, width: 220 },
  // 点击全书工具或大纲后展开，位于正文栏左侧。
  utility: { open: false, x: 0, y: 0, width: 440 },
  // 正文栏默认固定展开；收起后由右侧悬停轨道临时唤起。
  editor: { open: true, x: defaultRightX(480), y: 0, width: 480 },
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
        utility: merge('utility'),
        editor: merge('editor'),
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
