import React from 'react'
import { PurrButton, PurrTooltip } from '@/purr-components'
import {
  HighlightIcon,
  MasterOutlineIcon,
  StoryMemoryIcon,
} from '@/purr-components'
import { useWorkspace } from '../WorkspaceContext'
import {
  GLOBAL_OUTLINE_TAB,
  MEMORY_TAB,
  STYLE_TAB,
  type WorkspaceUtilityTab,
} from '../utilityPanelTypes'
import './NotebookToolbar.scss'

interface ToolDef {
  tab: WorkspaceUtilityTab
  icon: React.ReactNode
}

const TOOLS: ToolDef[] = [
  { tab: GLOBAL_OUTLINE_TAB, icon: <MasterOutlineIcon /> },
  { tab: MEMORY_TAB, icon: <StoryMemoryIcon /> },
  { tab: STYLE_TAB, icon: <HighlightIcon /> },
]

/** 工作台顶部的全书级工具入口；内容统一在右侧组合面板中以标签打开。 */
export default function NotebookToolbar() {
  const { utilityPanelOpen, activeUtilityTabKey, toggleUtilityTab } = useWorkspace()

  return (
    <div className="notebook-toolbar">
      {TOOLS.map(({ tab, icon }) => {
        const active = utilityPanelOpen && activeUtilityTabKey === tab.key
        return (
          <PurrTooltip key={tab.key} title={tab.title} placement="bottom">
            <PurrButton
              type="text"
              size="small"
              icon={icon}
              onClick={() => toggleUtilityTab(tab)}
              className={`app-header-action-btn notebook-toolbar-btn${active ? ' panel-toggle-active' : ''}`}
              aria-pressed={active}
              aria-label={tab.title}
            />
          </PurrTooltip>
        )
      })}
    </div>
  )
}
