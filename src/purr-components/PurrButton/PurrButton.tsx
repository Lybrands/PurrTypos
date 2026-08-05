import React from 'react'
import '../styles/purr.scss'

type ButtonVisualType = 'default' | 'primary' | 'dashed' | 'text' | 'link'

export interface PurrButtonProps extends Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, 'type'> {
  /** 常用视觉类型；项目内新代码也可使用语义更明确的 variant。 */
  type?: ButtonVisualType | 'button' | 'submit' | 'reset'
  htmlType?: 'button' | 'submit' | 'reset'
  variant?: 'default' | 'primary' | 'danger' | 'text' | 'link'
  size?: 'small' | 'middle' | 'large'
  loading?: boolean | { delay?: number }
  danger?: boolean
  icon?: React.ReactNode
  iconPosition?: 'start' | 'end'
  shape?: 'default' | 'circle' | 'round'
  block?: boolean
  ghost?: boolean
}

export const PurrButton = React.forwardRef<HTMLButtonElement, PurrButtonProps>(function PurrButton(
  {
    variant,
    type = 'default',
    htmlType,
    size = 'middle',
    loading = false,
    danger = false,
    icon,
    iconPosition = 'start',
    shape = 'default',
    block = false,
    ghost = false,
    className,
    disabled,
    children,
    ...props
  },
  ref,
) {
  const visualType: ButtonVisualType =
    type === 'button' || type === 'submit' || type === 'reset' ? 'default' : type
  const nativeType = htmlType ?? (type === 'button' || type === 'submit' || type === 'reset' ? type : 'button')
  const resolvedVariant = variant ?? (danger ? 'danger' : visualType === 'dashed' ? 'default' : visualType)
  const isLoading = Boolean(loading)
  const isIconOnly = Boolean(icon && children == null)

  return (
    <button
      {...props}
      ref={ref}
      type={nativeType}
      disabled={disabled || isLoading}
      aria-busy={isLoading || undefined}
      className={[
        'purr-button',
        `purr-button--${resolvedVariant}`,
        `purr-button--${size}`,
        `purr-button--${shape}`,
        visualType === 'dashed' && 'purr-button--dashed',
        ghost && 'purr-button--ghost',
        block && 'purr-button--block',
        isIconOnly && 'purr-button--icon-only',
        isLoading && 'purr-button--loading',
        (disabled || isLoading) && 'purr-button--disabled',
        className,
      ].filter(Boolean).join(' ')}
    >
      {isLoading && <span className="purr-button__spinner" aria-hidden />}
      {icon && iconPosition === 'start' && <span className="purr-button__icon">{icon}</span>}
      {children != null && <span className="purr-button__label">{children}</span>}
      {icon && iconPosition === 'end' && <span className="purr-button__icon">{icon}</span>}
    </button>
  )
})
