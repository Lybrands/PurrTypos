import React from 'react'
import { SunOutlined, MoonOutlined } from '../../ui'
import { Button } from '../../ui'
import { useTheme } from '../../contexts/ThemeContext'
import './index.scss'

export default function GlobalActions() {
  const { theme, toggleTheme } = useTheme()

  return (
    <div className="global-actions">
      <Button
        type="text"
        size="small"
        icon={theme === 'light' ? <MoonOutlined style={{ fontSize: 16 }} /> : <SunOutlined style={{ fontSize: 16 }} />}
        title={theme === 'light' ? '切换到深色' : '切换到浅色'}
        onClick={toggleTheme}
      />
    </div>
  )
}
