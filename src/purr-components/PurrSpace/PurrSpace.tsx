import React from 'react'
import '../styles/purr.scss'

export interface PurrSpaceProps {
  children?: React.ReactNode
  size?: 'small' | 'middle' | 'large' | number
  wrap?: boolean
  className?: string
  style?: React.CSSProperties
}

export function PurrSpace({
  children,
  size = 'small',
  wrap,
  className,
  style,
}: PurrSpaceProps) {
  const gap = typeof size === 'number' ? size : size === 'large' ? 16 : size === 'middle' ? 12 : 8
  return (
    <div
      className={['purr-space', wrap && 'purr-space--wrap', className].filter(Boolean).join(' ')}
      style={{ gap, ...style }}
    >
      {React.Children.map(children, (child) => (
        child == null ? null : <div className="purr-space__item">{child}</div>
      ))}
    </div>
  )
}
