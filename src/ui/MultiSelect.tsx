import React from 'react'
import { Select as BaseSelect } from '@base-ui/react/select'
import type { SelectOption } from './Select'
import './ui.scss'

export interface MultiSelectProps<T extends string | number> {
  value: T[]
  onChange: (value: T[]) => void
  options: SelectOption<T>[]
  placeholder?: React.ReactNode
  disabled?: boolean
  className?: string
  size?: 'small' | 'middle' | 'large'
  allowClear?: boolean
}

/** 基础多选控件，适用于关联章节/大纲等有限选项场景。 */
export function MultiSelect<T extends string | number>({ value, onChange, options, placeholder, disabled, className, size = 'middle', allowClear }: MultiSelectProps<T>) {
  return (
    <BaseSelect.Root<T, true> value={value} onValueChange={(next) => onChange(next ?? [])} items={options} disabled={disabled} multiple modal={false}>
      <BaseSelect.Trigger className={['purr-select', 'purr-multi-select', `purr-select--${size}`, className].filter(Boolean).join(' ')}>
        <BaseSelect.Value className="purr-select__value" placeholder={placeholder}>
          {(selected) => {
            const selectedValues = Array.isArray(selected) ? selected : []
            if (!selectedValues.length) return placeholder
            return options
              .filter((option) => selectedValues.includes(option.value))
              .map((option, index) => (
                <React.Fragment key={option.value}>
                  {index > 0 ? '、' : null}
                  {option.label}
                </React.Fragment>
              ))
          }}
        </BaseSelect.Value>
        {allowClear && value.length > 0 && (
          <span className="purr-select__clear" role="button" tabIndex={0} aria-label="清空" onPointerDown={(event) => { event.preventDefault(); event.stopPropagation() }} onClick={(event) => { event.stopPropagation(); onChange([]) }}>×</span>
        )}
        <BaseSelect.Icon className="purr-select__icon">⌄</BaseSelect.Icon>
      </BaseSelect.Trigger>
      <BaseSelect.Portal>
        <BaseSelect.Positioner side="bottom" align="start" sideOffset={4}>
          <BaseSelect.Popup className="purr-select__popup">
            <BaseSelect.List className="purr-select__list">
              {options.map((option) => (
                <BaseSelect.Item key={option.value} value={option.value} disabled={option.disabled} className="purr-select__item">
                  <BaseSelect.ItemText>{option.label}</BaseSelect.ItemText>
                  <BaseSelect.ItemIndicator className="purr-select__check">✓</BaseSelect.ItemIndicator>
                </BaseSelect.Item>
              ))}
            </BaseSelect.List>
          </BaseSelect.Popup>
        </BaseSelect.Positioner>
      </BaseSelect.Portal>
    </BaseSelect.Root>
  )
}
