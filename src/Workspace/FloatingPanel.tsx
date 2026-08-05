import React from 'react'
import { PurrButton, PurrTooltip } from '@/purr-components'
import { CloseIcon } from '@/purr-components'
import './FloatingPanel.scss'

/**
 * 悬浮面板容器：用于把「设定」「写作」两个 panel 浮在 AI 主区域之上。
 *
 * 设计要点：
 * - 绝对定位在 .app-body 内，不会盖住顶部 AppHeader。
 * - 拖动 header 改变位置；左/右把手改变宽度（高度始终撑满 app-body）。
 * - 浮窗永远「钉住」展示：点击外部不会自动收起，必须显式点击 × 或再按侧贴线/快捷键关闭。
 *   早期还有 pinned 切换按钮，现已移除（语义被简化为单一状态）。
 * - 关闭仅触发 onClose，外层负责把 open 置 false。
 * - 默认带半透明阴影背景，强调「浮在 AI 之上」。
 */

export interface FloatingPanelProps {
  side: 'left' | 'right'
  title: React.ReactNode
  /** px。受控。父组件应持久化到 localStorage */
  x: number
  y: number
  width: number
  onPositionChange: (next: { x: number; y: number; width: number }) => void
  onClose: () => void
  children: React.ReactNode
}

const MIN_WIDTH = 280
const MAX_WIDTH_RATIO = 0.7
const MIN_X = 0
const TOP_OFFSET = 0
const HANDLE_SIZE = 6
/* 与 FloatingPanel.scss 的 z-index: 50 对齐；点击置顶时从这里向上递增 */
const Z_INDEX_BASE = 50

/** 跨实例共享的置顶计数器：mousedown 的浮窗拿最大值，浮到其它浮窗之上 */
let zIndexCounter = Z_INDEX_BASE

type DragMode = 'move' | 'resize-left' | 'resize-right' | null

export default function FloatingPanel({
  side,
  title,
  x,
  y,
  width,
  onPositionChange,
  onClose,
  children,
}: FloatingPanelProps) {
  const ref = React.useRef<HTMLDivElement>(null)
  const dragRef = React.useRef<{
    mode: DragMode
    startX: number
    startY: number
    startPanelX: number
    startPanelY: number
    startWidth: number
  } | null>(null)

  /** 拖动结束后写一次回调；过程中走 ref + DOM transform 避免 re-render 抖动 */
  const liveRef = React.useRef({ x, y, width })
  React.useEffect(() => {
    liveRef.current = { x, y, width }
  }, [x, y, width])

  const [zIndex, setZIndex] = React.useState(() => ++zIndexCounter)
  const zIndexRef = React.useRef(zIndex)
  React.useEffect(() => {
    zIndexRef.current = zIndex
  }, [zIndex])
  const bringToFront = React.useCallback(() => {
    // 已经在最顶层就不再抬升，避免计数器无意义增长
    if (zIndexRef.current === zIndexCounter) return
    zIndexCounter += 1
    setZIndex(zIndexCounter)
  }, [])

  /**
   * 持久化坐标可能是在更大的窗口里拖出来的：挂载与窗口 resize 时
   * 把 x/y/width 收回容器内，避免浮窗整体跑到视口外、看起来像「打不开」。
   * （拖拽过程中的边界约束在 onMove 里，这里只兜挂载/缩窗两个时机。）
   */
  React.useEffect(() => {
    const clampIntoContainer = () => {
      if (dragRef.current) return
      const container = ref.current?.parentElement
      if (!container) return
      const rect = container.getBoundingClientRect()
      if (rect.width <= 0 || rect.height <= 0) return

      const cur = liveRef.current
      const width = Math.max(MIN_WIDTH, Math.min(cur.width, rect.width * MAX_WIDTH_RATIO))
      const x = Math.max(MIN_X, Math.min(cur.x, rect.width - width))
      const y = Math.max(TOP_OFFSET, Math.min(cur.y, rect.height - 80))
      if (x !== cur.x || y !== cur.y || width !== cur.width) {
        onPositionChange({ x, y, width })
      }
    }
    clampIntoContainer()
    window.addEventListener('resize', clampIntoContainer)
    return () => window.removeEventListener('resize', clampIntoContainer)
  }, [onPositionChange])

  const startDrag = React.useCallback(
    (mode: Exclude<DragMode, null>) => (e: React.MouseEvent) => {
      e.preventDefault()
      dragRef.current = {
        mode,
        startX: e.clientX,
        startY: e.clientY,
        startPanelX: liveRef.current.x,
        startPanelY: liveRef.current.y,
        startWidth: liveRef.current.width,
      }
      document.body.style.userSelect = 'none'
    },
    [],
  )

  React.useEffect(() => {
    const onMove = (e: MouseEvent) => {
      const d = dragRef.current
      if (!d) return
      const dx = e.clientX - d.startX
      const dy = e.clientY - d.startY

      const container = ref.current?.parentElement
      const containerRect = container?.getBoundingClientRect()
      const maxW = containerRect ? containerRect.width * MAX_WIDTH_RATIO : 1200
      const containerW = containerRect?.width ?? 1600
      const containerH = containerRect?.height ?? 900

      let next = { ...liveRef.current }

      if (d.mode === 'move') {
        next.x = Math.max(MIN_X, Math.min(d.startPanelX + dx, containerW - next.width))
        next.y = Math.max(TOP_OFFSET, Math.min(d.startPanelY + dy, containerH - 80))
      } else if (d.mode === 'resize-right') {
        next.width = Math.max(MIN_WIDTH, Math.min(d.startWidth + dx, maxW))
      } else if (d.mode === 'resize-left') {
        const w = Math.max(MIN_WIDTH, Math.min(d.startWidth - dx, maxW))
        const dW = w - d.startWidth
        next.width = w
        next.x = Math.max(MIN_X, d.startPanelX - dW)
      }

      liveRef.current = next
      const el = ref.current
      if (el) {
        el.style.transform = `translate(${next.x}px, ${next.y}px)`
        el.style.width = `${next.width}px`
      }
    }
    const onUp = () => {
      if (!dragRef.current) return
      dragRef.current = null
      document.body.style.userSelect = ''
      onPositionChange({ ...liveRef.current })
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
    return () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
  }, [onPositionChange])

  return (
    <div
      ref={ref}
      className={`floating-panel floating-panel--${side}`}
      style={{
        transform: `translate(${x}px, ${y}px)`,
        width,
        zIndex,
      }}
      onMouseDownCapture={bringToFront}
    >
      <div className="floating-panel-header" onMouseDown={startDrag('move')}>
        <span className="floating-panel-title">{title}</span>
        <div className="floating-panel-actions" onMouseDown={(e) => e.stopPropagation()}>
          <PurrTooltip title="关闭">
            <PurrButton
              type="text"
              size="small"
              icon={<CloseIcon style={{ fontSize: 14 }} />}
              onClick={onClose}
              className="floating-panel-action"
            />
          </PurrTooltip>
        </div>
      </div>

      <div className="floating-panel-body">{children}</div>

      {/* resize 把手 */}
      <div
        className="floating-panel-resize floating-panel-resize--left"
        onMouseDown={startDrag('resize-left')}
        style={{ width: HANDLE_SIZE }}
      />
      <div
        className="floating-panel-resize floating-panel-resize--right"
        onMouseDown={startDrag('resize-right')}
        style={{ width: HANDLE_SIZE }}
      />
    </div>
  )
}
