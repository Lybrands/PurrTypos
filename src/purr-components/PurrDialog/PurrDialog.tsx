import React from 'react'
import { Dialog as BaseDialog } from '@base-ui/react/dialog'
import '../styles/dialog.scss'

export interface PurrDialogProps {
  open: boolean
  title: React.ReactNode
  children: React.ReactNode
  footer?: React.ReactNode
  width?: number | string
  onOpenChange: (open: boolean) => void
  className?: string
  closable?: boolean
  styles?: {
    container?: React.CSSProperties
    header?: React.CSSProperties
    body?: React.CSSProperties
    footer?: React.CSSProperties
  }
}

/**
 * 项目内唯一的模态框入口。行为由 Base UI 提供，视觉和 API 归项目维护。
 */
export function PurrDialog({
  open,
  title,
  children,
  footer,
  width = 520,
  onOpenChange,
  className,
  closable = true,
  styles,
}: PurrDialogProps) {
  return (
    <BaseDialog.Root open={open} onOpenChange={onOpenChange}>
      <BaseDialog.Portal>
        <BaseDialog.Backdrop className="purr-dialog-backdrop" />
        <BaseDialog.Viewport className="purr-dialog-viewport">
          <BaseDialog.Popup
            className={['purr-dialog', className].filter(Boolean).join(' ')}
            style={{
              '--purr-dialog-width': typeof width === 'number' ? `${width}px` : width,
              ...styles?.container,
            } as React.CSSProperties}
          >
            <div className="purr-dialog__header" style={styles?.header}>
              <BaseDialog.Title className="purr-dialog__title">{title}</BaseDialog.Title>
              {closable && <BaseDialog.Close className="purr-dialog__close" aria-label="关闭">×</BaseDialog.Close>}
            </div>
            <div className="purr-dialog__body" style={styles?.body}>{children}</div>
            {footer && <div className="purr-dialog__footer" style={styles?.footer}>{footer}</div>}
          </BaseDialog.Popup>
        </BaseDialog.Viewport>
      </BaseDialog.Portal>
    </BaseDialog.Root>
  )
}
