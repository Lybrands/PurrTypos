import React from 'react'
import { PurrButton } from '../PurrButton'
import { PurrPopover } from '../PurrPopover'
import '../styles/purr.scss'

export interface PurrPopconfirmProps {
  title: React.ReactNode
  description?: React.ReactNode
  children: React.ReactElement
  onConfirm?: () => void | Promise<void>
  onCancel?: () => void
  okText?: React.ReactNode
  cancelText?: React.ReactNode
  placement?: React.ComponentProps<typeof PurrPopover>['placement']
  disabled?: boolean
  okButtonProps?: React.ComponentProps<typeof PurrButton>
  zIndex?: number
}

export function PurrPopconfirm({
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
  zIndex,
}: PurrPopconfirmProps) {
  const [open, setOpen] = React.useState(false)
  return (
    <PurrPopover
      zIndex={zIndex}
      open={open}
      onOpenChange={setOpen}
      placement={placement}
      disabled={disabled}
      content={(
        <div className="purr-popconfirm">
          <div className="purr-popconfirm__title">{title}</div>
          {description != null && <div className="purr-popconfirm__description">{description}</div>}
          <div className="purr-popconfirm__actions">
            <PurrButton size="small" onClick={() => { setOpen(false); onCancel?.() }}>
              {cancelText}
            </PurrButton>
            <PurrButton
              size="small"
              type="primary"
              danger
              {...okButtonProps}
              onClick={() => {
                setOpen(false)
                void onConfirm?.()
              }}
            >
              {okText}
            </PurrButton>
          </div>
        </div>
      )}
    >
      {children}
    </PurrPopover>
  )
}
