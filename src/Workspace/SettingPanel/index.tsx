import React from 'react'
import { Tabs } from '../../ui'
import { TeamOutlined, GlobalOutlined, CompassOutlined } from '../../ui'
import type { EntityId } from '../../types'
import CharacterTab from '../OutlinePanel/CharacterTab'
import StoryBackgroundTab from '../OutlinePanel/StoryBackgroundTab'
import WorldEntityTab from '../OutlinePanel/WorldEntityTab'
import './SettingPanel.scss'

export type SettingPanelTab = 'characters' | 'background' | 'entities'

export interface OpenSettingPanelDetail {
  tab?: SettingPanelTab
  characterId?: number | null
  entityId?: number | null
}

interface SettingPanelProps {
  bookId: EntityId | null
  /** 面板首次挂载前收到的定位请求也能通过 prop 补发。 */
  openRequest?: OpenSettingPanelDetail | null
}

export default function SettingPanel({ bookId, openRequest }: SettingPanelProps) {
  const [activeTab, setActiveTab] = React.useState<SettingPanelTab>('characters')
  const [focusCharacterId, setFocusCharacterId] = React.useState<number | null>(null)
  const [focusEntityId, setFocusEntityId] = React.useState<number | null>(null)

  const applyOpenRequest = React.useCallback((detail?: OpenSettingPanelDetail | null) => {
    if (detail?.tab) setActiveTab(detail.tab)
    if (detail?.characterId != null) setFocusCharacterId(detail.characterId)
    if (detail?.entityId != null) setFocusEntityId(detail.entityId)
  }, [])

  React.useEffect(() => {
    applyOpenRequest(openRequest)
  }, [applyOpenRequest, openRequest])

  React.useEffect(() => {
    const handler = (e: Event) => {
      applyOpenRequest((e as CustomEvent<OpenSettingPanelDetail>).detail)
    }
    window.addEventListener('open-setting-panel', handler as EventListener)
    return () => window.removeEventListener('open-setting-panel', handler as EventListener)
  }, [applyOpenRequest])

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
    {
      key: 'entities',
      label: '世界设定',
      icon: <CompassOutlined />,
      children: (
        <WorldEntityTab
          bookId={bookId}
          focusEntityId={focusEntityId}
          onFocusEntityHandled={() => setFocusEntityId(null)}
        />
      ),
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
