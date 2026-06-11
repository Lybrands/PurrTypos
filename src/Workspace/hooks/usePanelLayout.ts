import React from 'react'

/**
 * AI-Centric 工作区的浮窗 / 主区域布局状态（localStorage 持久化）。
 *
 * 规则：
 * - 只有 AI 与 写作（editor）能成为主区域，章节列表 (left) 永远是浮窗；
 * - 切换主区域时，原主区域自动降为钉住浮窗，便于一键切回；
 * - 新主区域的浮窗自动关闭（它现在是主，无须浮窗）。
 */

export type PanelKey = 'ai' | 'left' | 'editor' | 'setting'
/** 能成为主区域的 panel 子集（章节列表只能浮窗） */
export type MainPanelKey = 'ai' | 'editor'

export interface FloatingState {
  open: boolean
  x: number
  y: number
  width: number
}

const WORKSPACE_PANEL_STORAGE_KEY = 'purrtypos_workspace_floating_state_v2'

export interface PersistedPanelState {
  mainPanel: MainPanelKey
  ai: FloatingState
  left: FloatingState
  editor: FloatingState
  setting: FloatingState
}

/** 给 right 浮窗一个估算的 x（窗口 - 自身宽度 - 12 边距），运行时再据容器纠正 */
function defaultRightX(width: number): number {
  if (typeof window === 'undefined') return 800
  return Math.max(0, window.innerWidth - width - 12)
}

const DEFAULT_STATE: PersistedPanelState = {
  mainPanel: 'ai',
  ai: { open: false, x: defaultRightX(560), y: 8, width: 560 },
  left: { open: false, x: 16, y: 8, width: 340 },
  editor: { open: false, x: defaultRightX(620), y: 8, width: 620 },
  setting: { open: false, x: defaultRightX(480), y: 48, width: 480 },
}

function loadPanelState(): PersistedPanelState {
  try {
    const raw = localStorage.getItem(WORKSPACE_PANEL_STORAGE_KEY)
    if (raw) {
      const p = JSON.parse(raw) as Partial<PersistedPanelState>
      const mergeFloating = (key: PanelKey, fallback: FloatingState): FloatingState => {
        const v = p[key]
        if (!v || typeof v !== 'object') return fallback
        return {
          open: !!v.open,
          x: typeof v.x === 'number' ? v.x : fallback.x,
          y: typeof v.y === 'number' ? v.y : fallback.y,
          width: typeof v.width === 'number' ? v.width : fallback.width,
        }
      }
      const main: MainPanelKey =
        p.mainPanel === 'ai' || p.mainPanel === 'editor' ? p.mainPanel : 'ai'
      return {
        mainPanel: main,
        ai: mergeFloating('ai', DEFAULT_STATE.ai),
        left: mergeFloating('left', DEFAULT_STATE.left),
        editor: mergeFloating('editor', DEFAULT_STATE.editor),
        setting: mergeFloating('setting', DEFAULT_STATE.setting),
      }
    }
  } catch {
    // ignore
  }
  return DEFAULT_STATE
}

function savePanelState(state: PersistedPanelState) {
  try {
    localStorage.setItem(WORKSPACE_PANEL_STORAGE_KEY, JSON.stringify(state))
  } catch {
    // ignore
  }
}

export function usePanelLayout() {
  const initialState = React.useMemo(loadPanelState, [])
  const [panelState, setPanelState] = React.useState<PersistedPanelState>(initialState)

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
    setPanelState((prev) => {
      // 如果该 panel 已是主区域，toggle 浮窗无意义
      if (prev.mainPanel === key) return prev
      return { ...prev, [key]: { ...prev[key], open: !prev[key].open } }
    })
  }, [])

  const closeFloating = React.useCallback((key: PanelKey) => {
    setPanelState((prev) => ({ ...prev, [key]: { ...prev[key], open: false } }))
  }, [])

  /**
   * 切换主区域（仅限 AI / Editor 两者互换）：
   * - 如果 next === current，noop
   * - 原主区域自动转为浮窗（open=true）便于一键切回
   * - 新主区域的浮窗自动关闭（它现在是主，无须浮窗）
   *
   * 注：浮窗永远是「钉住」语义（不会被点击外部自动收起），
   *     所以这里不再需要单独写 pinned 字段。
   */
  const setMain = React.useCallback((next: MainPanelKey) => {
    setPanelState((prev) => {
      if (prev.mainPanel === next) return prev
      const prevMain = prev.mainPanel
      return {
        ...prev,
        mainPanel: next,
        [prevMain]: { ...prev[prevMain], open: true },
        [next]: { ...prev[next], open: false },
      }
    })
  }, [])

  return {
    panelState,
    mainPanel: panelState.mainPanel,
    updateFloating,
    toggleFloating,
    closeFloating,
    setMain,
  }
}
