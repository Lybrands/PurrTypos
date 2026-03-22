import React from 'react'

interface StopCircleIconProps {
  size?: number
  className?: string
  style?: React.CSSProperties
}

export default function StopCircleIcon({ size = 16, className, style }: StopCircleIconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 16 16"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      style={{ display: 'block', ...style }}
    >
      {/* 外圆 */}
      <circle cx="8" cy="8" r="7.5" fill="currentColor" />
      {/* 内方块 */}
      <rect x="5" y="5" width="6" height="6" rx="1" fill="var(--bg-panel, #1e1e2e)" />
    </svg>
  )
}
