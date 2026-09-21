import { type LexicalEditor } from 'lexical'
import { editorStateToText } from '../EditorPanel/LexicalEditor'
import {
  $applyFlatSelection,
  findAllMatchStarts,
} from '../EditorPanel/selectionAnchor'

export { findAllMatchStarts }

/**
 * 将选区矩形滚入可视区域（与 Lexical 内部 scrollIntoViewIfNeeded 同源思路）：
 * 从 contenteditable 根节点向上遍历，对可滚动祖先调整 scrollTop，必要时滚动 window。
 */
function scrollSelectionRectIntoView(rootElement: HTMLElement, selectionRect: DOMRectReadOnly): void {
  const doc = rootElement.ownerDocument
  const defaultView = doc.defaultView
  if (!defaultView) return

  let currentTop = selectionRect.top
  let currentBottom = selectionRect.bottom
  let element: HTMLElement | null = rootElement

  while (element != null) {
    const isBody = element === doc.body
    let targetTop: number
    let targetBottom: number
    if (isBody) {
      targetTop = 0
      targetBottom = defaultView.innerHeight
    } else {
      const er = element.getBoundingClientRect()
      targetTop = er.top
      targetBottom = er.bottom
    }
    let diff = 0
    if (currentTop < targetTop) {
      diff = -(targetTop - currentTop)
    } else if (currentBottom > targetBottom) {
      diff = currentBottom - targetBottom
    }
    if (diff !== 0) {
      if (isBody) {
        defaultView.scrollBy({ top: diff, behavior: 'smooth' })
      } else {
        const prev = element.scrollTop
        element.scrollTop += diff
        const yOffset = element.scrollTop - prev
        currentTop -= yOffset
        currentBottom -= yOffset
      }
    }
    if (isBody) break
    element = element.parentElement
  }
}

function getRangeClientRect(range: Range): DOMRect | null {
  const r = range.getBoundingClientRect()
  if (r.width > 0 || r.height > 0) return r
  const rects = range.getClientRects()
  if (rects.length === 0) return null
  return rects[0]
}

export function selectLexicalSearchMatch(editor: LexicalEditor, query: string, matchIndex: number) {
  const q = query.trim()
  if (!q) return
  editor.update(() => {
    const flat = editorStateToText(editor.getEditorState())
    const starts = findAllMatchStarts(flat, q)
    if (matchIndex < 0 || matchIndex >= starts.length) return
    const s = starts[matchIndex]
    const e = s + q.length
    $applyFlatSelection(s, e)
  })

  const rootEl = editor.getRootElement()
  if (rootEl) {
    rootEl.focus({ preventScroll: true })
  } else {
    editor.focus()
  }

  queueMicrotask(() => {
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        const root = editor.getRootElement()
        if (!root) return
        const domSel = window.getSelection()
        if (!domSel || domSel.rangeCount === 0) return
        const range = domSel.getRangeAt(0)
        const rect = getRangeClientRect(range)
        if (!rect) return
        scrollSelectionRectIntoView(root, rect)
      })
    })
  })
}
