import React from 'react'
import { SearchIcon } from '../icons'
import '../styles/purr.scss'

export interface PurrInputRef {
  input: HTMLInputElement | null
  nativeElement: HTMLInputElement | null
  focus: (options?: FocusOptions) => void
  blur: () => void
  select: () => void
}

export interface PurrTextAreaRef {
  resizableTextArea?: { textArea: HTMLTextAreaElement | null }
  nativeElement: HTMLTextAreaElement | null
  focus: (options?: FocusOptions) => void
  blur: () => void
}

export interface PurrInputProps extends Omit<React.InputHTMLAttributes<HTMLInputElement>, 'size' | 'prefix'> {
  size?: 'small' | 'middle' | 'large'
  prefix?: React.ReactNode
  suffix?: React.ReactNode
  allowClear?: boolean
  variant?: 'outlined' | 'borderless'
  onPressEnter?: (event: React.KeyboardEvent<HTMLInputElement>) => void
}

const BasicInput = React.forwardRef<PurrInputRef, PurrInputProps>(function BasicInput(
  {
    size = 'middle',
    prefix,
    suffix,
    allowClear,
    variant = 'outlined',
    onPressEnter,
    onKeyDown,
    className,
    value,
    defaultValue,
    onChange,
    disabled,
    ...props
  },
  ref,
) {
  const inputRef = React.useRef<HTMLInputElement>(null)
  React.useImperativeHandle(ref, () => ({
    input: inputRef.current,
    nativeElement: inputRef.current,
    focus: (options) => inputRef.current?.focus(options),
    blur: () => inputRef.current?.blur(),
    select: () => inputRef.current?.select(),
  }))

  const input = (
    <input
      {...props}
      ref={inputRef}
      value={value}
      defaultValue={defaultValue}
      disabled={disabled}
      className={[
        'purr-input',
        `purr-input--${size}`,
        variant === 'borderless' && 'purr-input--borderless',
        className,
      ].filter(Boolean).join(' ')}
      onChange={onChange}
      onKeyDown={(event) => {
        onKeyDown?.(event)
        if (event.key === 'Enter' && !event.defaultPrevented) onPressEnter?.(event)
      }}
    />
  )

  if (!prefix && !suffix && !allowClear) return input

  const canClear = allowClear && !disabled && value != null && String(value).length > 0
  return (
    <span className={['purr-input-affix', `purr-input-affix--${size}`, className].filter(Boolean).join(' ')}>
      {prefix && <span className="purr-input__prefix">{prefix}</span>}
      {React.cloneElement(input, { className: 'purr-input purr-input--embedded' })}
      {canClear && (
        <button
          type="button"
          className="purr-input__clear"
          aria-label="清空"
          onClick={() => {
            const element = inputRef.current
            if (!element) return
            const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set
            setter?.call(element, '')
            element.dispatchEvent(new Event('input', { bubbles: true }))
            element.focus()
          }}
        >
          ×
        </button>
      )}
      {suffix && <span className="purr-input__suffix">{suffix}</span>}
    </span>
  )
})

export interface PurrTextAreaProps extends Omit<React.TextareaHTMLAttributes<HTMLTextAreaElement>, 'size'> {
  autoSize?: boolean | { minRows?: number; maxRows?: number }
  size?: 'small' | 'middle' | 'large'
  onPressEnter?: (event: React.KeyboardEvent<HTMLTextAreaElement>) => void
}

const TextArea = React.forwardRef<PurrTextAreaRef, PurrTextAreaProps>(function TextArea(
  { autoSize, size = 'middle', onPressEnter, onKeyDown, className, rows, value, defaultValue, ...props },
  ref,
) {
  const textAreaRef = React.useRef<HTMLTextAreaElement>(null)
  const minRows = typeof autoSize === 'object' ? autoSize.minRows : undefined
  const maxRows = typeof autoSize === 'object' ? autoSize.maxRows : undefined

  const resize = React.useCallback(() => {
    const element = textAreaRef.current
    if (!element || !autoSize) return
    const lineHeight = Number.parseFloat(getComputedStyle(element).lineHeight) || 22
    element.style.height = 'auto'
    const minHeight = (minRows ?? 1) * lineHeight
    const maxHeight = (maxRows ?? Number.POSITIVE_INFINITY) * lineHeight
    element.style.height = `${Math.max(minHeight, Math.min(element.scrollHeight, maxHeight))}px`
  }, [autoSize, maxRows, minRows])

  React.useLayoutEffect(resize, [resize, value])
  React.useImperativeHandle(ref, () => ({
    resizableTextArea: { textArea: textAreaRef.current },
    nativeElement: textAreaRef.current,
    focus: (options) => textAreaRef.current?.focus(options),
    blur: () => textAreaRef.current?.blur(),
  }))

  return (
    <textarea
      {...props}
      ref={textAreaRef}
      rows={rows ?? minRows}
      value={value}
      defaultValue={defaultValue}
      className={['purr-textarea', 'purr-input', `purr-input--${size}`, className].filter(Boolean).join(' ')}
      onInput={resize}
      onKeyDown={(event) => {
        onKeyDown?.(event)
        if (event.key === 'Enter' && !event.defaultPrevented) onPressEnter?.(event)
      }}
    />
  )
})

const Password = React.forwardRef<PurrInputRef, PurrInputProps>(function Password(props, ref) {
  return <BasicInput {...props} ref={ref} type="password" />
})

export interface PurrSearchProps extends PurrInputProps {
  onSearch?: (value: string) => void
  enterButton?: React.ReactNode
  loading?: boolean
}

const Search = React.forwardRef<PurrInputRef, PurrSearchProps>(function Search(
  { onSearch, enterButton, loading, onPressEnter, suffix, className, ...props },
  ref,
) {
  return (
    <BasicInput
      {...props}
      ref={ref}
      className={['purr-input-search', className].filter(Boolean).join(' ')}
      suffix={suffix ?? (enterButton ? (
        <button
          type="button"
          className="purr-input-search__button"
          disabled={loading}
          onClick={(event) => onSearch?.((event.currentTarget.closest('.purr-input-affix')?.querySelector('input') as HTMLInputElement | null)?.value ?? '')}
        >
          {loading ? '…' : enterButton === true ? '搜索' : enterButton}
        </button>
      ) : <SearchIcon />)}
      onPressEnter={(event) => {
        onPressEnter?.(event)
        if (!event.defaultPrevented) onSearch?.(event.currentTarget.value)
      }}
    />
  )
})

type InputComponent = typeof BasicInput & {
  TextArea: typeof TextArea
  Password: typeof Password
  Search: typeof Search
}

export const PurrInput = BasicInput as InputComponent
PurrInput.TextArea = TextArea
PurrInput.Password = Password
PurrInput.Search = Search
