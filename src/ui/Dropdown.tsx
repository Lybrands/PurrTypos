import React from 'react'
import { Menu } from '@base-ui/react/menu'
import { Button, type ButtonProps } from './Button'
import './ui.scss'

type DropdownPlacement = 'topLeft' | 'topRight' | 'bottomLeft' | 'bottomRight'

const placementMap: Record<DropdownPlacement, { side: 'top' | 'bottom'; align: 'start' | 'end' }> = {
  topLeft: { side: 'top', align: 'start' }, topRight: { side: 'top', align: 'end' },
  bottomLeft: { side: 'bottom', align: 'start' }, bottomRight: { side: 'bottom', align: 'end' },
}

export interface DropdownItem {
  key: React.Key
  label: React.ReactNode
  icon?: React.ReactNode
  onClick?: (info: { key: React.Key }) => void
  disabled?: boolean
  danger?: boolean
}

export interface DropdownProps {
  children: React.ReactElement
  menu: {
    items?: Array<DropdownItem | null>
    onClick?: (info: { key: React.Key }) => void
  }
  placement?: DropdownPlacement
  disabled?: boolean
  trigger?: Array<'click' | 'hover' | 'contextMenu'>
}

/** 基于 Base UI Menu 的项目内下拉操作菜单。 */
function DropdownBase({ children, menu, placement = 'bottomLeft', disabled }: DropdownProps) {
  const position = placementMap[placement]
  return (
    <Menu.Root>
      <Menu.Trigger render={<span className="purr-popup-trigger" />} nativeButton={false} disabled={disabled}>
        {children}
      </Menu.Trigger>
      <Menu.Portal>
        <Menu.Positioner side={position.side} align={position.align} sideOffset={6}>
          <Menu.Popup className="purr-dropdown">
            {(menu.items ?? []).filter((item): item is DropdownItem => item != null).map((item) => (
              <Menu.Item
                key={item.key}
                className={['purr-dropdown__item', item.danger && 'purr-dropdown__item--danger'].filter(Boolean).join(' ')}
                disabled={item.disabled}
                onClick={() => {
                  const info = { key: item.key }
                  item.onClick?.(info)
                  menu.onClick?.(info)
                }}
              >
                {item.icon && <span className="purr-dropdown__icon">{item.icon}</span>}
                {item.label}
              </Menu.Item>
            ))}
          </Menu.Popup>
        </Menu.Positioner>
      </Menu.Portal>
    </Menu.Root>
  )
}

interface DropdownButtonProps extends Omit<ButtonProps, 'icon'> {
  menu: DropdownProps['menu']
  icon?: React.ReactNode
  trigger?: DropdownProps['trigger']
  placement?: DropdownPlacement
}

function DropdownButton({ menu, icon, trigger, placement, children, className, ...buttonProps }: DropdownButtonProps) {
  return (
    <span className={['purr-dropdown-button', className].filter(Boolean).join(' ')}>
      <Button {...buttonProps}>{children}</Button>
      <DropdownBase menu={menu} trigger={trigger} placement={placement}>
        <Button {...buttonProps} icon={icon} aria-label="更多操作" />
      </DropdownBase>
    </span>
  )
}

type DropdownComponent = typeof DropdownBase & { Button: typeof DropdownButton }

export const Dropdown = DropdownBase as DropdownComponent
Dropdown.Button = DropdownButton
