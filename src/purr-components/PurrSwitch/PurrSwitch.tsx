import React from 'react'
import '../styles/purr.scss'

export interface PurrSwitchProps {
  checked?: boolean
  defaultChecked?: boolean
  onChange?: (checked: boolean) => void
  disabled?: boolean
  loading?: boolean
  size?: 'small' | 'default'
  className?: string
}

export function PurrSwitch({
  checked,
  defaultChecked,
  onChange,
  disabled,
  loading,
  size = 'default',
  className,
}: PurrSwitchProps) {
  const [internal, setInternal] = React.useState(Boolean(defaultChecked))
  const resolved = checked ?? internal
  return (
    <button
      type="button"
      role="switch"
      aria-checked={resolved}
      disabled={disabled || loading}
      className={['purr-switch', `purr-switch--${size}`, resolved && 'is-checked', className].filter(Boolean).join(' ')}
      onClick={() => {
        const next = !resolved
        if (checked == null) setInternal(next)
        onChange?.(next)
      }}
    >
      <span className="purr-switch__thumb" />
    </button>
  )
}
