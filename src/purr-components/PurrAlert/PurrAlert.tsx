import React from 'react'
import '../styles/purr.scss'

export interface PurrAlertProps {
  message?: React.ReactNode
  description?: React.ReactNode
  type?: 'success' | 'info' | 'warning' | 'error'
  showIcon?: boolean
  closable?: boolean
  onClose?: () => void
  className?: string
}

export function PurrAlert({
  message,
  description,
  type = 'info',
  showIcon,
  closable,
  onClose,
  className,
}: PurrAlertProps) {
  const [visible, setVisible] = React.useState(true)
  if (!visible) return null
  return (
    <div role="alert" className={['purr-alert', `purr-alert--${type}`, className].filter(Boolean).join(' ')}>
      {showIcon && (
        <span className="purr-alert__icon" aria-hidden>
          {type === 'success' ? '✓' : type === 'info' ? 'i' : '!'}
        </span>
      )}
      <div>
        {message != null && <div className="purr-alert__message">{message}</div>}
        {description != null && <div className="purr-alert__description">{description}</div>}
      </div>
      {closable && (
        <button
          type="button"
          className="purr-alert__close"
          aria-label="关闭"
          onClick={() => {
            setVisible(false)
            onClose?.()
          }}
        >
          ×
        </button>
      )}
    </div>
  )
}
