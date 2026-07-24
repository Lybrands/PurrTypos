import React from 'react'
import { Popover as BasePopover } from '@base-ui/react/popover'
import './ui.scss'

type PopoverPlacement = 'top' | 'topLeft' | 'topRight' | 'bottom' | 'bottomLeft' | 'bottomRight' | 'left' | 'leftTop' | 'leftBottom' | 'right' | 'rightTop' | 'rightBottom'

const placementMap: Record<PopoverPlacement, { side: 'top' | 'bottom' | 'left' | 'right'; align: 'start' | 'center' | 'end' }> = {
  top: { side: 'top', align: 'center' }, topLeft: { side: 'top', align: 'start' }, topRight: { side: 'top', align: 'end' },
  bottom: { side: 'bottom', align: 'center' }, bottomLeft: { side: 'bottom', align: 'start' }, bottomRight: { side: 'bottom', align: 'end' },
  left: { side: 'left', align: 'center' }, leftTop: { side: 'left', align: 'start' }, leftBottom: { side: 'left', align: 'end' },
  right: { side: 'right', align: 'center' }, rightTop: { side: 'right', align: 'start' }, rightBottom: { side: 'right', align: 'end' },
}

export interface PopoverProps {
  content: React.ReactNode
  children: React.ReactElement
  title?: React.ReactNode
  open?: boolean
  onOpenChange?: (open: boolean) => void
  placement?: PopoverPlacement
  trigger?: 'click' | 'hover' | 'focus' | Array<'click' | 'hover' | 'focus'>
  arrow?: boolean | { pointAtCenter?: boolean }
  overlayClassName?: string
  rootClassName?: string
  classNames?: { root?: string; body?: string }
  styles?: {
    root?: React.CSSProperties
    container?: React.CSSProperties
    content?: React.CSSProperties
    body?: React.CSSProperties
  }
  align?: { offset?: [number, number] }
  mouseEnterDelay?: number
  mouseLeaveDelay?: number
  destroyOnHidden?: boolean
  disabled?: boolean
  /** 触发节点最终渲染为原生 button 时保持 true；仅 span 等节点设为 false。 */
  nativeButton?: boolean
}

/** 项目内点击/悬停浮层入口，支持受控开合与定位。 */
export function Popover({
  content,
  children,
  title,
  open,
  onOpenChange,
  placement = 'top',
  trigger = 'click',
  arrow = true,
  overlayClassName,
  rootClassName,
  classNames,
  styles,
  align,
  mouseEnterDelay,
  mouseLeaveDelay,
  disabled,
  nativeButton = true,
}: PopoverProps) {
  const position = placementMap[placement]
  const triggers = Array.isArray(trigger) ? trigger : [trigger]
  const openOnHover = triggers.includes('hover') || triggers.includes('focus')
  return (
    <BasePopover.Root open={open} onOpenChange={(nextOpen) => onOpenChange?.(nextOpen)}>
      <BasePopover.Trigger
        render={children}
        nativeButton={nativeButton}
        disabled={disabled}
        openOnHover={openOnHover}
        delay={mouseEnterDelay == null ? undefined : mouseEnterDelay * 1000}
        closeDelay={mouseLeaveDelay == null ? undefined : mouseLeaveDelay * 1000}
      />
      <BasePopover.Portal>
        <BasePopover.Positioner
          className="purr-popover__positioner"
          side={position.side}
          align={position.align}
          sideOffset={8 + (align?.offset?.[1] ?? 0)}
          alignOffset={align?.offset?.[0]}
        >
          <BasePopover.Popup
            className={['purr-popover', overlayClassName, rootClassName, classNames?.root].filter(Boolean).join(' ')}
            style={styles?.root}
          >
            {arrow && <BasePopover.Arrow className="purr-popover__arrow" />}
            <div className="purr-popover__inner" style={styles?.container}>
              {title != null && <div className="purr-popover__title">{title}</div>}
              <div
                className={['purr-popover__content', classNames?.body].filter(Boolean).join(' ')}
                style={{ ...styles?.content, ...styles?.body }}
              >
                {content}
              </div>
            </div>
          </BasePopover.Popup>
        </BasePopover.Positioner>
      </BasePopover.Portal>
    </BasePopover.Root>
  )
}
