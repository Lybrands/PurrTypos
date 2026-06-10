import React from 'react'

interface ChapterListIconProps {
  size?: number
  className?: string
  style?: React.CSSProperties
}

/** 章节列表图标：项目符号列表（圆点 + 行） */
export default function ChapterListIcon({ size = 16, className, style }: ChapterListIconProps) {
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
      <circle cx="3" cy="4" r="1.1" fill="currentColor" />
      <circle cx="3" cy="8" r="1.1" fill="currentColor" />
      <circle cx="3" cy="12" r="1.1" fill="currentColor" />
      <line x1="6.2" y1="4" x2="14" y2="4" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
      <line x1="6.2" y1="8" x2="14" y2="8" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
      <line x1="6.2" y1="12" x2="11.5" y2="12" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
    </svg>
  )
}
