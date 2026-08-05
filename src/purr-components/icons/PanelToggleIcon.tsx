import React from 'react'
import { PurrIcon, type PurrIconProps } from './PurrIcon'

export interface PanelToggleIconProps extends Omit<PurrIconProps, 'children'> {
  side: 'left' | 'right'
  state: 'collapsed' | 'expanded'
}

export const PanelToggleIcon = React.forwardRef<HTMLSpanElement, PanelToggleIconProps>(
  ({ side, state, className, ...props }, ref) => {
    const dividerXByState = {
      right: { collapsed: 15.5, expanded: 15.5 },
      left: { collapsed: 8.5, expanded: 8.5 },
    } as const
    const expandedPanelPathBySide = {
      right: 'M15.5 4.5h2a3 3 0 0 1 3 3v9a3 3 0 0 1-3 3h-2Z',
      left: 'M8.5 4.5h-2a3 3 0 0 0-3 3v9a3 3 0 0 0 3 3h2Z',
    } as const
    const dividerX = dividerXByState[side][state]

    return (
      <PurrIcon
        {...props}
        ref={ref}
        className={['purr-panel-toggle-icon', className].filter(Boolean).join(' ')}
        data-side={side}
        data-state={state}
      >
        {state === 'expanded' && (
          <path d={expandedPanelPathBySide[side]} fill="currentColor" stroke="none" />
        )}
        <rect x="3.5" y="4.5" width="17" height="15" rx="3" />
        <path d={`M${dividerX} 4.75v14.5`} />
      </PurrIcon>
    )
  },
)

PanelToggleIcon.displayName = 'PanelToggleIcon'
