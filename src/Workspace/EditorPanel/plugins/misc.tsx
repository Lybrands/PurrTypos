// ─── 小型插件集合：编辑器实例暴露 / 聚焦态 / 快捷键 ─────────────────

import React from 'react'
import { useLexicalComposerContext } from '@lexical/react/LexicalComposerContext'
import {
  COMMAND_PRIORITY_HIGH,
  KEY_DOWN_COMMAND,
  type LexicalEditor,
} from 'lexical'

/** 把 Lexical 实例暴露给父组件（供工作台搜索定位等）。 */
export function ExposeLexicalEditorPlugin({ onEditor }: { onEditor: (editor: LexicalEditor | null) => void }) {
  const [editor] = useLexicalComposerContext()
  React.useEffect(() => {
    onEditor(editor)
    return () => onEditor(null)
  }, [editor, onEditor])
  return null
}

/** 聚焦时隐藏 placeholder。 */
export function FocusPlaceholderPlugin({ onFocusChange }: { onFocusChange: (focused: boolean) => void }) {
  const [editor] = useLexicalComposerContext()
  React.useEffect(() => {
    const root = editor.getRootElement()
    if (!root) return
    const handleFocus = () => onFocusChange(true)
    const handleBlur = () => onFocusChange(false)
    root.addEventListener('focus', handleFocus, true)
    root.addEventListener('blur', handleBlur, true)
    return () => {
      root.removeEventListener('focus', handleFocus, true)
      root.removeEventListener('blur', handleBlur, true)
    }
  }, [editor, onFocusChange])
  return null
}

/** \ 键唤起 AI，Escape 关闭。 */
export function KeyPlugin({
  onKeyTrigger,
}: {
  onKeyTrigger?: (key: string, rect: DOMRect) => void
}) {
  const [editor] = useLexicalComposerContext()

  React.useEffect(() => {
    return editor.registerCommand(
      KEY_DOWN_COMMAND,
      (event: KeyboardEvent) => {
        if (event.key === '\\') {
          event.preventDefault()
          const el = editor.getRootElement()
          const rect = el?.getBoundingClientRect() ?? new DOMRect(200, 100, 0, 0)
          onKeyTrigger?.('backslash', rect)
          return true
        }
        if (event.key === 'Escape') {
          const el = editor.getRootElement()
          const rect = el?.getBoundingClientRect() ?? new DOMRect(200, 100, 0, 0)
          onKeyTrigger?.('escape', rect)
          return false
        }
        return false
      },
      COMMAND_PRIORITY_HIGH
    )
  }, [editor, onKeyTrigger])

  return null
}
