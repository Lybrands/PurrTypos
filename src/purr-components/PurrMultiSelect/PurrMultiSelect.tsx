import React from 'react'
import { Select as BaseSelect } from '@base-ui/react/select'
import type { PurrSelectOption } from '../PurrSelect'
import { PurrTooltip } from '../PurrTooltip'
import { ChevronDownIcon, SearchIcon } from '../icons'
import '../styles/purr.scss'

export interface PurrMultiSelectProps<T extends string | number> {
  value: T[]
  onChange: (value: T[]) => void
  options: PurrSelectOption<T>[]
  placeholder?: React.ReactNode
  disabled?: boolean
  className?: string
  size?: 'small' | 'middle' | 'large'
  allowClear?: boolean
  searchable?: boolean
  searchPlaceholder?: string
  notFoundContent?: React.ReactNode
  maxVisibleValues?: number
  renderValue?: (
    value: T[],
    selectedOptions: PurrSelectOption<T>[],
  ) => React.ReactNode
  optionRender?: (option: PurrSelectOption<T>) => React.ReactNode
}

/** 基础多选控件，支持在有限选项中搜索、筛选和批量关联。 */
export function PurrMultiSelect<T extends string | number>({
  value,
  onChange,
  options,
  placeholder,
  disabled,
  className,
  size = 'middle',
  allowClear,
  searchable,
  searchPlaceholder = '搜索选项',
  notFoundContent = '没有匹配的内容',
  maxVisibleValues = 1,
  renderValue,
  optionRender,
}: PurrMultiSelectProps<T>) {
  const [search, setSearch] = React.useState('')
  const normalizedSearch = search.trim().toLocaleLowerCase()
  const filteredOptions = React.useMemo(() => {
    if (!searchable || !normalizedSearch) return options
    return options.filter((option) => {
      const searchText = option.searchText
      const labelText = typeof option.label === 'string' || typeof option.label === 'number'
        ? String(option.label)
        : ''
      return String(searchText ?? labelText).toLocaleLowerCase().includes(normalizedSearch)
    })
  }, [normalizedSearch, options, searchable])

  return (
    <BaseSelect.Root<T, true>
      value={value}
      onValueChange={(next) => onChange(next ?? [])}
      onOpenChange={(open) => {
        if (!open) setSearch('')
      }}
      items={options}
      disabled={disabled}
      multiple
      modal={false}
    >
      <BaseSelect.Trigger className={['purr-select', 'purr-multi-select', `purr-select--${size}`, className].filter(Boolean).join(' ')}>
        <BaseSelect.Value className="purr-select__value" placeholder={placeholder}>
          {(selected) => {
            const selectedValues = Array.isArray(selected) ? selected : []
            if (!selectedValues.length) return placeholder
            const selectedOptions = options.filter((option) => (
              selectedValues.includes(option.value)
            ))
            if (renderValue) return renderValue(selectedValues, selectedOptions)
            const visibleOptions = selectedOptions.slice(
              0,
              Math.max(1, maxVisibleValues),
            )
            const hiddenOptions = selectedOptions.slice(visibleOptions.length)
            const hiddenCount = hiddenOptions.length
            return (
              <span className="purr-multi-select__selection">
                {visibleOptions.map((option) => (
                  <span
                    key={option.value}
                    className="purr-multi-select__selected-value"
                    title={typeof option.label === 'string'
                      ? option.label
                      : undefined}
                  >
                    {option.label}
                  </span>
                ))}
                {hiddenCount > 0 && (
                  <PurrTooltip
                    placement="top"
                    mouseEnterDelay={0.2}
                    title={(
                      <span className="purr-multi-select__hidden-values">
                        <strong>其余已选内容</strong>
                        <span>
                          {hiddenOptions.map((option) => (
                            <span key={option.value}>{option.label}</span>
                          ))}
                        </span>
                      </span>
                    )}
                  >
                    <span
                      className="purr-multi-select__rest"
                      tabIndex={0}
                      aria-label={`另有 ${hiddenCount} 项，悬停查看`}
                    >
                      另有 {hiddenCount} 项
                    </span>
                  </PurrTooltip>
                )}
              </span>
            )
          }}
        </BaseSelect.Value>
        {allowClear && value.length > 0 && (
          <span className="purr-select__clear" role="button" tabIndex={0} aria-label="清空" onPointerDown={(event) => { event.preventDefault(); event.stopPropagation() }} onClick={(event) => { event.stopPropagation(); onChange([]) }}>×</span>
        )}
        <BaseSelect.Icon className="purr-select__icon"><ChevronDownIcon /></BaseSelect.Icon>
      </BaseSelect.Trigger>
      <BaseSelect.Portal>
        <BaseSelect.Positioner side="bottom" align="start" sideOffset={4}>
          <BaseSelect.Popup className={[
            'purr-select__popup',
            searchable && 'purr-select__popup--searchable',
          ].filter(Boolean).join(' ')}>
            {searchable && (
              <label
                className="purr-select__search"
                onPointerDown={(event) => event.stopPropagation()}
              >
                <SearchIcon />
                <input
                  value={search}
                  autoFocus
                  placeholder={searchPlaceholder}
                  aria-label={searchPlaceholder}
                  onChange={(event) => setSearch(event.target.value)}
                  onKeyDown={(event) => event.stopPropagation()}
                />
              </label>
            )}
            <BaseSelect.List className="purr-select__list">
              {filteredOptions.map((option) => (
                <BaseSelect.Item
                  key={option.value}
                  value={option.value}
                  disabled={option.disabled}
                  className="purr-select__item"
                >
                  <BaseSelect.ItemText>
                    {optionRender?.(option) ?? option.label}
                  </BaseSelect.ItemText>
                  <BaseSelect.ItemIndicator className="purr-select__check">✓</BaseSelect.ItemIndicator>
                </BaseSelect.Item>
              ))}
              {filteredOptions.length === 0 && (
                <div className="purr-select__empty">{notFoundContent}</div>
              )}
            </BaseSelect.List>
          </BaseSelect.Popup>
        </BaseSelect.Positioner>
      </BaseSelect.Portal>
    </BaseSelect.Root>
  )
}
