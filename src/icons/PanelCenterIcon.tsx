import React from 'react'

interface PanelCenterIconProps {
  size?: number
  className?: string
  style?: React.CSSProperties
}

/** 中间写作区域面板切换图标：外框 + 中间色块 */
export default function PanelCenterIcon({ size = 16, className, style }: PanelCenterIconProps) {
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
      {/* 中间面板色块 */}
      <rect x="6" y="3" width="4" height="10" rx="0.5" fill="currentColor" />
      {/* 左侧分隔线 */}
      <line x1="5.5" y1="3" x2="5.5" y2="13" stroke="currentColor" strokeWidth="0.8" strokeOpacity="0.35" />
      {/* 右侧分隔线 */}
      <line x1="10.5" y1="3" x2="10.5" y2="13" stroke="currentColor" strokeWidth="0.8" strokeOpacity="0.35" />
    </svg>
  )
}
