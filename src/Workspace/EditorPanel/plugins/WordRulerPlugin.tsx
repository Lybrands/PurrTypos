// ─── 插件：字数标尺 ──────────────────────────────────────────────

import React from 'react'
import ReactDOM from 'react-dom'
import { useLexicalComposerContext } from '@lexical/react/LexicalComposerContext'
import { $getRoot } from 'lexical'
import { Tooltip } from '../../../ui'

/**
 * 字数标尺插件：每 N 个字（默认 500）在编辑器右侧打一个浮签：「500字 / 1000字 ...」。
 * - 字数定义：去除空白字符（含换行）后的全部字符
 * - 标尺贴在「跨过该阈值的段落」的顶部，scrollTop 一起滚
 * - 一段若跨多个阈值，按发生顺序竖向堆叠排列
 */
export function WordRulerPlugin({ interval = 500 }: { interval?: number }) {
  const [editor] = useLexicalComposerContext()
  const [markers, setMarkers] = React.useState<{ top: number; label: string }[]>([])
  const [wrapEl, setWrapEl] = React.useState<HTMLElement | null>(null)

  React.useEffect(() => {
    if (interval <= 0) return

    let raf = 0
    const compute = () => {
      const editorEl = editor.getRootElement()
      if (!editorEl) {
        setMarkers([])
        return
      }
      const wrap = editorEl.closest('.editor-lexical-wrap') as HTMLElement | null
      if (!wrap) return
      setWrapEl(wrap)

      const next: { top: number; label: string }[] = []
      let cum = 0
      let nextThreshold = interval

      editor.getEditorState().read(() => {
        const root = $getRoot()
        const children = root.getChildren()
        for (const child of children) {
          const text = child.getTextContent()
          // 去掉空白与换行，按可见字符计数
          const charsInThisPara = text.replace(/\s+/g, '').length
          cum += charsInThisPara
          if (cum < nextThreshold) continue

          const paraEl = editor.getElementByKey(child.getKey()) as HTMLElement | null
          if (!paraEl) {
            while (nextThreshold <= cum) nextThreshold += interval
            continue
          }
          // 段相对于 wrap 的纵坐标（offsetParent 应该就是 .editor-lexical-wrap）
          const baseTop = paraEl.offsetTop
          let stackOffset = 0
          while (nextThreshold <= cum) {
            next.push({
              top: baseTop + stackOffset,
              label: `${nextThreshold} 字`,
            })
            nextThreshold += interval
            stackOffset += 22 // 同段多个阈值时往下错开
          }
        }
      })
      setMarkers(next)
    }

    const schedule = () => {
      if (raf) cancelAnimationFrame(raf)
      raf = requestAnimationFrame(compute)
    }

    schedule()
    const unregister = editor.registerUpdateListener(() => schedule())
    const ro = new ResizeObserver(schedule)
    const editorEl = editor.getRootElement()
    if (editorEl) ro.observe(editorEl)
    window.addEventListener('resize', schedule)

    return () => {
      unregister()
      ro.disconnect()
      window.removeEventListener('resize', schedule)
      if (raf) cancelAnimationFrame(raf)
    }
  }, [editor, interval])

  if (!wrapEl || markers.length === 0) return null
  return ReactDOM.createPortal(
    <>
      {markers.map((m, i) => (
        <Tooltip
          key={`${m.label}-${i}`}
          title={m.label}
          placement="left"
          mouseEnterDelay={0.12}
        >
          <span
            className={`lexical-word-ruler${(i + 1) % 2 === 0 ? ' is-major' : ''}`}
            style={{ top: m.top }}
            role="note"
            aria-label={m.label}
            onPointerDown={(event) => event.preventDefault()}
          />
        </Tooltip>
      ))}
    </>,
    wrapEl,
  )
}
