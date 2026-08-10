import React from 'react'
import { Select as BaseSelect } from '@base-ui/react/select'
import '../styles/purr.scss'

function SelectChevron() {
  return (
    <svg
      viewBox="0 0 16 16"
      width="16"
      height="16"
      aria-hidden="true"
      focusable="false"
    >
      <path
        d="m4.25 6.25 3.75 3.5 3.75-3.5"
        fill="none"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="1.5"
      />
    </svg>
  )
}

export interface PurrSelectOption<T extends string | number = string | number> {
  value: T
  label: React.ReactNode
  disabled?: boolean
  [key: string]: unknown
}

export interface PurrSelectProps<T extends string | number = string | number> {
  value?: T | T[] | null
  defaultValue?: T | T[] | null
  onChange?: (value: any, option?: any) => void
  options?: PurrSelectOption<T>[]
  placeholder?: React.ReactNode
  disabled?: boolean
  className?: string
  classNames?: { popup?: { root?: string } }
  style?: React.CSSProperties
  size?: 'small' | 'middle' | 'large'
  mode?: 'multiple' | 'tags'
  allowClear?: boolean
  showSearch?: boolean
  optionFilterProp?: string
  filterOption?: boolean | ((input: string, option?: PurrSelectOption<T>) => boolean)
  notFoundContent?: React.ReactNode
  maxCount?: number
  maxTagCount?: number | 'responsive'
  open?: boolean
  onOpenChange?: (open: boolean) => void
  popupMatchSelectWidth?: boolean
  suffixIcon?: React.ReactNode
  variant?: 'outlined' | 'borderless'
  optionRender?: (option: { value: T; label: React.ReactNode; data: PurrSelectOption<T> }) => React.ReactNode
}

function TagsSelect<T extends string | number>({
  value,
  onChange,
  options = [],
  placeholder,
  disabled,
  className,
  style,
  size = 'middle',
  maxCount,
  allowClear,
  open,
}: PurrSelectProps<T>) {
  const values = Array.isArray(value) ? value : value == null || value === '' ? [] : [value]
  const [draft, setDraft] = React.useState('')

  const emit = (next: T[]) => {
    onChange?.(maxCount === 1 ? (next[0] ?? undefined) : next)
  }
  const addDraft = () => {
    const trimmed = draft.trim()
    if (!trimmed || (maxCount != null && values.length >= maxCount)) return
    const matching = options.find((option) => String(option.label).toLowerCase() === trimmed.toLowerCase())
    const nextValue = (matching?.value ?? trimmed) as T
    if (!values.includes(nextValue)) emit([...values, nextValue])
    setDraft('')
  }

  return (
    <div
      className={['purr-select', 'purr-tags-select', `purr-select--${size}`, disabled && 'is-disabled', className].filter(Boolean).join(' ')}
      style={style}
    >
      <div className="purr-tags-select__values">
        {values.map((selected) => {
          const label = options.find((option) => Object.is(option.value, selected))?.label ?? String(selected)
          return (
            <span key={String(selected)} className="purr-tags-select__tag">
              {label}
              <button type="button" aria-label="移除" disabled={disabled} onClick={() => emit(values.filter((item) => !Object.is(item, selected)))}>×</button>
            </span>
          )
        })}
        {open !== false && (
          <input
            value={draft}
            disabled={disabled || (maxCount != null && values.length >= maxCount)}
            placeholder={values.length ? undefined : typeof placeholder === 'string' ? placeholder : undefined}
            onChange={(event) => setDraft(event.target.value)}
            onBlur={addDraft}
            onKeyDown={(event) => {
              if (event.key === 'Enter' || event.key === ',') {
                event.preventDefault()
                addDraft()
              } else if (event.key === 'Backspace' && !draft && values.length) {
                emit(values.slice(0, -1))
              }
            }}
          />
        )}
      </div>
      {allowClear && values.length > 0 && <button type="button" className="purr-select__clear" onClick={() => emit([])}>×</button>}
    </div>
  )
}

/** 单选、多选与自由标签入口；选择行为和键盘导航由 Base UI 提供。 */
export function PurrSelect<T extends string | number = string | number>(props: PurrSelectProps<T>) {
  const {
    value,
    defaultValue,
    onChange,
    options = [],
    placeholder,
    disabled,
    className,
    classNames,
    style,
    size = 'middle',
    mode,
    allowClear,
    notFoundContent,
    open,
    onOpenChange,
    suffixIcon,
    variant = 'outlined',
    optionRender,
  } = props

  if (mode === 'tags') return <TagsSelect {...props} />

  const multiple = mode === 'multiple'
  const resolvedValue = multiple
    ? (Array.isArray(value) ? value : [])
    : (Array.isArray(value) ? value[0] : value ?? null)

  return (
    <BaseSelect.Root<any, any>
      value={resolvedValue as any}
      defaultValue={defaultValue as any}
      onValueChange={(next) => {
        if (multiple) {
          onChange?.(next ?? [], options.filter((option) => (next ?? []).includes(option.value)))
        } else {
          onChange?.(next ?? undefined, options.find((option) => Object.is(option.value, next)))
        }
      }}
      items={options}
      disabled={disabled}
      multiple={multiple as any}
      modal={false}
      open={open}
      onOpenChange={(nextOpen) => onOpenChange?.(nextOpen)}
    >
      <BaseSelect.Trigger
        className={[
          'purr-select',
          multiple && 'purr-multi-select',
          `purr-select--${size}`,
          variant === 'borderless' && 'purr-select--borderless',
          className,
        ].filter(Boolean).join(' ')}
        style={style}
      >
        <span className="purr-select__selector">
          <BaseSelect.Value className="purr-select__value" placeholder={placeholder}>
            {multiple ? (selected) => {
              const selectedValues = Array.isArray(selected) ? selected : []
              if (!selectedValues.length) return placeholder
              return options
                .filter((option) => selectedValues.includes(option.value))
                .map((option) => option.label)
                .reduce<React.ReactNode[]>((nodes, label, index) => [...nodes, index ? '、' : null, label], [])
            } : undefined}
          </BaseSelect.Value>
          {allowClear && (multiple ? Array.isArray(resolvedValue) && resolvedValue.length > 0 : resolvedValue != null) && (
            <span
              className="purr-select__clear"
              role="button"
              tabIndex={0}
              aria-label="清空"
              onPointerDown={(event) => { event.preventDefault(); event.stopPropagation() }}
              onClick={(event) => {
                event.stopPropagation()
                onChange?.(multiple ? [] : undefined)
              }}
            >
              ×
            </span>
          )}
          {suffixIcon !== null && (
            <BaseSelect.Icon className="purr-select__icon">
              {suffixIcon ?? <SelectChevron />}
            </BaseSelect.Icon>
          )}
        </span>
      </BaseSelect.Trigger>
      <BaseSelect.Portal>
        <BaseSelect.Positioner
          className="purr-select__positioner"
          side="bottom"
          align="start"
          sideOffset={4}
          alignItemWithTrigger={false}
        >
          <BaseSelect.Popup className={['purr-select__popup', classNames?.popup?.root].filter(Boolean).join(' ')}>
            <BaseSelect.List className="purr-select__list">
              {options.length ? options.map((option) => (
                <BaseSelect.Item key={option.value} value={option.value} disabled={option.disabled} className="purr-select__item">
                  <BaseSelect.ItemText>
                    {optionRender?.({ value: option.value, label: option.label, data: option }) ?? option.label}
                  </BaseSelect.ItemText>
                  <BaseSelect.ItemIndicator className="purr-select__check">✓</BaseSelect.ItemIndicator>
                </BaseSelect.Item>
              )) : <div className="purr-select__empty">{notFoundContent ?? '暂无数据'}</div>}
            </BaseSelect.List>
          </BaseSelect.Popup>
        </BaseSelect.Positioner>
      </BaseSelect.Portal>
    </BaseSelect.Root>
  )
}
