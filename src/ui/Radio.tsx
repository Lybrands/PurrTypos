import React from 'react'
import './ui.scss'

export interface RadioChangeEvent<T = unknown> {
  target: { value: T; checked: boolean }
}

interface RadioGroupContextValue {
  value: unknown
  onChange?: (event: RadioChangeEvent) => void
  button: boolean
  name?: string
}

const RadioGroupContext = React.createContext<RadioGroupContextValue | null>(null)

export interface RadioProps<T = unknown> {
  checked?: boolean
  value: T
  onChange?: (event: RadioChangeEvent<T>) => void
  children?: React.ReactNode
  disabled?: boolean
  className?: string
}

function RadioBase<T>({ checked, value, onChange, children, disabled, className }: RadioProps<T>) {
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
      className,
    ].filter(Boolean).join(' ')}>
      <input className="purr-radio" type="radio" name={group?.name} value={String(value)} checked={resolvedChecked} disabled={disabled} onChange={emit} />
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
  onChange?: (event: RadioChangeEvent<T>) => void
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
    onChange: onChange as ((event: RadioChangeEvent) => void) | undefined,
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

export const Radio = RadioBase as RadioComponent
Radio.Group = RadioGroup
Radio.Button = RadioBase
