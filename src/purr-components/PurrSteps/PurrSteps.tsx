import React from 'react'
import { CheckIcon } from '../icons'
import '../styles/purr.scss'

export interface PurrStepItem<Key extends React.Key = string> {
  key: Key
  title: React.ReactNode
  disabled?: boolean
}

export interface PurrStepsProps<Key extends React.Key = string> {
  items: PurrStepItem<Key>[]
  current: number
  availableUntil?: number
  completedUntil?: number
  onChange?: (index: number, item: PurrStepItem<Key>) => void
  ariaLabel?: string
  className?: string
}

export function PurrSteps<Key extends React.Key = string>({
  items,
  current,
  availableUntil = items.length - 1,
  completedUntil = current - 1,
  onChange,
  ariaLabel = '步骤导航',
  className,
}: PurrStepsProps<Key>) {
  return (
    <nav
      className={['purr-steps', className].filter(Boolean).join(' ')}
      aria-label={ariaLabel}
    >
      {items.map((item, index) => {
        const isCurrent = index === current
        const isCompleted = !isCurrent && index <= completedUntil
        const isAvailable = index <= availableUntil && !item.disabled
        return (
          <button
            type="button"
            key={item.key}
            className={[
              'purr-steps__item',
              isCurrent ? 'is-current' : '',
              isCompleted ? 'is-completed' : '',
            ].filter(Boolean).join(' ')}
            disabled={!isAvailable}
            aria-current={isCurrent ? 'step' : undefined}
            onClick={() => onChange?.(index, item)}
          >
            <span className="purr-steps__index">
              {isCompleted ? <CheckIcon /> : index + 1}
            </span>
            <span className="purr-steps__title">{item.title}</span>
          </button>
        )
      })}
    </nav>
  )
}
