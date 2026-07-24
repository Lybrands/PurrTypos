import React from 'react'
import { Dialog as BaseDialog } from '@base-ui/react/dialog'
import './Dialog/Dialog.scss'

export interface DrawerProps {
  open: boolean
  title: React.ReactNode
  children?: React.ReactNode
  onClose?: () => void
  placement?: 'left' | 'right'
  width?: number | string
  destroyOnHidden?: boolean
  closable?: boolean
  className?: string
  rootClassName?: string
}

/** 从视口侧边进入的非阻塞抽屉，焦点管理和退出行为由 Base UI 提供。 */
export function Drawer({
  open,
  title,
  children,
  onClose,
  placement = 'right',
  width = 520,
  destroyOnHidden,
  closable = true,
  className,
  rootClassName,
}: DrawerProps) {
  if (destroyOnHidden && !open) return null

  return (
    <BaseDialog.Root open={open} onOpenChange={(nextOpen) => { if (!nextOpen) onClose?.() }}>
      <BaseDialog.Portal>
        <BaseDialog.Backdrop className="purr-dialog-backdrop purr-drawer-backdrop" />
        <BaseDialog.Viewport className="purr-drawer-viewport">
          <BaseDialog.Popup
            className={['purr-drawer', `purr-drawer--${placement}`, className, rootClassName].filter(Boolean).join(' ')}
            style={{ '--purr-drawer-width': typeof width === 'number' ? `${width}px` : width } as React.CSSProperties}
          >
            <div className="purr-drawer__header">
              <BaseDialog.Title className="purr-drawer__title">{title}</BaseDialog.Title>
              {closable && <BaseDialog.Close className="purr-dialog__close" aria-label="关闭">×</BaseDialog.Close>}
            </div>
            <div className="purr-drawer__body">{children}</div>
          </BaseDialog.Popup>
        </BaseDialog.Viewport>
      </BaseDialog.Portal>
    </BaseDialog.Root>
  )
}
