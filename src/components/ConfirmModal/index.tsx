import React from 'react'
import { Button, Checkbox, Dialog } from '../../ui'
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
    <Dialog
      title={title}
      open
      onOpenChange={(open) => { if (!open) onCancel() }}
      footer={(
        <>
          <Button onClick={onCancel}>取消</Button>
          <Button variant="danger" onClick={() => onConfirm(checked)}>确认删除</Button>
        </>
      )}
    >
      <p className="confirm-modal-message">{message}</p>
      {checkboxLabel && (
        <div className="confirm-modal-checkbox">
          <Checkbox checked={checked} onChange={(event) => setChecked(event.target.checked)}>
            {checkboxLabel}
          </Checkbox>
        </div>
      )}
    </Dialog>
  )
}
