import React from 'react'
import { Checkbox as BaseCheckbox } from '@base-ui/react/checkbox'
import '../styles/purr.scss'

export interface PurrCheckboxProps {
  checked: boolean
  onChange?: (event: { target: { checked: boolean } }) => void
  children?: React.ReactNode
  disabled?: boolean
  indeterminate?: boolean
  className?: string
  onClick?: React.MouseEventHandler<HTMLLabelElement>
}

export function PurrCheckbox({ checked, onChange, children, disabled, indeterminate, className, onClick }: PurrCheckboxProps) {
  return (
    <label className={['purr-checkbox-label', className].filter(Boolean).join(' ')} onClick={onClick}>
      <BaseCheckbox.Root
        className="purr-checkbox"
        checked={checked}
        disabled={disabled}
        indeterminate={indeterminate}
        onCheckedChange={(nextChecked) => onChange?.({ target: { checked: nextChecked } })}
      >
        <BaseCheckbox.Indicator className="purr-checkbox__indicator">
          {indeterminate ? '−' : '✓'}
        </BaseCheckbox.Indicator>
      </BaseCheckbox.Root>
      {children != null && <span>{children}</span>}
    </label>
  )
}
