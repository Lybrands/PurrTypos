import React from 'react'
import { PurrEmpty, PurrTabs } from '@/purr-components'
import type { EntityId } from '../../types'
import StoryHealthTab from './StoryHealthTab'
import WritingStatsTab from './WritingStatsTab'
import './DashboardPanel.scss'

interface DashboardPanelProps {
  bookId: EntityId | null
}

type DashboardTab = 'health' | 'stats'

export default function DashboardPanel({ bookId }: DashboardPanelProps) {
  const [activeTab, setActiveTab] = React.useState<DashboardTab>('health')

  if (bookId == null) {
    return (
      <div className="dashboard-panel dashboard-panel--empty">
        <PurrEmpty description="请先选择书籍" />
      </div>
    )
  }

  return (
    <div className="dashboard-panel">
      <PurrTabs
        activeKey={activeTab}
        onChange={(key) => setActiveTab(key as DashboardTab)}
        className="dashboard-panel-tabs"
        destroyOnHidden={false}
        items={[
          { key: 'health', label: '故事健康', children: <StoryHealthTab bookId={bookId} /> },
          { key: 'stats', label: '写作统计', children: <WritingStatsTab bookId={bookId} /> },
        ]}
      />
    </div>
  )
}
