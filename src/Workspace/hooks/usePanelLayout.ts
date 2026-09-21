import React from 'react'
import {
  usePanelLayoutStore,
  type FloatingState,
  type PanelKey,
} from '../../stores/panelLayoutStore'

export type { FloatingState, PanelKey }

/** 工作区停靠布局状态：薄适配层，状态与持久化在 panelLayoutStore。 */
export function usePanelLayout() {
  const panelState = usePanelLayoutStore()
  const updateFloating = React.useCallback(
    (key: PanelKey, patch: Partial<FloatingState>) => {
      usePanelLayoutStore.getState().updatePanel(key, patch)
    },
    [],
  )
  const toggleFloating = React.useCallback((key: PanelKey) => {
    const current = usePanelLayoutStore.getState()[key]
    usePanelLayoutStore.getState().updatePanel(key, { open: !current.open })
  }, [])
  const closeFloating = React.useCallback((key: PanelKey) => {
    usePanelLayoutStore.getState().updatePanel(key, { open: false })
  }, [])
  return { panelState, updateFloating, toggleFloating, closeFloating }
}
