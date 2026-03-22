import React from 'react'
import { Modal, Checkbox } from 'antd'
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
    <Modal
      title={title}
      open
      onOk={() => onConfirm(checked)}
      onCancel={onCancel}
      okText="确认删除"
      okButtonProps={{ danger: true }}
      cancelText="取消"
      destroyOnHidden
    >
      <p className="confirm-modal-message">{message}</p>
      {checkboxLabel && (
        <div className="confirm-modal-checkbox">
          <Checkbox checked={checked} onChange={(e) => setChecked(e.target.checked)}>
            {checkboxLabel}
          </Checkbox>
        </div>
      )}
    </Modal>
  )
}
