// ─── 插件：Ghost Text 触发检测 ──────────────────────────────────
// - selection 折叠且空闲 `idleMs` 毫秒后触发 onIdle（携带光标前缀 + cursor DOM rect）
// - 任何 update（编辑或光标移动）触发 onChangeAny，供父组件即时 cancel ghost

import React from 'react'
import { useLexicalComposerContext } from '@lexical/react/LexicalComposerContext'
import { $getRoot, $getSelection, $isRangeSelection } from 'lexical'

export function IdleDetectPlugin({
  enabled,
  idleMs = 800,
  onIdle,
  onChangeAny,
}: {
  enabled: boolean
  idleMs?: number
  onIdle: (payload: { prefix: string; cursorRect: DOMRect }) => void
  onChangeAny: () => void
}) {
  const [editor] = useLexicalComposerContext()
  const timerRef = React.useRef<ReturnType<typeof setTimeout> | null>(null)
  const lastTriggerPrefixRef = React.useRef<string | null>(null)

  React.useEffect(() => {
    if (!enabled) {
      if (timerRef.current) clearTimeout(timerRef.current)
      lastTriggerPrefixRef.current = null
      return
    }
    const clearTimer = () => {
      if (timerRef.current) {
        clearTimeout(timerRef.current)
        timerRef.current = null
      }
    }

    const unregister = editor.registerUpdateListener(({ editorState, tags }) => {
      // 内部 load / remote 等 tag 不触发
      if (tags.has('history-merge')) return
      onChangeAny()
      clearTimer()

      // 800ms idle 后再检查状态
      timerRef.current = setTimeout(() => {
        editorState.read(() => {
          const sel = $getSelection()
          if (!$isRangeSelection(sel) || !sel.isCollapsed()) return
          const root = editor.getRootElement()
          if (!root) return
          if (document.activeElement !== root) return
          const fullText = $getRoot().getTextContent()
          if (!fullText.trim()) return
          // 用选区前的文本做 prefix；Lexical 没有直接 offset，
          // 用 anchor 所在 textNode + 之前所有节点拼接。
          const anchorNode = sel.anchor.getNode()
          const anchorOffset = sel.anchor.offset
          let prefix = ''
          const children = $getRoot().getChildren()
          for (const child of children) {
            const desc = child.getTextContent()
            if (child.getKey() === anchorNode.getTopLevelElement()?.getKey()) {
              // 粗粒度：段落前的全部 + 本段落 anchorOffset 之前
              // （多节点 inline 的精细 offset 在 MVP 暂忽略）
              prefix += desc.slice(0, anchorOffset)
              break
            }
            prefix += desc + '\n'
          }
          if (prefix.length < 4) return
          if (prefix === lastTriggerPrefixRef.current) return
          lastTriggerPrefixRef.current = prefix
          const domSel = window.getSelection()
          if (!domSel || domSel.rangeCount === 0) return
          const rect = domSel.getRangeAt(0).getBoundingClientRect()
          onIdle({ prefix, cursorRect: rect })
        })
      }, idleMs)
    })
    return () => {
      unregister()
      clearTimer()
    }
  }, [editor, enabled, idleMs, onIdle, onChangeAny])

  return null
}
