import React from 'react'
import '../styles/purr.scss'

export interface PurrProgressProps {
  percent?: number
  status?: 'success' | 'exception' | 'normal' | 'active'
  size?: 'small' | 'default' | number | [number, number]
  showInfo?: boolean
  strokeColor?: string
  format?: (percent?: number) => React.ReactNode
}

export function PurrProgress({
  percent = 0,
  status,
  size,
  showInfo = true,
  strokeColor,
  format,
}: PurrProgressProps) {
  const height = Array.isArray(size) ? size[1] : typeof size === 'number' ? size : size === 'small' ? 6 : 8
  return (
    <div className={['purr-progress', status && `purr-progress--${status}`].filter(Boolean).join(' ')}>
      <div className="purr-progress__track" style={{ height }}>
        <span
          className="purr-progress__bar"
          style={{ width: `${Math.max(0, Math.min(100, percent))}%`, background: strokeColor }}
        />
      </div>
      {showInfo && <span className="purr-progress__text">{format ? format(percent) : `${Math.round(percent)}%`}</span>}
    </div>
  )
}
