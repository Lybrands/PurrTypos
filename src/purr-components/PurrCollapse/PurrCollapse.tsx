import React from 'react'
import { Accordion } from '@base-ui/react/accordion'
import { ChevronRightIcon } from '../icons'
import '../styles/purr.scss'

export interface PurrCollapseItem {
  key: string
  label: React.ReactNode
  children: React.ReactNode
  extra?: React.ReactNode
  disabled?: boolean
}

export interface PurrCollapseProps {
  items: PurrCollapseItem[]
  activeKeys?: string[]
  defaultActiveKeys?: string[]
  accordion?: boolean
  onChange?: (keys: string[]) => void
  className?: string
  size?: 'default' | 'small'
}

/** 项目统一的可折叠内容容器，键盘与 ARIA 行为由 Base UI Accordion 提供。 */
export function PurrCollapse({
  items,
  activeKeys,
  defaultActiveKeys = [],
  accordion = false,
  onChange,
  className,
  size = 'default',
}: PurrCollapseProps) {
  return (
    <Accordion.Root
      className={['purr-collapse', `purr-collapse--${size}`, className].filter(Boolean).join(' ')}
      value={activeKeys}
      defaultValue={defaultActiveKeys}
      multiple={!accordion}
      onValueChange={(keys) => onChange?.(keys.map(String))}
    >
      {items.map((item) => (
        <Accordion.Item key={item.key} value={item.key} disabled={item.disabled} className="purr-collapse__item">
          <Accordion.Header className="purr-collapse__header">
            <Accordion.Trigger className="purr-collapse__trigger">
              <ChevronRightIcon className="purr-collapse__chevron" />
              <span className="purr-collapse__label">{item.label}</span>
            </Accordion.Trigger>
            {item.extra && <span className="purr-collapse__extra">{item.extra}</span>}
          </Accordion.Header>
          <Accordion.Panel className="purr-collapse__panel">
            <div className="purr-collapse__content">{item.children}</div>
          </Accordion.Panel>
        </Accordion.Item>
      ))}
    </Accordion.Root>
  )
}
