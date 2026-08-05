import React from 'react'
import { CheckIcon } from '../icons'
import '../styles/purr.scss'

export interface PurrRadioChangeEvent<T = unknown> {
  target: { value: T; checked: boolean }
}

interface RadioGroupContextValue {
  value: unknown
  onChange?: (event: PurrRadioChangeEvent) => void
  button: boolean
  name?: string
}

const RadioGroupContext = React.createContext<RadioGroupContextValue | null>(null)

export interface PurrRadioProps<T = unknown> {
  checked?: boolean
  value: T
  onChange?: (event: PurrRadioChangeEvent<T>) => void
  children?: React.ReactNode
  disabled?: boolean
  className?: string
}

function RadioBase<T>({ checked, value, onChange, children, disabled, className }: PurrRadioProps<T>) {
  const group = React.useContext(RadioGroupContext)
  const resolvedChecked = checked ?? Object.is(group?.value, value)
  const button = group?.button ?? false
  const emit = () => {
    const event = { target: { value, checked: true } }
    onChange?.(event)
    group?.onChange?.(event)
  }
  return (
    <label className={[
      button ? 'purr-radio-button' : 'purr-radio-label',
      resolvedChecked && 'is-checked',
      button && resolvedChecked && 'is-checked',
      disabled && 'is-disabled',
      className,
    ].filter(Boolean).join(' ')}>
      <input className="purr-radio" type="radio" name={group?.name} value={String(value)} checked={resolvedChecked} disabled={disabled} onChange={emit} />
      {!button && (
        <span className="purr-selection-mark purr-radio__mark" aria-hidden="true">
          <CheckIcon />
        </span>
      )}
      <span>{children}</span>
    </label>
  )
}

function RadioGroup<T>({
  value,
  onChange,
  children,
  optionType,
  size = 'middle',
  className,
  name,
  options,
}: {
  value?: T
  onChange?: (event: PurrRadioChangeEvent<T>) => void
  children?: React.ReactNode
  optionType?: 'default' | 'button'
  buttonStyle?: 'outline' | 'solid'
  size?: 'small' | 'middle' | 'large'
  className?: string
  name?: string
  options?: Array<T | { label: React.ReactNode; value: T; disabled?: boolean }>
}) {
  const context = React.useMemo<RadioGroupContextValue>(() => ({
    value,
    onChange: onChange as ((event: PurrRadioChangeEvent) => void) | undefined,
    button: optionType === 'button',
    name,
  }), [name, onChange, optionType, value])
  return (
    <RadioGroupContext.Provider value={context}>
      <div className={['purr-radio-group', `purr-radio-group--${size}`, optionType === 'button' && 'purr-radio-group--button', className].filter(Boolean).join(' ')}>
        {children ?? options?.map((raw) => {
          const option = typeof raw === 'object' && raw !== null && 'value' in raw
            ? raw
            : { label: String(raw), value: raw as T }
          return (
            <RadioBase key={String(option.value)} value={option.value} disabled={option.disabled}>
              {option.label}
            </RadioBase>
          )
        })}
      </div>
    </RadioGroupContext.Provider>
  )
}

type RadioComponent = typeof RadioBase & {
  Group: typeof RadioGroup
  Button: typeof RadioBase
}

export const PurrRadio = RadioBase as RadioComponent
PurrRadio.Group = RadioGroup
PurrRadio.Button = RadioBase
