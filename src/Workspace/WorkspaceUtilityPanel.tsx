import React, { Suspense, lazy } from 'react'
import {
  BookOutlined,
  BulbOutlined,
  DashboardOutlined,
  FileTextOutlined,
  FullscreenExitOutlined,
  FullscreenOutlined,
  HighlightOutlined,
  PanelToggleIcon,
  TeamOutlined,
} from '../ui'
import { Button, Spin, Tabs, Tooltip } from '../ui'
import type { EntityId } from '../types'
import type { OpenSettingPanelDetail } from './SettingPanel'
import {
  EDITOR_TAB_KEY,
  type WorkspaceUtilityTab,
  type WorkspaceUtilityTabKind,
} from './utilityPanelTypes'
import './WorkspaceUtilityPanel.scss'

const ChapterOutlinePanel = lazy(() => import('./DirectorNotebook/ChapterOutlineModal'))
const MemoryCenter = lazy(() => import('./AiPanel/components/MemoryCenter'))
const StyleForm = lazy(() => import('./DirectorNotebook/StyleForm'))
const SettingPanel = lazy(() => import('./SettingPanel'))
const DashboardPanel = lazy(() => import('./DashboardPanel'))

const TAB_ICONS: Record<WorkspaceUtilityTabKind, React.ReactNode> = {
  outline: <BookOutlined />,
  memory: <BulbOutlined />,
  style: <HighlightOutlined />,
  setting: <TeamOutlined />,
  dashboard: <DashboardOutlined />,
}

interface WorkspaceUtilityPanelProps {
  bookId: EntityId | null
  tabs: WorkspaceUtilityTab[]
  activeKey: string | null
  onActiveKeyChange: (key: string) => void
  onCloseTab: (key: string) => void
  editorContent: React.ReactNode
  onCollapse?: () => void
  dockCollapsed?: boolean
  onExpandDock?: () => void
  fullscreen?: boolean
  onToggleFullscreen?: () => void
  settingOpenRequest?: OpenSettingPanelDetail | null
}

const PanelFallback = () => (
  <div className="workspace-utility-loading"><Spin size="small" /></div>
)

export default function WorkspaceUtilityPanel({
  bookId,
  tabs,
  activeKey,
  onActiveKeyChange,
  onCloseTab,
  editorContent,
  onCollapse,
  dockCollapsed = false,
  onExpandDock,
  fullscreen = false,
  onToggleFullscreen,
  settingOpenRequest,
}: WorkspaceUtilityPanelProps) {
  const [fullscreenTooltipOpen, setFullscreenTooltipOpen] = React.useState(false)

  React.useEffect(() => {
    setFullscreenTooltipOpen(false)
  }, [fullscreen])

  const handleOutlineChanged = React.useCallback(() => {
    window.dispatchEvent(new CustomEvent('chapter-outline-changed'))
  }, [])

  const items = [{
    key: EDITOR_TAB_KEY,
    label: (
      <span className="workspace-utility-tab-label">
        <FileTextOutlined />
        <span>正文</span>
      </span>
    ),
    children: editorContent,
    closable: false,
  }, ...tabs.map((tab) => ({
    key: tab.key,
    label: (
      <span className="workspace-utility-tab-label">
        {TAB_ICONS[tab.kind]}
        <span>{tab.title}</span>
      </span>
    ),
    children: (
      <Suspense fallback={<PanelFallback />}>
        {tab.kind === 'outline' && tab.outlineTarget ? (
          <ChapterOutlinePanel
            target={tab.outlineTarget}
            bookId={bookId}
            onChanged={handleOutlineChanged}
          />
        ) : null}
        {tab.kind === 'memory' ? (
          <div className="workspace-utility-content workspace-utility-content--memory">
            <MemoryCenter bookId={bookId} />
          </div>
        ) : null}
        {tab.kind === 'style' ? (
          <div className="workspace-utility-content workspace-utility-content--style">
            <StyleForm />
          </div>
        ) : null}
        {tab.kind === 'setting' ? (
          <div className="workspace-utility-content workspace-utility-content--setting">
            <SettingPanel bookId={bookId} openRequest={settingOpenRequest} />
          </div>
        ) : null}
        {tab.kind === 'dashboard' ? (
          <div className="workspace-utility-content workspace-utility-content--dashboard">
            <DashboardPanel bookId={bookId} />
          </div>
        ) : null}
      </Suspense>
    ),
    closable: true,
  }))]

  return (
    <div className="workspace-utility-panel">
      <Tabs
        activeKey={activeKey ?? undefined}
        onChange={onActiveKeyChange}
        onEdit={(targetKey) => onCloseTab(targetKey)}
        destroyOnHidden={false}
        items={items}
        className="workspace-utility-tabs"
        tabBarExtraContent={(
          <div className="workspace-utility-actions">
            {!dockCollapsed && onToggleFullscreen ? (
              <Tooltip
                title={fullscreen ? '退出全屏' : '全屏'}
                open={fullscreenTooltipOpen}
                onOpenChange={setFullscreenTooltipOpen}
              >
                <Button
                  type="text"
                  size="small"
                  icon={fullscreen ? <FullscreenExitOutlined /> : <FullscreenOutlined />}
                  onClick={(event) => {
                    setFullscreenTooltipOpen(false)
                    event.currentTarget.blur()
                    onToggleFullscreen()
                  }}
                  className="workspace-utility-action-btn"
                  aria-label={fullscreen ? '退出工作面板全屏' : '全屏显示工作面板'}
                />
              </Tooltip>
            ) : null}
            {dockCollapsed && onExpandDock ? (
              <Tooltip title="固定展开工作面板">
                <Button
                  type="text"
                  size="small"
                  icon={<PanelToggleIcon side="right" action="expand" />}
                  onClick={onExpandDock}
                  className="workspace-utility-action-btn"
                  aria-label="固定展开工作面板"
                />
              </Tooltip>
            ) : null}
            {!dockCollapsed && onCollapse ? (
              <Tooltip title="收起工作面板">
                <Button
                  type="text"
                  size="small"
                  icon={<PanelToggleIcon side="right" action="collapse" />}
                  onClick={onCollapse}
                  className="workspace-utility-action-btn"
                  aria-label="收起工作面板"
                />
              </Tooltip>
            ) : null}
          </div>
        )}
      />
    </div>
  )
}
