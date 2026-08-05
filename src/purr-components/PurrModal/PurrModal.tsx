import React from 'react'
import { PurrButton, type PurrButtonProps } from '../PurrButton'
import { PurrDialog } from '../PurrDialog'

export interface PurrModalProps {
  open: boolean
  title: React.ReactNode
  children?: React.ReactNode
  onCancel?: () => void
  onOk?: () => void | Promise<void>
  footer?: React.ReactNode
  okText?: React.ReactNode
  cancelText?: React.ReactNode
  confirmLoading?: boolean
  okButtonProps?: PurrButtonProps
  cancelButtonProps?: PurrButtonProps
  width?: number | string
  destroyOnHidden?: boolean
  closable?: boolean
  maskClosable?: boolean
  className?: string
  rootClassName?: string
  styles?: {
    container?: React.CSSProperties
    header?: React.CSSProperties
    body?: React.CSSProperties
    footer?: React.CSSProperties
  }
}

/** 业务模态框兼容层。行为基于 Base UI PurrDialog，API 覆盖项目现有用法。 */
export function PurrModal({
  open,
  title,
  children,
  onCancel,
  onOk,
  footer,
  okText = '确定',
  cancelText = '取消',
  confirmLoading = false,
  okButtonProps,
  cancelButtonProps,
  width,
  destroyOnHidden,
  closable = true,
  className,
  rootClassName,
  styles,
}: PurrModalProps) {
  if (destroyOnHidden && !open) return null

  const resolvedFooter = footer === null
    ? undefined
    : footer ?? (
      <>
        <PurrButton {...cancelButtonProps} onClick={onCancel}>{cancelText}</PurrButton>
        <PurrButton type="primary" {...okButtonProps} loading={confirmLoading} onClick={() => void onOk?.()}>
          {okText}
        </PurrButton>
      </>
    )

  return (
    <PurrDialog
      open={open}
      title={title}
      width={width}
      closable={closable}
      className={[className, rootClassName].filter(Boolean).join(' ')}
      styles={styles}
      onOpenChange={(nextOpen) => {
        if (!nextOpen) onCancel?.()
      }}
      footer={resolvedFooter}
    >
      {children}
    </PurrDialog>
  )
}
