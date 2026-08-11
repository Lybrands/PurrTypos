import React from 'react'
import { Tooltip as BaseTooltip } from '@base-ui/react/tooltip'
import { getOverlayLayerStyle } from '../overlayLayer'
import '../styles/purr.scss'

type TooltipPlacement = 'top' | 'topLeft' | 'topRight' | 'bottom' | 'bottomLeft' | 'bottomRight' | 'left' | 'leftTop' | 'leftBottom' | 'right' | 'rightTop' | 'rightBottom'

const placementMap: Record<TooltipPlacement, { side: 'top' | 'bottom' | 'left' | 'right'; align: 'start' | 'center' | 'end' }> = {
  top: { side: 'top', align: 'center' }, topLeft: { side: 'top', align: 'start' }, topRight: { side: 'top', align: 'end' },
  bottom: { side: 'bottom', align: 'center' }, bottomLeft: { side: 'bottom', align: 'start' }, bottomRight: { side: 'bottom', align: 'end' },
  left: { side: 'left', align: 'center' }, leftTop: { side: 'left', align: 'start' }, leftBottom: { side: 'left', align: 'end' },
  right: { side: 'right', align: 'center' }, rightTop: { side: 'right', align: 'start' }, rightBottom: { side: 'right', align: 'end' },
}

export interface PurrTooltipProps {
  title: React.ReactNode
  children: React.ReactElement
  placement?: TooltipPlacement
  mouseEnterDelay?: number
  disabled?: boolean
  className?: string
  open?: boolean
  onOpenChange?: (open: boolean) => void
  getPopupContainer?: () => HTMLElement
  zIndex?: number
  styles?: {
    root?: React.CSSProperties
    container?: React.CSSProperties
  }
}

/** 项目内提示层入口，提供受控开合、定位与 Portal 能力。 */
export function PurrTooltip({ title, children, placement = 'top', mouseEnterDelay, disabled, className, open, onOpenChange, getPopupContainer, zIndex, styles }: PurrTooltipProps) {
  const position = placementMap[placement]
  if (!title) return children
  return (
    <BaseTooltip.Root disabled={disabled} open={open} onOpenChange={(nextOpen) => onOpenChange?.(nextOpen)}>
      <BaseTooltip.Trigger render={children} delay={mouseEnterDelay == null ? undefined : mouseEnterDelay * 1000} />
      <BaseTooltip.Portal container={getPopupContainer?.()}>
        <BaseTooltip.Positioner
          className="purr-tooltip__positioner"
          style={getOverlayLayerStyle('PurrTooltip', zIndex)}
          side={position.side}
          align={position.align}
          sideOffset={8}
        >
          <BaseTooltip.Popup
            className={['purr-tooltip', className].filter(Boolean).join(' ')}
            style={{ ...styles?.root, ...styles?.container }}
          >
            <BaseTooltip.Arrow className="purr-tooltip__arrow" />
            {title}
          </BaseTooltip.Popup>
        </BaseTooltip.Positioner>
      </BaseTooltip.Portal>
    </BaseTooltip.Root>
  )
}

export function PurrTooltipProvider({ children }: { children: React.ReactNode }) {
  return <BaseTooltip.Provider delay={500}>{children}</BaseTooltip.Provider>
}
