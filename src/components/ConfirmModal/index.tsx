import React from 'react'
import { PurrButton, PurrCheckbox, PurrDialog } from '@/purr-components'
import './index.scss'

interface ConfirmModalProps {
  title: string
  message: string
  checkboxLabel?: string
  onConfirm: (checked: boolean) => void
  onCancel: () => void
}

export default function ConfirmModal({
  title,
  message,
  checkboxLabel,
  onConfirm,
  onCancel,
}: ConfirmModalProps) {
  const [checked, setChecked] = React.useState(false)

  return (
    <PurrDialog
      title={title}
      open
      onOpenChange={(open) => { if (!open) onCancel() }}
      footer={(
        <>
          <PurrButton onClick={onCancel}>取消</PurrButton>
          <PurrButton variant="danger" onClick={() => onConfirm(checked)}>确认删除</PurrButton>
        </>
      )}
    >
      <p className="confirm-modal-message">{message}</p>
      {checkboxLabel && (
        <div className="confirm-modal-checkbox">
          <PurrCheckbox checked={checked} onChange={(event) => setChecked(event.target.checked)}>
            {checkboxLabel}
          </PurrCheckbox>
        </div>
      )}
    </PurrDialog>
  )
}
