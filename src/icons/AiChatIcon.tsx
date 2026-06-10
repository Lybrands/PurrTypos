import React from 'react'

interface AiChatIconProps {
  size?: number
  className?: string
  style?: React.CSSProperties
}

/** AI 对话图标：对话气泡 + 四角星（AI 语义） */
export default function AiChatIcon({ size = 16, className, style }: AiChatIconProps) {
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
      {/* 气泡（左下带尾巴） */}
      <path
        d="M3 2.6 H13 A1.6 1.6 0 0 1 14.6 4.2 V9.8 A1.6 1.6 0 0 1 13 11.4 H7.4 L4.2 14 V11.4 H3 A1.6 1.6 0 0 1 1.4 9.8 V4.2 A1.6 1.6 0 0 1 3 2.6 Z"
        stroke="currentColor"
        strokeWidth="1.2"
        strokeLinejoin="round"
      />
      {/* 四角星：AI 的「灵光」 */}
      <path
        d="M8 4.2 L8.9 6.1 L10.8 7 L8.9 7.9 L8 9.8 L7.1 7.9 L5.2 7 L7.1 6.1 Z"
        fill="currentColor"
      />
    </svg>
  )
}
