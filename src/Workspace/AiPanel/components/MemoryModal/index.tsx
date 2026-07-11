import React from 'react'
import { Button, Modal, Tabs } from 'antd'
import ManageTab from './ManageTab'
import SelectionTab from './SelectionTab'
import type { MemoryModalProps } from './types'
import { useMemoryModal } from './useMemoryModal'
import './index.scss'

export type { MemoryModalProps } from './types'

export default function MemoryModal({
  open,
  onCancel,
  bookId,
  writingChapters = [],
  selectedIds,
  selectedForeshadowingIds = [],
  onSelectConfirm,
}: MemoryModalProps) {
  const controller = useMemoryModal({
    open,
    onCancel,
    bookId,
    writingChapters,
    selectedIds,
    selectedForeshadowingIds,
    onSelectConfirm,
  })

  return (
    <Modal
      title="本轮强制注入"
      open={open}
      onCancel={onCancel}
      width={680}
      destroyOnHidden
      className="ai-memory-modal"
      footer={
        controller.activeTab === 'select' ? (
          <Button type="primary" onClick={controller.handleSelectOk}>
            本轮带上（{controller.checkedIds.length + controller.checkedForeshadowingIds.length} 条）
          </Button>
        ) : null
      }
      styles={{ body: { height: '60vh', overflow: 'auto' } }}
    >
      <Tabs
        activeKey={controller.activeTab}
        onChange={controller.setActiveTab}
        items={[
          {
            key: 'select',
            label: '本轮强制注入',
            children: <SelectionTab controller={controller} />,
          },
          {
            key: 'manage',
            label: '快速添加旧设定',
            children: (
              <ManageTab controller={controller} writingChapters={writingChapters} />
            ),
          },
        ]}
      />
    </Modal>
  )
}
