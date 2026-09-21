import React from 'react'

/**
 * 全局快捷键体系：
 * - Ctrl/Cmd+K          命令面板
 * - Ctrl/Cmd+B          展开/收起章节列表
 * - Ctrl/Cmd+E          展开/切回正文面板（正文已激活则收起）
 * - Ctrl/Cmd+Shift+H    打开本章 diff 历史
 */
export function useWorkspaceShortcuts({
  toggleCommandPalette,
  onToggleChapterSidebar,
  onToggleEditorPanel,
}: {
  toggleCommandPalette: () => void
  onToggleChapterSidebar: () => void
  onToggleEditorPanel: () => void
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

      if (!e.shiftKey && (e.key === 'b' || e.key === 'B')) {
        e.preventDefault()
        onToggleChapterSidebar()
        return
      }

      if (!e.shiftKey && (e.key === 'e' || e.key === 'E')) {
        e.preventDefault()
        onToggleEditorPanel()
        return
      }

      if (!e.shiftKey) return

      const key = e.key
      if (key === 'H' || key === 'h') {
        e.preventDefault()
        window.dispatchEvent(new CustomEvent('editor-open-diff-history'))
        return
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [toggleCommandPalette, onToggleChapterSidebar, onToggleEditorPanel])
}
