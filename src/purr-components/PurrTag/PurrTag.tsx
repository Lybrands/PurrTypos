import React from 'react'
import '../styles/purr.scss'

export interface PurrTagProps extends Omit<React.HTMLAttributes<HTMLSpanElement>, 'color'> {
  color?: string
  variant?: string
}

export function PurrTag({
  children,
  color = 'default',
  className,
  ...props
}: PurrTagProps) {
  return (
    <span
      {...props}
      className={['purr-tag', `purr-tag--${color}`, className].filter(Boolean).join(' ')}
    >
      {children}
    </span>
  )
}
