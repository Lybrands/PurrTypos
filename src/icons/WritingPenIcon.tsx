import React from 'react'

interface WritingPenIconProps {
  size?: number
  className?: string
  style?: React.CSSProperties
}

/** 写作 / 正文编辑器图标：钢笔 + 书写基线 */
export default function WritingPenIcon({ size = 16, className, style }: WritingPenIconProps) {
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
      {/* 笔身（斜置） */}
      <path
        d="M10.3 2.6 L13 5.3 L6.1 12.2 L2.7 13 L3.5 9.6 Z"
        stroke="currentColor"
        strokeWidth="1.2"
        strokeLinejoin="round"
      />
      {/* 笔尖分割线 */}
      <line
        x1="9.1"
        y1="3.9"
        x2="11.8"
        y2="6.6"
        stroke="currentColor"
        strokeWidth="0.9"
        strokeOpacity="0.45"
      />
      {/* 书写基线 */}
      <line
        x1="9"
        y1="13.6"
        x2="14"
        y2="13.6"
        stroke="currentColor"
        strokeWidth="1.2"
        strokeLinecap="round"
      />
    </svg>
  )
}
