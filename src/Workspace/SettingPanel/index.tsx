import React from 'react'
import { Tabs } from 'antd'
import { TeamOutlined, GlobalOutlined } from '@ant-design/icons'
import type { EntityId } from '../../types'
import CharacterTab from '../OutlinePanel/CharacterTab'
import StoryBackgroundTab from '../OutlinePanel/StoryBackgroundTab'
import './SettingPanel.scss'

export type SettingPanelTab = 'characters' | 'background'

export interface OpenSettingPanelDetail {
  tab?: SettingPanelTab
  characterId?: number | null
}

interface SettingPanelProps {
  bookId: EntityId | null
}

export default function SettingPanel({ bookId }: SettingPanelProps) {
  const [activeTab, setActiveTab] = React.useState<SettingPanelTab>('characters')
  const [focusCharacterId, setFocusCharacterId] = React.useState<number | null>(null)

  React.useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent<OpenSettingPanelDetail>).detail
      if (detail?.tab) setActiveTab(detail.tab)
      if (detail?.characterId != null) setFocusCharacterId(detail.characterId)
    }
    window.addEventListener('open-setting-panel', handler as EventListener)
    return () => window.removeEventListener('open-setting-panel', handler as EventListener)
  }, [])

  const tabItems = [
    {
      key: 'characters',
      label: '人物',
      icon: <TeamOutlined />,
      children: (
        <CharacterTab
          bookId={bookId}
          hideHeader
          focusCharacterId={focusCharacterId}
          onFocusCharacterHandled={() => setFocusCharacterId(null)}
        />
      ),
    },
    {
      key: 'background',
      label: '故事背景',
      icon: <GlobalOutlined />,
      children: <StoryBackgroundTab bookId={bookId} />,
    },
  ]

  return (
    <div className="setting-panel">
      <Tabs
        activeKey={activeTab}
        onChange={(key) => setActiveTab(key as SettingPanelTab)}
        items={tabItems}
        className="setting-panel-tabs"
        destroyOnHidden={false}
      />
    </div>
  )
}
