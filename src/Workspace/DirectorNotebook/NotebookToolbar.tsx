import React from 'react'
import { Button, Tooltip } from 'antd'
import {
  BookOutlined,
  BulbOutlined,
  HighlightOutlined,
} from '@ant-design/icons'
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
  { tab: GLOBAL_OUTLINE_TAB, icon: <BookOutlined /> },
  { tab: MEMORY_TAB, icon: <BulbOutlined /> },
  { tab: STYLE_TAB, icon: <HighlightOutlined /> },
]

/** 工作台顶部的全书级工具入口；内容统一在正文左侧的辅助面板中打开。 */
export default function NotebookToolbar() {
  const { utilityPanelOpen, activeUtilityTabKey, toggleUtilityTab } = useWorkspace()

  return (
    <div className="notebook-toolbar">
      {TOOLS.map(({ tab, icon }) => {
        const active = utilityPanelOpen && activeUtilityTabKey === tab.key
        return (
          <Tooltip key={tab.key} title={tab.title} placement="bottom">
            <Button
              type="text"
              size="small"
              icon={icon}
              onClick={() => toggleUtilityTab(tab)}
              className={`app-header-action-btn notebook-toolbar-btn${active ? ' panel-toggle-active' : ''}`}
              aria-pressed={active}
              aria-label={tab.title}
            />
          </Tooltip>
        )
      })}
    </div>
  )
}
