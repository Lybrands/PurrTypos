import React from 'react'
import '../styles/purr.scss'

export interface PurrDividerProps {
  style?: React.CSSProperties
  className?: string
}

export function PurrDivider({ style, className }: PurrDividerProps) {
  return <div role="separator" className={['purr-divider', className].filter(Boolean).join(' ')} style={style} />
}
