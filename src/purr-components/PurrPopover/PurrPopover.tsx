import React from 'react'
import { Popover as BasePopover } from '@base-ui/react/popover'
import { getOverlayLayerStyle } from '../overlayLayer'
import '../styles/purr.scss'

type PopoverPlacement = 'top' | 'topLeft' | 'topRight' | 'bottom' | 'bottomLeft' | 'bottomRight' | 'left' | 'leftTop' | 'leftBottom' | 'right' | 'rightTop' | 'rightBottom'

const placementMap: Record<PopoverPlacement, { side: 'top' | 'bottom' | 'left' | 'right'; align: 'start' | 'center' | 'end' }> = {
  top: { side: 'top', align: 'center' }, topLeft: { side: 'top', align: 'start' }, topRight: { side: 'top', align: 'end' },
  bottom: { side: 'bottom', align: 'center' }, bottomLeft: { side: 'bottom', align: 'start' }, bottomRight: { side: 'bottom', align: 'end' },
  left: { side: 'left', align: 'center' }, leftTop: { side: 'left', align: 'start' }, leftBottom: { side: 'left', align: 'end' },
  right: { side: 'right', align: 'center' }, rightTop: { side: 'right', align: 'start' }, rightBottom: { side: 'right', align: 'end' },
}

export interface PurrPopoverProps {
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
  /**
   * 内容自适应限高：实际高度取 min(maxHeight, 锚点所在侧的视口剩余高度)，
   * 超出部分内部滚动。剩余高度来自 Base UI 定位层的 `--available-height`
   * （已含翻转后的最终朝向），浮层在视口边缘不会被裁切。
   */
  maxHeight?: number
  zIndex?: number
}

/** 项目内点击/悬停浮层入口，支持受控开合与定位。 */
export function PurrPopover({
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
  maxHeight,
  zIndex,
}: PurrPopoverProps) {
  const position = placementMap[placement]
  const triggers = Array.isArray(trigger) ? trigger : [trigger]
  const openOnHover = triggers.includes('hover') || triggers.includes('focus')
  // 48px = 内边距 28 + 箭头 10 + 安全余量；--available-height 缺省时回退到 maxHeight 本身
  const fitMaxHeight = maxHeight != null
    ? `min(${maxHeight}px, calc(var(--available-height, ${maxHeight + 48}px) - 48px))`
    : undefined
  return (
    <BasePopover.Root
      open={open}
      onOpenChange={(nextOpen, eventDetails) => {
        /*
         * Base UI 的 PurrPopover.Trigger 即使启用 openOnHover，也会默认响应点击。
         * 对纯 hover / focus 浮层取消 trigger-press，避免点击触发器后把预览
         * 固定在打开状态；只有显式声明 click 的 PurrPopover 才允许点击切换。
         */
        if (!triggers.includes('click') && eventDetails.reason === 'trigger-press') {
          eventDetails.cancel()
          return
        }
        onOpenChange?.(nextOpen)
      }}
    >
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
          style={getOverlayLayerStyle('PurrPopover', zIndex)}
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
                style={{
                  ...styles?.content,
                  ...styles?.body,
                  ...(fitMaxHeight != null ? { maxHeight: fitMaxHeight, overflowY: 'auto' } : null),
                }}
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
