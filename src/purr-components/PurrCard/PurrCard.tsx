import React from 'react'
import '../styles/purr.scss'

export interface PurrCardProps {
  children?: React.ReactNode
  className?: string
  size?: 'default' | 'small'
}

export function PurrCard({ children, className, size = 'default' }: PurrCardProps) {
  return (
    <div className={['purr-card', `purr-card--${size}`, className].filter(Boolean).join(' ')}>
      <div className="purr-card__body">{children}</div>
    </div>
  )
}
