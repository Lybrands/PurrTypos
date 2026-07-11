import React from 'react'
import './DockedPanel.scss'

type DockSide = 'left' | 'right'

interface DockedPanelProps {
  side: DockSide
  width: number
  minWidth: number
  maxWidth: number
  onWidthChange: (width: number) => void
  /** 左侧栏越过最小宽度后触发折叠。 */
  onCollapse?: (width: number) => void
  ariaLabel: string
  children: React.ReactNode
  className?: string
}

/**
 * Stable workspace sidebar with a draggable inner edge.
 * Width is persisted only when dragging finishes, avoiding localStorage writes
 * on every pointer move while still keeping the resize interaction immediate.
 */
export default function DockedPanel({
  side,
  width,
  minWidth,
  maxWidth,
  onWidthChange,
  onCollapse,
  ariaLabel,
  children,
  className = '',
}: DockedPanelProps) {
  const [displayWidth, setDisplayWidth] = React.useState(width)
  const widthRef = React.useRef(width)
  const draggingRef = React.useRef(false)
  const collapseArmedRef = React.useRef(false)
  const [collapseArmed, setCollapseArmed] = React.useState(false)

  React.useEffect(() => {
    if (draggingRef.current) return
    widthRef.current = width
    setDisplayWidth(width)
  }, [width])

  const beginResize = React.useCallback((event: React.PointerEvent<HTMLDivElement>) => {
    event.preventDefault()
    draggingRef.current = true
    const startX = event.clientX
    const startWidth = widthRef.current
    document.body.classList.add('workspace-dock-resizing')

    const handlePointerMove = (moveEvent: PointerEvent) => {
      const delta = moveEvent.clientX - startX
      const requested = side === 'left' ? startWidth + delta : startWidth - delta
      const shouldCollapse = side === 'left' && !!onCollapse && requested <= minWidth - 28
      collapseArmedRef.current = shouldCollapse
      setCollapseArmed(shouldCollapse)
      const nextWidth = Math.min(maxWidth, Math.max(minWidth, requested))
      widthRef.current = nextWidth
      setDisplayWidth(nextWidth)
    }

    const finishResize = () => {
      draggingRef.current = false
      document.body.classList.remove('workspace-dock-resizing')
      window.removeEventListener('pointermove', handlePointerMove)
      window.removeEventListener('pointerup', finishResize)
      window.removeEventListener('pointercancel', finishResize)
      setCollapseArmed(false)
      if (collapseArmedRef.current && onCollapse) {
        collapseArmedRef.current = false
        onCollapse(minWidth)
      } else {
        onWidthChange(widthRef.current)
      }
    }

    window.addEventListener('pointermove', handlePointerMove)
    window.addEventListener('pointerup', finishResize)
    window.addEventListener('pointercancel', finishResize)
  }, [maxWidth, minWidth, onCollapse, onWidthChange, side])

  const resizeFromKeyboard = React.useCallback((event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return
    event.preventDefault()
    const movement = event.key === 'ArrowRight' ? 16 : -16
    const widthDelta = side === 'left' ? movement : -movement
    if (side === 'left' && onCollapse && widthRef.current <= minWidth && widthDelta < 0) {
      onCollapse(minWidth)
      return
    }
    const nextWidth = Math.min(
      maxWidth,
      Math.max(minWidth, widthRef.current + widthDelta),
    )
    widthRef.current = nextWidth
    setDisplayWidth(nextWidth)
    onWidthChange(nextWidth)
  }, [maxWidth, minWidth, onCollapse, onWidthChange, side])

  const style = {
    '--workspace-dock-width': `${displayWidth}px`,
    '--workspace-dock-min-width': `${minWidth}px`,
    '--workspace-dock-max-width': `${maxWidth}px`,
  } as React.CSSProperties

  return (
    <aside
      className={`workspace-dock workspace-dock--${side} ${collapseArmed ? 'is-collapse-armed' : ''} ${className}`.trim()}
      style={style}
      aria-label={ariaLabel}
    >
      <div className="workspace-dock-content">{children}</div>
      <div
        className={`workspace-dock-resize workspace-dock-resize--${side}`}
        role="separator"
        tabIndex={0}
        aria-orientation="vertical"
        aria-label={`调整${ariaLabel}宽度`}
        aria-valuemin={minWidth}
        aria-valuemax={maxWidth}
        aria-valuenow={Math.round(displayWidth)}
        onPointerDown={beginResize}
        onKeyDown={resizeFromKeyboard}
      >
        <span className="workspace-dock-resize-grip" />
      </div>
    </aside>
  )
}
