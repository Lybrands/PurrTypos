// ─── 插件：章节切换时加载内容 ────────────────────────────────────
// 配合父组件的 key={chapterId}：章节切换时组件完整重建
// 此插件额外处理「内容异步到达」的情况（初次 mount 时 value 可能是空字符串）

import React from 'react'
import { useLexicalComposerContext } from '@lexical/react/LexicalComposerContext'
import { $getRoot } from 'lexical'
import { getEditorText, textToEditorState } from './editorText'

export function LoadContentPlugin({ value }: { value: string }) {
  const [editor] = useLexicalComposerContext()
  const isInitialRef = React.useRef(true)
  /**
   * 记录编辑器最近一次提交后的纯文本。用于辨别 `value` prop 是否源自用户自己的输入。
   *
   * 为何需要：
   * - 用户输入时，Lexical 提交 → onChange 把文本向外回流到父组件的 setState，
   *   随后若父组件因为「其他 state」（如 saveStatus）先一步触发过中间渲染，
   *   父组件重渲染时传下来的 `value` 就可能「暂时落后」于编辑器最新提交的文本；
   *   此时旧逻辑的 `editorText !== value` 会成立 → 触发 textToEditorState(旧 value)
   *   → root.clear() + 以旧文本重建段落，造成「选中替换后，选区外的内容也被吞掉」。
   * - 通过追踪编辑器自身最近提交的文本，只要 `value` 能匹配到近期的「自发出」文本，
   *   就一律跳过重建，即使与 `editorText` 的严格比较不一致也不会误伤。
   */
  const lastEmittedRef = React.useRef<string>('')

  React.useEffect(() => {
    const unregister = editor.registerUpdateListener(({ editorState }) => {
      editorState.read(() => {
        lastEmittedRef.current = $getRoot()
          .getChildren()
          .map((n) => n.getTextContent())
          .join('\n')
      })
    })
    return unregister
  }, [editor])

  React.useEffect(() => {
    if (isInitialRef.current) {
      isInitialRef.current = false
      lastEmittedRef.current = value
      textToEditorState(value, editor)
      return
    }
    // 1) value 与编辑器最近发出的文本一致 → 这是用户输入回环造成的 value 更新，跳过。
    if (value === lastEmittedRef.current) return
    // 2) value 与编辑器当前实际文本一致 → 已经同步过，跳过。
    const editorText = getEditorText(editor)
    if (editorText === value) return
    // 3) 走到这里说明 value 是真正的外部更新（章节异步加载 / AI 写回等），才重建编辑器。
    lastEmittedRef.current = value
    textToEditorState(value, editor)
  }, [value, editor])

  return null
}
