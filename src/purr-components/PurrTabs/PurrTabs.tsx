import React from 'react'
import { Tabs as BaseTabs } from '@base-ui/react/tabs'
import '../styles/purr.scss'

export interface PurrTabItem {
  key: string
  label: React.ReactNode
  children?: React.ReactNode
  icon?: React.ReactNode
  disabled?: boolean
  closable?: boolean
}

export interface PurrTabsProps {
  items: PurrTabItem[]
  activeKey?: string
  defaultActiveKey?: string
  onChange?: (key: string) => void
  className?: string
  destroyOnHidden?: boolean
  onEdit?: (key: string, action: 'remove') => void
  tabBarExtraContent?: React.ReactNode | { left?: React.ReactNode; right?: React.ReactNode }
}

/** 项目内 PurrTabs 入口，使用 items/activeKey API，交互由 Base UI 管理。 */
export function PurrTabs({ items, activeKey, defaultActiveKey, onChange, className, destroyOnHidden = false, onEdit, tabBarExtraContent }: PurrTabsProps) {
  const hasPanels = items.some((item) => item.children !== undefined)
  const extra: { left?: React.ReactNode; right?: React.ReactNode } =
    typeof tabBarExtraContent === 'object' &&
    tabBarExtraContent !== null &&
    !React.isValidElement(tabBarExtraContent) &&
    ('left' in tabBarExtraContent || 'right' in tabBarExtraContent)
      ? tabBarExtraContent
      : { right: tabBarExtraContent as React.ReactNode }
  return (
    <BaseTabs.Root
      className={['purr-tabs', className].filter(Boolean).join(' ')}
      value={activeKey}
      defaultValue={defaultActiveKey ?? items[0]?.key}
      onValueChange={(value) => { if (typeof value === 'string') onChange?.(value) }}
    >
      <div className="purr-tabs__bar">
        {extra.left && <div className="purr-tabs__extra purr-tabs__extra--left">{extra.left}</div>}
        <BaseTabs.List className="purr-tabs__nav">
          {items.map((item) => (
            <BaseTabs.Tab key={item.key} value={item.key} disabled={item.disabled} className="purr-tabs__tab">
              {item.icon && <span className="purr-tabs__icon">{item.icon}</span>}
              {item.label}
              {item.closable && (
                <span
                  className="purr-tabs__remove"
                  role="button"
                  tabIndex={0}
                  aria-label="关闭标签"
                  onPointerDown={(event) => { event.preventDefault(); event.stopPropagation() }}
                  onClick={(event) => { event.stopPropagation(); onEdit?.(item.key, 'remove') }}
                  onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onEdit?.(item.key, 'remove') } }}
                >×</span>
              )}
            </BaseTabs.Tab>
          ))}
        </BaseTabs.List>
        {extra.right && <div className="purr-tabs__extra purr-tabs__extra--right">{extra.right}</div>}
      </div>
      {hasPanels && (
        <div className="purr-tabs__content-holder purr-tabs__content">
          {items.map((item) => (
            <BaseTabs.Panel key={item.key} value={item.key} keepMounted={!destroyOnHidden} className="purr-tabs__panel">
              {item.children}
            </BaseTabs.Panel>
          ))}
        </div>
      )}
    </BaseTabs.Root>
  )
}
