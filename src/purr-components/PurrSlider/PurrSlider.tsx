import React from 'react'
import '../styles/purr.scss'

export interface PurrSliderProps {
  value?: number
  defaultValue?: number
  onChange?: (value: number) => void
  min?: number
  max?: number
  step?: number
  disabled?: boolean
  tooltip?: { formatter?: (value?: number) => React.ReactNode }
  showValue?: boolean
  valueLabel?: string
  className?: string
}

export function PurrSlider({
  value,
  defaultValue,
  onChange,
  min = 0,
  max = 100,
  step = 1,
  disabled,
  tooltip,
  showValue = false,
  valueLabel = '当前值',
  className,
}: PurrSliderProps) {
  const [internalValue, setInternalValue] = React.useState(defaultValue ?? min)
  const inputId = React.useId()
  const resolvedValue = value ?? internalValue
  const range = max - min
  const progress = range > 0
    ? Math.min(100, Math.max(0, ((resolvedValue - min) / range) * 100))
    : 0
  const formattedValue = tooltip?.formatter
    ? tooltip.formatter(resolvedValue)
    : resolvedValue
  const ariaValueText = typeof formattedValue === 'string'
    || typeof formattedValue === 'number'
    ? String(formattedValue)
    : undefined

  return (
    <span className={['purr-slider-control', className].filter(Boolean).join(' ')}>
      <span className="purr-slider-control__track">
        <input
          id={inputId}
          type="range"
          className="purr-slider"
          value={resolvedValue}
          min={min}
          max={max}
          step={step}
          disabled={disabled}
          aria-valuetext={ariaValueText}
          onInput={(event) => {
            const nextValue = Number(event.currentTarget.value)
            setInternalValue(nextValue)
            onChange?.(nextValue)
          }}
        />
        {tooltip && !showValue && (
          <span
            className="purr-slider__tooltip"
            style={{ left: `${progress}%` }}
            aria-hidden="true"
          >
            {formattedValue}
          </span>
        )}
      </span>
      {showValue && (
        <output
          className="purr-slider__value"
          htmlFor={inputId}
          aria-label={valueLabel}
          aria-live="polite"
        >
          {formattedValue}
        </output>
      )}
    </span>
  )
}
