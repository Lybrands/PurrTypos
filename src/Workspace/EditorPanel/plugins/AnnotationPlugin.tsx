// ─── 插件：批注高亮层 ────────────────────────────────────────────
// 锚定不走 Lexical 节点标记（纯文本序列化会丢），而是按引文在当前全文中
// 解析范围（annotationAnchor），再用 DOM Range 的行矩形在 .editor-lexical-wrap
// 上叠高亮块（WordRulerPlugin 同款 portal + rAF/ResizeObserver/scroll 重算）。

import React from 'react'
import ReactDOM from 'react-dom'
import { useLexicalComposerContext } from '@lexical/react/LexicalComposerContext'
import { $getRoot } from 'lexical'
import type { ChapterAnnotation } from '../../../types'
import { resolveAnnotationRange } from '../annotationAnchor'

interface HighlightBlock {
  top: number
  left: number
  width: number
  height: number
}

interface AnnotationHighlight {
  id: number
  status: ChapterAnnotation['status']
  blocks: HighlightBlock[]
}

/** 在段落 DOM 内把段内字符偏移定位到具体 Text 节点 */
function textPosition(el: HTMLElement, offset: number): { node: Text; offset: number } | null {
  let remaining = offset
  const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT)
  let last: Text | null = null
  for (let node = walker.nextNode() as Text | null; node; node = walker.nextNode() as Text | null) {
    last = node
    const len = node.data.length
    if (remaining <= len) return { node, offset: Math.max(0, Math.min(remaining, len)) }
    remaining -= len
  }
  if (last) return { node: last, offset: last.data.length }
  return null
}

export function AnnotationPlugin({
  annotations,
  onAnnotationClick,
}: {
  annotations: ChapterAnnotation[]
  onAnnotationClick?: (annotation: ChapterAnnotation) => void
}) {
  const [editor] = useLexicalComposerContext()
  const [highlights, setHighlights] = React.useState<AnnotationHighlight[]>([])
  const [wrapEl, setWrapEl] = React.useState<HTMLElement | null>(null)

  React.useEffect(() => {
    let raf = 0
    const compute = () => {
      const editorEl = editor.getRootElement()
      if (!editorEl) {
        setHighlights([])
        return
      }
      const wrap = editorEl.closest('.editor-lexical-wrap') as HTMLElement | null
      if (!wrap) return
      setWrapEl(wrap)
      const wrapRect = wrap.getBoundingClientRect()

      const next: AnnotationHighlight[] = []
      editor.getEditorState().read(() => {
        const children = $getRoot().getChildren()
        const paras: { key: string; start: number; text: string }[] = []
        let pos = 0
        for (let i = 0; i < children.length; i++) {
          const text = children[i].getTextContent()
          paras.push({ key: children[i].getKey(), start: pos, text })
          pos += text.length + (i < children.length - 1 ? 1 : 0)
        }
        const flat = paras.map((p) => p.text).join('\n')

        for (const a of annotations) {
          const range = resolveAnnotationRange(flat, a)
          if (!range) continue
          const blocks: HighlightBlock[] = []
          for (const p of paras) {
            const ps = Math.max(range.start, p.start)
            const pe = Math.min(range.end, p.start + p.text.length)
            if (ps >= pe) continue
            const paraEl = editor.getElementByKey(p.key) as HTMLElement | null
            if (!paraEl) continue
            const startPt = textPosition(paraEl, ps - p.start)
            const endPt = textPosition(paraEl, pe - p.start)
            if (!startPt || !endPt) continue
            try {
              const domRange = document.createRange()
              domRange.setStart(startPt.node, startPt.offset)
              domRange.setEnd(endPt.node, endPt.offset)
              for (const r of Array.from(domRange.getClientRects())) {
                if (r.width <= 0 || r.height <= 0) continue
                blocks.push({
                  top: r.top - wrapRect.top,
                  left: r.left - wrapRect.left,
                  width: r.width,
                  height: r.height,
                })
              }
            } catch {
              // DOM 定位失败（段落重排瞬间）忽略本段，等下一轮重算
            }
          }
          if (blocks.length) next.push({ id: a.id, status: a.status, blocks })
        }
      })
      setHighlights(next)
    }

    const schedule = () => {
      if (raf) cancelAnimationFrame(raf)
      raf = requestAnimationFrame(compute)
    }

    schedule()
    const unregister = editor.registerUpdateListener(() => schedule())
    const ro = new ResizeObserver(schedule)
    const editorEl = editor.getRootElement()
    const wrap = editorEl?.closest('.editor-lexical-wrap') as HTMLElement | null
    if (editorEl) ro.observe(editorEl)
    if (wrap) wrap.addEventListener('scroll', schedule)
    window.addEventListener('resize', schedule)

    return () => {
      unregister()
      ro.disconnect()
      wrap?.removeEventListener('scroll', schedule)
      window.removeEventListener('resize', schedule)
      if (raf) cancelAnimationFrame(raf)
    }
  }, [editor, annotations])

  const byId = React.useMemo(
    () => new Map(annotations.map((a) => [a.id, a])),
    [annotations],
  )

  if (!wrapEl || highlights.length === 0) return null
  return ReactDOM.createPortal(
    <div className="annotation-highlight-layer" aria-hidden={false}>
      {highlights.map((h) =>
        h.blocks.map((b, i) => (
          <span
            key={`${h.id}-${i}`}
            className={`annotation-highlight${h.status === 'resolved' ? ' is-resolved' : ''}`}
            style={{ top: b.top, left: b.left, width: b.width, height: b.height }}
            role="note"
            aria-label="批注"
            title={byId.get(h.id)?.note}
            onPointerDown={(event) => {
              // 阻止编辑器抢焦点丢失选区；点击穿透为「查看批注」
              event.preventDefault()
              const annotation = byId.get(h.id)
              if (annotation) onAnnotationClick?.(annotation)
            }}
          />
        )),
      )}
    </div>,
    wrapEl,
  )
}
