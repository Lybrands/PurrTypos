import '../styles/purr.scss'

export interface PurrInputNumberProps {
  value?: number | null
  onChange?: (value: number | null) => void
  min?: number
  max?: number
  step?: number
  size?: 'small' | 'middle' | 'large'
  className?: string
  placeholder?: string
}

export function PurrInputNumber({
  value,
  onChange,
  min,
  max,
  step,
  size = 'middle',
  className,
  placeholder,
}: PurrInputNumberProps) {
  return (
    <input
      type="number"
      className={['purr-input', 'purr-input-number', `purr-input--${size}`, className].filter(Boolean).join(' ')}
      value={value ?? ''}
      min={min}
      max={max}
      step={step}
      placeholder={placeholder}
      onChange={(event) => onChange?.(event.target.value === '' ? null : Number(event.target.value))}
    />
  )
}
