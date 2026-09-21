import React from 'react'
import { PurrButton, PurrTooltip } from '@/purr-components'
import {
  AiChatIcon,
  HighlightIcon,
  LinkIcon,
} from '@/purr-components'

const GAP = 8
const BUBBLE_HEIGHT = 36
const VIEWPORT_MARGIN = 8

/**
 * 选区上方浮出的快捷工具条。
 * - 入口均为单图标：AI（改写/提问统一弹层）· 批注 · 引用，语义靠 tooltip
 * - 位置自适应视口：上方空间不足翻到下方；左右越界时向内收（测量后钳制）
 */
export default function SelectionBubble({
  rect,
  onAi,
  onAnnotate,
  onQuote,
}: {
  rect: DOMRect
  onAi: () => void
  onAnnotate: () => void
  onQuote: () => void
}) {
  const toolbarRef = React.useRef<HTMLDivElement>(null)

  // 期望位置：选区上方居中，不够则翻到下方
  const preferred = React.useMemo(() => {
    const top =
      rect.top - BUBBLE_HEIGHT - GAP > VIEWPORT_MARGIN
        ? rect.top - BUBBLE_HEIGHT - GAP
        : rect.bottom + GAP
    return {
      top,
      left: rect.left + rect.width / 2,
    }
  }, [rect])

  // 渲染后按实测宽度把中心点钳进视口，避免左右溢出屏幕
  const [pos, setPos] = React.useState(preferred)
  React.useLayoutEffect(() => {
    const el = toolbarRef.current
    const width = el?.offsetWidth ?? 0
    const height = el?.offsetHeight ?? BUBBLE_HEIGHT
    const minLeft = VIEWPORT_MARGIN + width / 2
    const maxLeft = Math.max(minLeft, window.innerWidth - VIEWPORT_MARGIN - width / 2)
    const minTop = VIEWPORT_MARGIN
    const maxTop = Math.max(minTop, window.innerHeight - VIEWPORT_MARGIN - height)
    setPos({
      top: Math.min(Math.max(preferred.top, minTop), maxTop),
      left: Math.min(Math.max(preferred.left, minLeft), maxLeft),
    })
  }, [preferred])

  return (
    <div
      ref={toolbarRef}
      className="inline-edit-toolbar"
      style={{ position: 'fixed', top: pos.top, left: pos.left, transform: 'translateX(-50%)' }}
      onMouseDown={(e) => {
        // 阻止 mousedown 抢走 editor 焦点，否则会导致选区闪失。
        e.preventDefault()
      }}
    >
      <PurrTooltip title="就选中文段改写或提问（弹层内可切换处理方式）">
        <PurrButton
          type="text"
          size="small"
          className="inline-edit-toolbar-btn"
          icon={<AiChatIcon size={15} />}
          aria-label="AI 改写或提问"
          onClick={onAi}
        />
      </PurrTooltip>
      <div className="inline-edit-toolbar-sep" />
      <PurrTooltip title="给选中文段添加批注">
        <PurrButton
          type="text"
          size="small"
          className="inline-edit-toolbar-btn"
          icon={<HighlightIcon size={15} />}
          aria-label="批注"
          onClick={onAnnotate}
        />
      </PurrTooltip>
      <PurrTooltip title="引用选中文段到 AI 对话（可同时引用多条）">
        <PurrButton
          type="text"
          size="small"
          className="inline-edit-toolbar-btn"
          icon={<LinkIcon size={15} />}
          aria-label="引用"
          onClick={onQuote}
        />
      </PurrTooltip>
    </div>
  )
}
