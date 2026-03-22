import React from 'react'

interface PanelRightIconProps {
  size?: number
  className?: string
  style?: React.CSSProperties
}

/** 右侧面板切换图标：外框 + 右侧色块 */
export default function PanelRightIcon({ size = 16, className, style }: PanelRightIconProps) {
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
      {/* 整体外框 */}
      <rect x="1" y="2" width="14" height="12" rx="1.5" stroke="currentColor" strokeWidth="1.2" />
      {/* 右侧面板色块 */}
      <rect x="9.5" y="3" width="4.5" height="10" rx="0.5" fill="currentColor" />
      {/* 分隔线 */}
      <line x1="8.5" y1="3" x2="8.5" y2="13" stroke="currentColor" strokeWidth="0.8" strokeOpacity="0.35" />
    </svg>
  )
}
