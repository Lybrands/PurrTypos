import React from 'react'
import { Popover } from './Popover'
import { Button } from './Button'
import './ui.scss'

export function Tag({
  children,
  color = 'default',
  className,
  ...props
}: {
  children?: React.ReactNode
  color?: string
  variant?: string
  className?: string
} & Omit<React.HTMLAttributes<HTMLSpanElement>, 'color'>) {
  return <span {...props} className={['purr-tag', `purr-tag--${color}`, className].filter(Boolean).join(' ')}>{children}</span>
}

export function Space({
  children,
  size = 'small',
  wrap,
  className,
  style,
}: {
  children?: React.ReactNode
  size?: 'small' | 'middle' | 'large' | number
  wrap?: boolean
  className?: string
  style?: React.CSSProperties
}) {
  const gap = typeof size === 'number' ? size : size === 'large' ? 16 : size === 'middle' ? 12 : 8
  return (
    <div
      className={['purr-space', wrap && 'purr-space--wrap', className].filter(Boolean).join(' ')}
      style={{ gap, ...style }}
    >
      {React.Children.map(children, (child) => child == null ? null : <div className="purr-space__item">{child}</div>)}
    </div>
  )
}

export function Card({
  children,
  className,
  size = 'default',
}: {
  children?: React.ReactNode
  className?: string
  size?: 'default' | 'small'
}) {
  return <div className={['purr-card', `purr-card--${size}`, className].filter(Boolean).join(' ')}><div className="purr-card__body">{children}</div></div>
}

interface ListProps<T> {
  dataSource?: T[]
  renderItem?: (item: T, index: number) => React.ReactNode
  loading?: boolean
  locale?: { emptyText?: React.ReactNode }
  children?: React.ReactNode
  className?: string
  size?: 'small' | 'default' | 'large'
}

function ListBase<T>({ dataSource, renderItem, loading, locale, children, className }: ListProps<T>) {
  return (
    <div className={['purr-list', className].filter(Boolean).join(' ')}>
      {loading
        ? <div className="purr-list__empty">加载中…</div>
        : dataSource?.length
          ? dataSource.map((item, index) => <React.Fragment key={index}>{renderItem?.(item, index)}</React.Fragment>)
          : children ?? <div className="purr-list__empty">{locale?.emptyText ?? '暂无数据'}</div>}
    </div>
  )
}

function ListItem({
  children,
  actions,
  className,
  ...props
}: {
  children?: React.ReactNode
  actions?: React.ReactNode[]
  className?: string
} & React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div {...props} className={['purr-list__item', className].filter(Boolean).join(' ')}>
      <div className="purr-list__item-content">{children}</div>
      {actions?.length ? <div className="purr-list__actions">{actions.map((action, index) => <div key={index}>{action}</div>)}</div> : null}
    </div>
  )
}

function ListItemMeta({ title, description }: { title?: React.ReactNode; description?: React.ReactNode }) {
  return (
    <div className="purr-list__meta">
      {title != null && <div className="purr-list__meta-title">{title}</div>}
      {description != null && <div className="purr-list__meta-description">{description}</div>}
    </div>
  )
}

type ListComponent = typeof ListBase & {
  Item: typeof ListItem & { Meta: typeof ListItemMeta }
}

export const List = ListBase as ListComponent
List.Item = ListItem as typeof ListItem & { Meta: typeof ListItemMeta }
List.Item.Meta = ListItemMeta

export function Divider({ style, className }: { style?: React.CSSProperties; className?: string }) {
  return <div role="separator" className={['purr-divider', className].filter(Boolean).join(' ')} style={style} />
}

export function Alert({
  message,
  description,
  type = 'info',
  showIcon,
  closable,
  onClose,
  className,
}: {
  message?: React.ReactNode
  description?: React.ReactNode
  type?: 'success' | 'info' | 'warning' | 'error'
  showIcon?: boolean
  closable?: boolean
  onClose?: () => void
  className?: string
}) {
  const [visible, setVisible] = React.useState(true)
  if (!visible) return null
  return (
    <div role="alert" className={['purr-alert', `purr-alert--${type}`, className].filter(Boolean).join(' ')}>
      {showIcon && <span className="purr-alert__icon" aria-hidden>{type === 'success' ? '✓' : type === 'error' ? '!' : type === 'warning' ? '!' : 'i'}</span>}
      <div>
        {message != null && <div className="purr-alert__message">{message}</div>}
        {description != null && <div className="purr-alert__description">{description}</div>}
      </div>
      {closable && <button type="button" className="purr-alert__close" aria-label="关闭" onClick={() => { setVisible(false); onClose?.() }}>×</button>}
    </div>
  )
}

export function Segmented<T extends string | number>({
  value,
  options,
  onChange,
  size = 'middle',
  block,
  className,
}: {
  value?: T
  options: Array<T | { label: React.ReactNode; value: T; disabled?: boolean }>
  onChange?: (value: T) => void
  size?: 'small' | 'middle' | 'large'
  block?: boolean
  className?: string
}) {
  return (
    <div className={['purr-segmented', `purr-segmented--${size}`, block && 'purr-segmented--block', className].filter(Boolean).join(' ')}>
      {options.map((raw) => {
        const option = typeof raw === 'object' ? raw : { label: raw, value: raw }
        return (
          <button
            type="button"
            key={option.value}
            disabled={option.disabled}
            className={['purr-segmented__item', option.value === value && 'is-selected'].filter(Boolean).join(' ')}
            onClick={() => onChange?.(option.value)}
          >
            {option.label}
          </button>
        )
      })}
    </div>
  )
}

export function InputNumber({
  value,
  onChange,
  min,
  max,
  step,
  size = 'middle',
  className,
  placeholder,
}: {
  value?: number | null
  onChange?: (value: number | null) => void
  min?: number
  max?: number
  step?: number
  size?: 'small' | 'middle' | 'large'
  className?: string
  placeholder?: string
}) {
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

export function Progress({
  percent = 0,
  status,
  size,
  showInfo = true,
  strokeColor,
  format,
}: {
  percent?: number
  status?: 'success' | 'exception' | 'normal' | 'active'
  size?: 'small' | 'default' | number | [number, number]
  showInfo?: boolean
  strokeColor?: string
  format?: (percent?: number) => React.ReactNode
}) {
  const height = Array.isArray(size) ? size[1] : typeof size === 'number' ? size : size === 'small' ? 6 : 8
  return (
    <div className={['purr-progress', status && `purr-progress--${status}`].filter(Boolean).join(' ')}>
      <div className="purr-progress__track" style={{ height }}>
        <span className="purr-progress__bar" style={{ width: `${Math.max(0, Math.min(100, percent))}%`, background: strokeColor }} />
      </div>
      {showInfo && <span className="purr-progress__text">{format ? format(percent) : `${Math.round(percent)}%`}</span>}
    </div>
  )
}

export function Switch({
  checked,
  defaultChecked,
  onChange,
  disabled,
  loading,
  size = 'default',
  className,
}: {
  checked?: boolean
  defaultChecked?: boolean
  onChange?: (checked: boolean) => void
  disabled?: boolean
  loading?: boolean
  size?: 'small' | 'default'
  className?: string
}) {
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

export function Slider({
  value,
  defaultValue,
  onChange,
  min = 0,
  max = 100,
  step = 1,
  disabled,
  className,
}: {
  value?: number
  defaultValue?: number
  onChange?: (value: number) => void
  min?: number
  max?: number
  step?: number
  disabled?: boolean
  tooltip?: { formatter?: (value?: number) => React.ReactNode }
  className?: string
}) {
  return (
    <input
      type="range"
      className={['purr-slider', className].filter(Boolean).join(' ')}
      value={value}
      defaultValue={defaultValue}
      min={min}
      max={max}
      step={step}
      disabled={disabled}
      onChange={(event) => onChange?.(Number(event.target.value))}
    />
  )
}

export function Popconfirm({
  title,
  description,
  children,
  onConfirm,
  onCancel,
  okText = '确定',
  cancelText = '取消',
  placement = 'top',
  disabled,
  okButtonProps,
}: {
  title: React.ReactNode
  description?: React.ReactNode
  children: React.ReactElement
  onConfirm?: () => void | Promise<void>
  onCancel?: () => void
  okText?: React.ReactNode
  cancelText?: React.ReactNode
  placement?: React.ComponentProps<typeof Popover>['placement']
  disabled?: boolean
  okButtonProps?: React.ComponentProps<typeof Button>
}) {
  const [open, setOpen] = React.useState(false)
  return (
    <Popover
      open={open}
      onOpenChange={setOpen}
      placement={placement}
      disabled={disabled}
      content={(
        <div className="purr-popconfirm">
          <div className="purr-popconfirm__title">{title}</div>
          {description != null && <div className="purr-popconfirm__description">{description}</div>}
          <div className="purr-popconfirm__actions">
            <Button size="small" onClick={() => { setOpen(false); onCancel?.() }}>{cancelText}</Button>
            <Button size="small" type="primary" danger {...okButtonProps} onClick={() => { setOpen(false); void onConfirm?.() }}>{okText}</Button>
          </div>
        </div>
      )}
    >
      {children}
    </Popover>
  )
}

export const Typography = {
  Link: React.forwardRef<HTMLAnchorElement, React.AnchorHTMLAttributes<HTMLAnchorElement> & { ellipsis?: boolean }>(function TypographyLink({ ellipsis, ...props }, ref) {
    return <a {...props} ref={ref} className={['purr-typography-link', ellipsis && 'purr-typography-link--ellipsis', props.className].filter(Boolean).join(' ')} />
  }),
}
