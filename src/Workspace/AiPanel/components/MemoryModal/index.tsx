import { PurrButton, PurrModal } from '@/purr-components'
import { PurrTabs } from '@/purr-components'
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
  selectedLongTermMemoryIds = [],
  selectedForeshadowingIds = [],
  onSelectConfirm,
}: MemoryModalProps) {
  const controller = useMemoryModal({
    open,
    onCancel,
    bookId,
    writingChapters,
    selectedIds,
    selectedLongTermMemoryIds,
    selectedForeshadowingIds,
    onSelectConfirm,
  })

  return (
    <PurrModal
      title="本轮强制注入"
      open={open}
      onCancel={onCancel}
      width={680}
      destroyOnHidden
      className="ai-memory-modal"
      footer={
        controller.activeTab === 'select' ? (
          <PurrButton type="primary" onClick={controller.handleSelectOk}>
            本轮带上（{controller.checkedLongTermMemoryIds.length + controller.checkedIds.length + controller.checkedForeshadowingIds.length} 条）
          </PurrButton>
        ) : null
      }
      styles={{ body: { height: '60vh', overflow: 'auto' } }}
    >
      <PurrTabs
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
    </PurrModal>
  )
}
