import React, { Suspense, lazy } from 'react'
import {
  DashboardIcon,
  FullscreenExitIcon,
  FullscreenIcon,
  HighlightIcon,
  ManuscriptIcon,
  MasterOutlineIcon,
  OutlineIcon,
  PanelToggleIcon,
  StoryMemoryIcon,
  StorySettingIcon,
} from '@/purr-components'
import { PurrButton, PurrSpin, PurrTabs, PurrTooltip } from '@/purr-components'
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
const WritingMethodBindingsPanel = lazy(() => import('./WritingMethodBindingsPanel'))
const KnowledgePanel = lazy(() => import('./KnowledgePanel'))
const ContinuationCanonPanel = lazy(() => import('./ContinuationCanonPanel'))
const SettingPanel = lazy(() => import('./SettingPanel'))
const DashboardPanel = lazy(() => import('./DashboardPanel'))

const TAB_ICONS: Record<WorkspaceUtilityTabKind, React.ReactNode> = {
  outline: <OutlineIcon />,
  memory: <StoryMemoryIcon />,
  writingMethods: <HighlightIcon />,
  canon: <StoryMemoryIcon />,
  knowledge: <StoryMemoryIcon />,
  setting: <StorySettingIcon />,
  dashboard: <DashboardIcon />,
}

const getUtilityTabIcon = (tab: WorkspaceUtilityTab) => (
  tab.kind === 'outline' && tab.outlineTarget?.mode === 'global'
    ? <MasterOutlineIcon />
    : TAB_ICONS[tab.kind]
)

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
  <div className="workspace-utility-loading"><PurrSpin size="small" /></div>
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
        <ManuscriptIcon />
        <span>正文</span>
      </span>
    ),
    children: editorContent,
    closable: false,
  }, ...tabs.map((tab) => ({
    key: tab.key,
    label: (
      <span className="workspace-utility-tab-label">
        {getUtilityTabIcon(tab)}
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
        {tab.kind === 'writingMethods' ? (
          <div className="workspace-utility-content workspace-utility-content--writing-methods">
            <WritingMethodBindingsPanel bookId={bookId} />
          </div>
        ) : null}
        {tab.kind === 'knowledge' ? <KnowledgePanel key={String(bookId)} bookId={bookId} /> : null}
        {tab.kind === 'canon' ? (
          <div className="workspace-utility-content workspace-utility-content--canon">
            <ContinuationCanonPanel bookId={bookId} />
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
      <PurrTabs
        activeKey={activeKey ?? undefined}
        onChange={onActiveKeyChange}
        onEdit={(targetKey) => onCloseTab(targetKey)}
        destroyOnHidden={false}
        items={items}
        className="workspace-utility-tabs"
        tabBarExtraContent={(
          <div className="workspace-utility-actions">
            {!dockCollapsed && onToggleFullscreen ? (
              <PurrTooltip
                title={fullscreen ? '退出全屏' : '全屏'}
                open={fullscreenTooltipOpen}
                onOpenChange={setFullscreenTooltipOpen}
              >
                <PurrButton
                  type="text"
                  size="small"
                  icon={fullscreen ? <FullscreenExitIcon /> : <FullscreenIcon />}
                  onClick={(event) => {
                    setFullscreenTooltipOpen(false)
                    event.currentTarget.blur()
                    onToggleFullscreen()
                  }}
                  className="workspace-utility-action-btn"
                  aria-label={fullscreen ? '退出工作面板全屏' : '全屏显示工作面板'}
                />
              </PurrTooltip>
            ) : null}
            {dockCollapsed && onExpandDock ? (
              <PurrTooltip title="固定展开工作面板">
                <PurrButton
                  type="text"
                  size="small"
                  icon={<PanelToggleIcon side="right" state="collapsed" />}
                  onClick={onExpandDock}
                  className="workspace-utility-action-btn"
                  aria-label="固定展开工作面板"
                />
              </PurrTooltip>
            ) : null}
            {!dockCollapsed && onCollapse ? (
              <PurrTooltip title="收起工作面板">
                <PurrButton
                  type="text"
                  size="small"
                  icon={<PanelToggleIcon side="right" state="expanded" />}
                  onClick={onCollapse}
                  className="workspace-utility-action-btn"
                  aria-label="收起工作面板"
                />
              </PurrTooltip>
            ) : null}
          </div>
        )}
      />
    </div>
  )
}
