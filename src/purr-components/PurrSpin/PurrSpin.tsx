import React from 'react'
import '../styles/purr.scss'

export interface PurrSpinProps {
  size?: 'small' | 'default' | 'large'
  className?: string
  children?: React.ReactNode
  spinning?: boolean
  tip?: React.ReactNode
}

export function PurrSpin({ size = 'default', className, children, spinning = true, tip }: PurrSpinProps) {
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
