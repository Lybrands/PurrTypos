import React from 'react'
import { CheckIcon } from '../icons'
import type { PurrRadioChangeEvent } from '../PurrRadio'
import '../styles/purr.scss'

type PurrChoiceCardSize = 'small' | 'middle' | 'large'
type PurrChoiceCardColumns = 1 | 2 | 3 | 4

interface ChoiceCardGroupContextValue {
  value: unknown
  onChange?: (event: PurrRadioChangeEvent) => void
  name: string
  size: PurrChoiceCardSize
}

const ChoiceCardGroupContext = React.createContext<ChoiceCardGroupContextValue | null>(null)

export interface PurrChoiceCardProps<T = unknown> {
  value: T
  title?: React.ReactNode
  description?: React.ReactNode
  children?: React.ReactNode
  checked?: boolean
  disabled?: boolean
  className?: string
  onChange?: (event: PurrRadioChangeEvent<T>) => void
}

function ChoiceCardBase<T>({
  value,
  title,
  description,
  children,
  checked,
  disabled,
  className,
  onChange,
}: PurrChoiceCardProps<T>) {
  const group = React.useContext(ChoiceCardGroupContext)
  const resolvedChecked = checked ?? Object.is(group?.value, value)
  const emit = () => {
    const event = { target: { value, checked: true } }
    onChange?.(event)
    group?.onChange?.(event)
  }

  return (
    <label className={[
      'purr-choice-card',
      `purr-choice-card--${group?.size ?? 'middle'}`,
      resolvedChecked && 'is-checked',
      disabled && 'is-disabled',
      className,
    ].filter(Boolean).join(' ')}>
      <input
        className="purr-choice-card__input"
        type="radio"
        name={group?.name}
        value={String(value)}
        checked={resolvedChecked}
        disabled={disabled}
        onChange={emit}
      />
      <span className="purr-selection-mark purr-choice-card__mark" aria-hidden="true">
        <CheckIcon />
      </span>
      <span className="purr-choice-card__content">
        {children ?? (
          <>
            {title != null && <strong>{title}</strong>}
            {description != null && <small>{description}</small>}
          </>
        )}
      </span>
    </label>
  )
}

export interface PurrChoiceCardGroupProps<T = unknown> {
  value?: T
  onChange?: (event: PurrRadioChangeEvent<T>) => void
  children: React.ReactNode
  name?: string
  size?: PurrChoiceCardSize
  columns?: PurrChoiceCardColumns
  className?: string
  ariaLabel?: string
  ariaLabelledBy?: string
}

function ChoiceCardGroup<T>({
  value,
  onChange,
  children,
  name,
  size = 'middle',
  columns = 2,
  className,
  ariaLabel,
  ariaLabelledBy,
}: PurrChoiceCardGroupProps<T>) {
  const generatedName = React.useId()
  const context = React.useMemo<ChoiceCardGroupContextValue>(() => ({
    value,
    onChange: onChange as ((event: PurrRadioChangeEvent) => void) | undefined,
    name: name ?? `purr-choice-card-${generatedName}`,
    size,
  }), [generatedName, name, onChange, size, value])

  return (
    <div
      className={[
        'purr-choice-card-group',
        `purr-choice-card-group--columns-${columns}`,
        className,
      ].filter(Boolean).join(' ')}
      role="radiogroup"
      aria-label={ariaLabel}
      aria-labelledby={ariaLabelledBy}
    >
      <ChoiceCardGroupContext.Provider value={context}>
        {children}
      </ChoiceCardGroupContext.Provider>
    </div>
  )
}

type PurrChoiceCardComponent = typeof ChoiceCardBase & {
  Group: typeof ChoiceCardGroup
}

export const PurrChoiceCard = ChoiceCardBase as PurrChoiceCardComponent
PurrChoiceCard.Group = ChoiceCardGroup
