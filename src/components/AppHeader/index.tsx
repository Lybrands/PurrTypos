import React from 'react'
import { SunOutlined, MoonOutlined, FontSizeOutlined, SettingOutlined } from '@ant-design/icons'
import { Button, Tooltip } from 'antd'
import { useTheme } from '../../contexts/ThemeContext'
import { useFontSize } from '../../contexts/FontSizeContext'
import PanelLeftIcon from '../../icons/PanelLeftIcon'
import PanelCenterIcon from '../../icons/PanelCenterIcon'
import PanelRightIcon from '../../icons/PanelRightIcon'
import homeLogoLight from '../../imgs/home_logo_light.png'
import homeLogoDark from '../../imgs/home_logo_dark.png'
import './index.scss'

const FONT_SIZE_LABELS: Record<string, string> = { small: '小', medium: '中', large: '大' }

interface AppHeaderProps {
  title?: React.ReactNode
  left?: React.ReactNode
  /** 工作台搜索栏等，插在右侧操作区最前 */
  searchSlot?: React.ReactNode
  right?: React.ReactNode
  showActions?: boolean
  /** 工作台专属：左侧面板折叠状态 */
  leftCollapsed?: boolean
  /** 工作台专属：右侧面板折叠状态 */
  rightCollapsed?: boolean
  /** 工作台专属：中间写作区域折叠状态 */
  editorCollapsed?: boolean
  /** 工作台专属：切换左侧面板 */
  onToggleLeft?: () => void
  /** 工作台专属：切换右侧面板 */
  onToggleRight?: () => void
  /** 工作台专属：切换中间写作区域 */
  onToggleEditor?: () => void
  /** 打开设置页 */
  onOpenSettings?: () => void
}

export default function AppHeader({
  title,
  left,
  searchSlot,
  right,
  showActions = false,
  leftCollapsed,
  rightCollapsed,
  editorCollapsed,
  onToggleLeft,
  onToggleRight,
  onToggleEditor,
  onOpenSettings,
}: AppHeaderProps) {
  const { theme, toggleTheme } = useTheme()
  const { fontSize, cycleFontSize } = useFontSize()

  const showPanelToggles = showActions && (onToggleLeft != null || onToggleRight != null || onToggleEditor != null)

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
        {showPanelToggles && onToggleLeft && (
          <Tooltip title={leftCollapsed ? '展开左侧面板' : '收起左侧面板'}>
            <Button
              type="text"
              size="small"
              icon={<PanelLeftIcon size={16} />}
              onClick={onToggleLeft}
              className={`app-header-action-btn${leftCollapsed ? ' panel-toggle-inactive' : ''}`}
            />
          </Tooltip>
        )}
        {showPanelToggles && onToggleEditor && (
          <Tooltip title={editorCollapsed ? '展开写作区域' : '收起写作区域'}>
            <Button
              type="text"
              size="small"
              icon={<PanelCenterIcon size={16} />}
              onClick={onToggleEditor}
              className={`app-header-action-btn${editorCollapsed ? ' panel-toggle-inactive' : ''}`}
            />
          </Tooltip>
        )}
        {showPanelToggles && onToggleRight && (
          <Tooltip title={rightCollapsed ? '展开右侧面板' : '收起右侧面板'}>
            <Button
              type="text"
              size="small"
              icon={<PanelRightIcon size={16} />}
              onClick={onToggleRight}
              className={`app-header-action-btn${rightCollapsed ? ' panel-toggle-inactive' : ''}`}
            />
          </Tooltip>
        )}

        {showActions && (
          <>
            <Button
              type="text"
              size="small"
              icon={theme === 'light'
                ? <MoonOutlined style={{ fontSize: 16 }} />
                : <SunOutlined style={{ fontSize: 16 }} />}
              title={theme === 'light' ? '切换到深色' : '切换到浅色'}
              onClick={toggleTheme}
              className="app-header-action-btn"
            />
            <Tooltip title={`字体大小：${FONT_SIZE_LABELS[fontSize]}（点击切换）`}>
              <Button
                type="text"
                size="small"
                icon={<FontSizeOutlined style={{ fontSize: 16 }} />}
                onClick={cycleFontSize}
                className="app-header-action-btn"
              />
            </Tooltip>
          </>
        )}

        {onOpenSettings && (
          <Tooltip title="设置">
            <Button
              type="text"
              size="small"
              icon={<SettingOutlined style={{ fontSize: 16 }} />}
              onClick={onOpenSettings}
              className="app-header-action-btn"
            />
          </Tooltip>
        )}
      </div>
    </header>
  )
}
