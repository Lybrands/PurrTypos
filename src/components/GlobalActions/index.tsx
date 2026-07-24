import React from 'react'
import { SunOutlined, MoonOutlined, FontSizeOutlined } from '../../ui'
import { Button, Tooltip } from '../../ui'
import { useTheme } from '../../contexts/ThemeContext'
import { useFontSize } from '../../contexts/FontSizeContext'
import './index.scss'

const FONT_SIZE_LABELS: Record<string, string> = { small: '小', medium: '中', large: '大' }

export default function GlobalActions() {
  const { theme, toggleTheme } = useTheme()
  const { fontSize, cycleFontSize } = useFontSize()

  return (
    <div className="global-actions">
      <Button
        type="text"
        size="small"
        icon={theme === 'light' ? <MoonOutlined style={{ fontSize: 16 }} /> : <SunOutlined style={{ fontSize: 16 }} />}
        title={theme === 'light' ? '切换到深色' : '切换到浅色'}
        onClick={toggleTheme}
      />
      <Tooltip title={`字体大小：${FONT_SIZE_LABELS[fontSize]}（点击切换）`}>
        <Button
          type="text"
          size="small"
          icon={<FontSizeOutlined style={{ fontSize: 16 }} />}
          onClick={cycleFontSize}
        />
      </Tooltip>
    </div>
  )
}
