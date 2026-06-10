import React from 'react'
import type { MainPanelKey, PanelKey } from './usePanelLayout'

/**
 * 全局快捷键体系：
 * - Ctrl/Cmd+K          命令面板
 * - Ctrl/Cmd+Shift+1    Toggle「设定」浮窗
 * - Ctrl/Cmd+Shift+2    聚焦 AI 主区
 * - Ctrl/Cmd+Shift+3    Toggle「写作」浮窗
 * - Ctrl/Cmd+Shift+H    打开本章 diff 历史
 */
export function useWorkspaceShortcuts({
  toggleFloating,
  setMain,
  toggleCommandPalette,
}: {
  toggleFloating: (key: PanelKey) => void
  setMain: (key: MainPanelKey) => void
  toggleCommandPalette: () => void
}) {
  React.useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      const isMod = e.ctrlKey || e.metaKey
      if (!isMod) return

      if (!e.shiftKey && (e.key === 'k' || e.key === 'K')) {
        e.preventDefault()
        toggleCommandPalette()
        return
      }

      if (!e.shiftKey) return

      const key = e.key
      const code = e.code

      if (code === 'Digit1') {
        e.preventDefault()
        toggleFloating('left')
        return
      }
      if (code === 'Digit2') {
        e.preventDefault()
        // 聚焦 AI 主区：直接 setMain('ai')，原非 ai 主区会自动转为浮窗
        setMain('ai')
        return
      }
      if (code === 'Digit3') {
        e.preventDefault()
        toggleFloating('editor')
        return
      }

      if (key === 'H' || key === 'h') {
        e.preventDefault()
        window.dispatchEvent(new CustomEvent('editor-open-diff-history'))
        return
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [toggleFloating, setMain, toggleCommandPalette])
}
