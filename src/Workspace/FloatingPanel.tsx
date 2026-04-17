import React from 'react'
import { Button, Tooltip } from 'antd'
import { CloseOutlined, PushpinOutlined, PushpinFilled } from '@ant-design/icons'
import './FloatingPanel.scss'

/**
 * 悬浮面板容器：用于把「设定」「写作」两个 panel 浮在 AI 主区域之上。
 *
 * 设计要点：
 * - 绝对定位在 .app-body 内，不会盖住顶部 AppHeader。
 * - 拖动 header 改变位置；左/右把手改变宽度（高度始终撑满 app-body）。
 * - 钉住（pinned）= 持久显示；非钉住状态下，外层可在用户点击外部时关闭。
 * - 关闭仅触发 onClose，外层负责把 open 置 false。
 * - 默认带半透明阴影背景，强调"浮在 AI 之上"。
 */

export interface FloatingPanelProps {
  side: 'left' | 'right'
  title: React.ReactNode
  /** px。受控。父组件应持久化到 localStorage */
  x: number
  y: number
  width: number
  onPositionChange: (next: { x: number; y: number; width: number }) => void
  pinned: boolean
  onTogglePin: () => void
  onClose: () => void
  children: React.ReactNode
}

const MIN_WIDTH = 280
const MAX_WIDTH_RATIO = 0.7
const MIN_X = 0
const TOP_OFFSET = 0
const HANDLE_SIZE = 6

type DragMode = 'move' | 'resize-left' | 'resize-right' | null

export default function FloatingPanel({
  side,
  title,
  x,
  y,
  width,
  onPositionChange,
  pinned,
  onTogglePin,
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
      className={`floating-panel floating-panel--${side} ${pinned ? 'is-pinned' : ''}`}
      style={{
        transform: `translate(${x}px, ${y}px)`,
        width,
      }}
    >
      <div className="floating-panel-header" onMouseDown={startDrag('move')}>
        <span className="floating-panel-title">{title}</span>
        <div className="floating-panel-actions" onMouseDown={(e) => e.stopPropagation()}>
          <Tooltip title={pinned ? '取消钉住（点 AI 区域将关闭浮窗）' : '钉住浮窗（点击外部不会关闭）'}>
            <Button
              type="text"
              size="small"
              icon={pinned ? <PushpinFilled style={{ fontSize: 14 }} /> : <PushpinOutlined style={{ fontSize: 14 }} />}
              onClick={onTogglePin}
              className={`floating-panel-action ${pinned ? 'is-active' : ''}`}
            />
          </Tooltip>
          <Tooltip title="关闭">
            <Button
              type="text"
              size="small"
              icon={<CloseOutlined style={{ fontSize: 14 }} />}
              onClick={onClose}
              className="floating-panel-action"
            />
          </Tooltip>
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
