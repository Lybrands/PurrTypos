import React from 'react'
import { SunIcon, MoonIcon, SettingsIcon } from '@/purr-components'
import { PurrButton, PurrTooltip } from '@/purr-components'
import { useTheme } from '../../contexts/ThemeContext'
import homeLogoLight from '../../imgs/home_logo_light.png'
import homeLogoDark from '../../imgs/home_logo_dark.png'
import './index.scss'

/** 顶栏面板 toggle 按钮描述（工作台用来唤起/关闭浮窗） */
export interface HeaderPanelToggle {
  key: string
  icon: React.ReactNode
  tooltip: string
  /** 对应面板当前可见（浮窗已开 / 已是主区域） */
  active?: boolean
  /** 该面板已是主区域等场景：按钮仅作状态展示，点击无效 */
  disabled?: boolean
  onClick?: () => void
}

interface AppHeaderProps {
  title?: React.ReactNode
  left?: React.ReactNode
  /** 工作台搜索栏等，插在右侧操作区最前 */
  searchSlot?: React.ReactNode
  right?: React.ReactNode
  showActions?: boolean
  /** 工作台专属：面板可见性 toggle 按钮组 */
  panelToggles?: HeaderPanelToggle[]
  /** 打开设置页 */
  onOpenSettings?: () => void
}

export default function AppHeader({
  title,
  left,
  searchSlot,
  right,
  showActions = false,
  panelToggles,
  onOpenSettings,
}: AppHeaderProps) {
  const { theme, toggleTheme } = useTheme()

  const headerLogo = theme === 'dark' ? homeLogoDark : homeLogoLight

  return (
    <header className="app-header">
      <div className="app-header-left">
        {left}
        {title != null && (
          typeof title === 'string'
            ? <span className="app-title">{title}</span>
            : title
        )}
      </div>
      <div className="app-header-center">
        <img src={headerLogo} alt="" className="app-header-logo" />
      </div>
      <div className="app-header-right">
        {searchSlot}
        {right}

        {/* 面板切换（仅工作台） */}
        {panelToggles && panelToggles.length > 0 && (
          <div className="app-header-panel-toggles">
            {panelToggles.map((t) => (
              <PurrTooltip key={t.key} title={t.tooltip}>
                <PurrButton
                  type="text"
                  size="small"
                  icon={t.icon}
                  onClick={t.disabled ? undefined : t.onClick}
                  className={`app-header-action-btn panel-toggle-btn${t.active ? ' panel-toggle-active' : ''}${t.disabled ? ' panel-toggle-static' : ''}`}
                  aria-pressed={!!t.active}
                />
              </PurrTooltip>
            ))}
          </div>
        )}

        {showActions && (
          <PurrTooltip title={theme === 'light' ? '切换到深色' : '切换到浅色'}>
            <PurrButton
              type="text"
              size="small"
              icon={theme === 'light'
                ? <MoonIcon style={{ fontSize: 16 }} />
                : <SunIcon style={{ fontSize: 16 }} />}
              onClick={toggleTheme}
              className="app-header-action-btn"
              aria-label={theme === 'light' ? '切换到深色' : '切换到浅色'}
            />
          </PurrTooltip>
        )}

        {onOpenSettings && (
          <PurrTooltip title="设置">
            <PurrButton
              type="text"
              size="small"
              icon={<SettingsIcon style={{ fontSize: 16 }} />}
              onClick={onOpenSettings}
              className="app-header-action-btn"
              aria-label="打开设置"
            />
          </PurrTooltip>
        )}
      </div>
    </header>
  )
}
