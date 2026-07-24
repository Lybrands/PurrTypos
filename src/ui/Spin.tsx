import React from 'react'
import './ui.scss'

export interface SpinProps {
  size?: 'small' | 'default' | 'large'
  className?: string
  children?: React.ReactNode
  spinning?: boolean
  tip?: React.ReactNode
}

export function Spin({ size = 'default', className, children, spinning = true, tip }: SpinProps) {
  if (children) {
    return (
      <div className={['purr-spin-container', className].filter(Boolean).join(' ')} aria-busy={spinning}>
        {children}
        {spinning && (
          <span className="purr-spin-overlay">
            <span className={`purr-spin purr-spin--${size}`} aria-label="加载中" />
            {tip && <span className="purr-spin__tip">{tip}</span>}
          </span>
        )}
      </div>
    )
  }
  return spinning ? (
    <span className={['purr-spin-standalone', className].filter(Boolean).join(' ')}>
      <span className={['purr-spin', `purr-spin--${size}`].join(' ')} aria-label="加载中" />
      {tip && <span className="purr-spin__tip">{tip}</span>}
    </span>
  ) : null
}
