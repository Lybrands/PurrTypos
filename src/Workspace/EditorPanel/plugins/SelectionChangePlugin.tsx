// ─── 插件：暴露选区变化（非空文本选区时）给父组件 ──────────────────

import React from 'react'
import { useLexicalComposerContext } from '@lexical/react/LexicalComposerContext'
import { $getSelection, $isRangeSelection } from 'lexical'

export function SelectionChangePlugin({
  onSelectionChange,
}: {
  onSelectionChange: (payload: { text: string; rect: DOMRect } | null) => void
}) {
  const [editor] = useLexicalComposerContext()
  React.useEffect(() => {
    const root = editor.getRootElement()
    const report = () => {
      editor.getEditorState().read(() => {
        const sel = $getSelection()
        if (!$isRangeSelection(sel) || sel.isCollapsed()) {
          onSelectionChange(null)
          return
        }
        const text = sel.getTextContent()
        if (!text.trim()) {
          onSelectionChange(null)
          return
        }
        const domSel = window.getSelection()
        if (!domSel || domSel.rangeCount === 0) {
          onSelectionChange(null)
          return
        }
        const rect = domSel.getRangeAt(0).getBoundingClientRect()
        if (rect.width === 0 && rect.height === 0) {
          onSelectionChange(null)
          return
        }
        onSelectionChange({ text, rect })
      })
    }
    const unregister = editor.registerUpdateListener(() => {
      report()
    })
    const onBlur = () => {
      // blur 会触发 DOM selection 清空，但 Lexical 选区可能仍在。
      // 延时一帧：若焦点移到受控 UI（如工具条），保留；否则关闭。
      setTimeout(() => {
        const active = document.activeElement as HTMLElement | null
        if (active?.closest('.inline-edit-toolbar') || active?.closest('.inline-edit-popover')) {
          return
        }
        onSelectionChange(null)
      }, 50)
    }
    root?.addEventListener('blur', onBlur, true)
    return () => {
      unregister()
      root?.removeEventListener('blur', onBlur, true)
    }
  }, [editor, onSelectionChange])
  return null
}
