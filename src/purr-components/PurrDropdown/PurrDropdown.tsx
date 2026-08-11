import React from 'react'
import { ContextMenu } from '@base-ui/react/context-menu'
import { Menu } from '@base-ui/react/menu'
import { PurrButton, type PurrButtonProps } from '../PurrButton'
import { getOverlayLayerStyle } from '../overlayLayer'
import '../styles/purr.scss'

type DropdownPlacement = 'topLeft' | 'topRight' | 'bottomLeft' | 'bottomRight'

const placementMap: Record<DropdownPlacement, { side: 'top' | 'bottom'; align: 'start' | 'end' }> = {
  topLeft: { side: 'top', align: 'start' }, topRight: { side: 'top', align: 'end' },
  bottomLeft: { side: 'bottom', align: 'start' }, bottomRight: { side: 'bottom', align: 'end' },
}

export interface PurrDropdownItem {
  key: React.Key
  label: React.ReactNode
  icon?: React.ReactNode
  onClick?: (info: { key: React.Key }) => void
  disabled?: boolean
  danger?: boolean
}

export interface PurrDropdownProps {
  children: React.ReactElement
  menu: {
    items?: Array<PurrDropdownItem | null>
    onClick?: (info: { key: React.Key }) => void
  }
  placement?: DropdownPlacement
  disabled?: boolean
  trigger?: Array<'click' | 'hover' | 'contextMenu'>
  zIndex?: number
}

function DropdownMenuItems({
  items,
  onClick,
  Item,
}: {
  items: Array<PurrDropdownItem | null>
  onClick?: (info: { key: React.Key }) => void
  Item: typeof Menu.Item
}) {
  return items.filter((item): item is PurrDropdownItem => item != null).map((item) => (
    <Item
      key={item.key}
      className={['purr-dropdown__item', item.danger && 'purr-dropdown__item--danger'].filter(Boolean).join(' ')}
      disabled={item.disabled}
      onClick={() => {
        const info = { key: item.key }
        item.onClick?.(info)
        onClick?.(info)
      }}
    >
      {item.icon && <span className="purr-dropdown__icon">{item.icon}</span>}
      {item.label}
    </Item>
  ))
}

/** 基于 Base UI Menu 的项目内下拉操作菜单，同时支持右键上下文菜单。 */
function DropdownBase({ children, menu, placement = 'bottomLeft', disabled, trigger, zIndex }: PurrDropdownProps) {
  const position = placementMap[placement]
  const items = menu.items ?? []
  const layerStyle = getOverlayLayerStyle('PurrDropdown', zIndex)

  if (trigger?.includes('contextMenu')) {
    return (
      <ContextMenu.Root disabled={disabled}>
        <ContextMenu.Trigger render={<span className="purr-popup-trigger" />}>
          {children}
        </ContextMenu.Trigger>
        <ContextMenu.Portal>
          <ContextMenu.Positioner className="purr-dropdown__positioner" style={layerStyle}>
            <ContextMenu.Popup className="purr-dropdown">
              <DropdownMenuItems
                items={items}
                onClick={menu.onClick}
                Item={ContextMenu.Item}
              />
            </ContextMenu.Popup>
          </ContextMenu.Positioner>
        </ContextMenu.Portal>
      </ContextMenu.Root>
    )
  }

  return (
    <Menu.Root>
      <Menu.Trigger render={<span className="purr-popup-trigger" />} nativeButton={false} disabled={disabled}>
        {children}
      </Menu.Trigger>
      <Menu.Portal>
        <Menu.Positioner
          className="purr-dropdown__positioner"
          style={layerStyle}
          side={position.side}
          align={position.align}
          sideOffset={6}
        >
          <Menu.Popup className="purr-dropdown">
            <DropdownMenuItems items={items} onClick={menu.onClick} Item={Menu.Item} />
          </Menu.Popup>
        </Menu.Positioner>
      </Menu.Portal>
    </Menu.Root>
  )
}

interface DropdownButtonProps extends Omit<PurrButtonProps, 'icon'> {
  menu: PurrDropdownProps['menu']
  icon?: React.ReactNode
  trigger?: PurrDropdownProps['trigger']
  placement?: DropdownPlacement
  dropdownAriaLabel?: string
  zIndex?: number
}

function DropdownButton({
  menu,
  icon,
  trigger,
  placement,
  zIndex,
  children,
  className,
  dropdownAriaLabel = '更多操作',
  onClick,
  ...buttonProps
}: DropdownButtonProps) {
  return (
    <span className={['purr-dropdown-button', className].filter(Boolean).join(' ')}>
      <PurrButton {...buttonProps} onClick={onClick}>{children}</PurrButton>
      <DropdownBase menu={menu} trigger={trigger} placement={placement} zIndex={zIndex}>
        {/* The disclosure half only opens the menu. Never forward the
            primary action's onClick to it. */}
        <PurrButton {...buttonProps} icon={icon} aria-label={dropdownAriaLabel} />
      </DropdownBase>
    </span>
  )
}

type DropdownComponent = typeof DropdownBase & { Button: typeof DropdownButton }

export const PurrDropdown = DropdownBase as DropdownComponent
PurrDropdown.Button = DropdownButton
