import React from 'react'

export interface PurrIconProps extends React.HTMLAttributes<HTMLSpanElement> {
  children?: React.ReactNode
  spin?: boolean
  title?: string
  size?: number | string
}

/**
 * Purr Softline 图标统一画布。
 *
 * 所有图标使用 24 × 24 光学网格、1.75 描边和圆角线帽。
 * 业务层通过 font-size 控制尺寸，不接触 SVG 内部结构。
 */
export const PurrIcon = React.forwardRef<HTMLSpanElement, PurrIconProps>(
  ({ children, spin, title, size, className, style, ...props }, ref) => {
    const labelled = Boolean(title || props['aria-label'])
    return (
      <span
        {...props}
        ref={ref}
        className={['purr-icon', spin && 'purr-icon--spin', className].filter(Boolean).join(' ')}
        style={{ ...style, fontSize: size ?? style?.fontSize }}
      >
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.75"
          strokeLinecap="round"
          strokeLinejoin="round"
          shapeRendering="geometricPrecision"
          focusable="false"
          role={labelled ? 'img' : undefined}
          aria-hidden={labelled ? undefined : true}
        >
          {title && <title>{title}</title>}
          {children}
        </svg>
      </span>
    )
  },
)

PurrIcon.displayName = 'PurrIcon'

export function createPurrIcon(displayName: string, glyph: React.ReactNode) {
  const Component = React.forwardRef<HTMLSpanElement, PurrIconProps>(
    (props, ref) => <PurrIcon {...props} ref={ref} data-icon={displayName}>{glyph}</PurrIcon>,
  )
  Component.displayName = displayName
  return Component
}
