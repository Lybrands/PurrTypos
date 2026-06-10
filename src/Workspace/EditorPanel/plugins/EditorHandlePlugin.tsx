// ─── 插件：暴露 undo/redo/reformat 等命令给父组件 ref ─────────────────

import React from 'react'
import { useLexicalComposerContext } from '@lexical/react/LexicalComposerContext'
import {
  $getRoot,
  $createParagraphNode,
  $createTextNode,
  $createRangeSelection,
  $setSelection,
  $getSelection,
  $isRangeSelection,
  UNDO_COMMAND,
  REDO_COMMAND,
  type RangeSelection,
} from 'lexical'
import { getEditorText, reformatArticleText } from './editorText'

export interface LexicalEditorHandle {
  undo: () => void
  redo: () => void
  /**
   * 一键排版：去除段落首行空白、删除段落间空行。
   * 返回本次排版的统计；若无变更则 changed=false。
   * 保留编辑历史（可撤销）。
   */
  reformat: () => {
    changed: boolean
    removedEmptyLines: number
    strippedIndents: number
  }
  /**
   * 把文本插入到当前光标位置：
   * - 若有选区，先替换选区
   * - 文本中的 `\n` 会拆为新段落
   * - 没有选区或聚焦时：默认追加到末尾
   * 保留编辑历史（可撤销）。
   */
  insertAtCursor: (text: string) => void
  /**
   * 捕获当前选区，返回：
   * - `text`：选中文本
   * - `restore()`：重新把同一范围设为选区
   * - `replace(newText)`：基于快照范围替换为新文本
   * 无选区（collapsed / 空）返回 null。
   */
  captureSelection: () => {
    text: string
    restore: () => void
    replace: (newText: string) => void
  } | null
  /** 读取当前选区纯文本；无选区返回空串。 */
  getSelectedText: () => string
  /** 聚焦编辑器。 */
  focus: () => void
}

export function EditorHandlePlugin({ parentRef }: { parentRef: React.Ref<LexicalEditorHandle | null> }) {
  const [editor] = useLexicalComposerContext()
  React.useImperativeHandle(
    parentRef,
    () => ({
      undo: () => editor.dispatchCommand(UNDO_COMMAND, undefined),
      redo: () => editor.dispatchCommand(REDO_COMMAND, undefined),
      reformat: () => {
        const before = getEditorText(editor)
        const after = reformatArticleText(before)
        if (after === before) {
          return { changed: false, removedEmptyLines: 0, strippedIndents: 0 }
        }
        const beforeLines = before.split('\n')
        const afterLines = after ? after.split('\n') : []
        const removedEmptyLines = beforeLines.length - afterLines.length
        let strippedIndents = 0
        for (const line of beforeLines) {
          if (/^[\u0020\t\u00A0\u3000]+/.test(line) && line.trim().length > 0) {
            strippedIndents += 1
          }
        }
        editor.update(() => {
          const root = $getRoot()
          root.clear()
          if (!after) {
            root.append($createParagraphNode())
            return
          }
          for (const line of afterLines) {
            const para = $createParagraphNode()
            para.append($createTextNode(line))
            root.append(para)
          }
        })
        return { changed: true, removedEmptyLines, strippedIndents }
      },
      insertAtCursor: (text: string) => {
        if (!text) return
        const lines = text.split('\n')
        editor.focus()
        editor.update(() => {
          const selection = $getSelection()
          if ($isRangeSelection(selection)) {
            // 多行：第一行直接插入到当前光标，后续行作为新段落
            // 利用 selection.insertText 处理首行；后续行通过 insertParagraph 插入
            selection.insertText(lines[0])
            for (let i = 1; i < lines.length; i++) {
              const sel = $getSelection()
              if ($isRangeSelection(sel)) {
                sel.insertParagraph()
                sel.insertText(lines[i])
              }
            }
          } else {
            // 没有选区：追加到 root 末尾
            const root = $getRoot()
            for (const line of lines) {
              const para = $createParagraphNode()
              para.append($createTextNode(line))
              root.append(para)
            }
          }
        })
      },
      captureSelection: () => {
        let result:
          | {
              text: string
              restore: () => void
              replace: (newText: string) => void
            }
          | null = null
        editor.getEditorState().read(() => {
          const sel = $getSelection()
          if (!$isRangeSelection(sel) || sel.isCollapsed()) return
          const text = sel.getTextContent()
          if (!text) return
          const anchorKey = sel.anchor.key
          const anchorOffset = sel.anchor.offset
          const anchorType = sel.anchor.type
          const focusKey = sel.focus.key
          const focusOffset = sel.focus.offset
          const focusType = sel.focus.type

          const restoreFn = () => {
            editor.update(() => {
              const range = $createRangeSelection()
              range.anchor.set(anchorKey, anchorOffset, anchorType)
              range.focus.set(focusKey, focusOffset, focusType)
              $setSelection(range)
            })
          }
          const replaceFn = (newText: string) => {
            if (!newText) return
            editor.focus()
            editor.update(() => {
              const range = $createRangeSelection()
              range.anchor.set(anchorKey, anchorOffset, anchorType)
              range.focus.set(focusKey, focusOffset, focusType)
              $setSelection(range)
              const current = $getSelection() as RangeSelection | null
              if (!current || !$isRangeSelection(current)) return
              const lines = newText.split('\n')
              current.insertText(lines[0])
              for (let i = 1; i < lines.length; i++) {
                const s = $getSelection()
                if ($isRangeSelection(s)) {
                  s.insertParagraph()
                  s.insertText(lines[i])
                }
              }
            })
          }
          result = { text, restore: restoreFn, replace: replaceFn }
        })
        return result
      },
      getSelectedText: () => {
        let text = ''
        editor.getEditorState().read(() => {
          const sel = $getSelection()
          if ($isRangeSelection(sel) && !sel.isCollapsed()) {
            text = sel.getTextContent()
          }
        })
        return text
      },
      focus: () => editor.focus(),
    }),
    [editor]
  )
  return null
}
