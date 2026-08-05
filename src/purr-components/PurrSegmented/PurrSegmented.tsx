import React from 'react'
import '../styles/purr.scss'

export interface PurrSegmentedOption<T extends string | number> {
  label: React.ReactNode
  value: T
  disabled?: boolean
}

export interface PurrSegmentedProps<T extends string | number> {
  value?: T
  options: Array<T | PurrSegmentedOption<T>>
  onChange?: (value: T) => void
  size?: 'small' | 'middle' | 'large'
  block?: boolean
  className?: string
}

export function PurrSegmented<T extends string | number>({
  value,
  options,
  onChange,
  size = 'middle',
  block,
  className,
}: PurrSegmentedProps<T>) {
  return (
    <div className={['purr-segmented', `purr-segmented--${size}`, block && 'purr-segmented--block', className].filter(Boolean).join(' ')}>
      {options.map((raw) => {
        const option = typeof raw === 'object' ? raw : { label: raw, value: raw }
        return (
          <button
            type="button"
            key={option.value}
            disabled={option.disabled}
            className={['purr-segmented__item', option.value === value && 'is-selected'].filter(Boolean).join(' ')}
            onClick={() => onChange?.(option.value)}
          >
            {option.label}
          </button>
        )
      })}
    </div>
  )
}
