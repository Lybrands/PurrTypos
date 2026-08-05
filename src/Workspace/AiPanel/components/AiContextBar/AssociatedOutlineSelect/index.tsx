import React from 'react'
import { PurrButton, PurrMultiSelect, PurrTooltip } from '@/purr-components'
import { BoltIcon } from '@/purr-components'
import type { EntityId } from '../../../../../types'

export interface AssociatedOutlineSelectProps {
  value: EntityId[]
  onChange: (ids: EntityId[]) => void
  options: { label: string; value: EntityId }[]
  chapterId: EntityId | null | undefined
  onQuickAssociate: () => void
}

export default function AssociatedOutlineSelect({
  value,
  onChange,
  options,
  chapterId,
  onQuickAssociate,
}: AssociatedOutlineSelectProps) {
  return (
    <div className="ai-context-group">
      <PurrMultiSelect
        className="ai-context-select"
        size="small"
        placeholder="章节大纲"
        allowClear
        value={value}
        onChange={onChange}
        options={options}
      />
      {chapterId != null && (
        <PurrTooltip title="快捷关联当前章节大纲">
          <PurrButton
            type="text"
            size="small"
            icon={<BoltIcon style={{ fontSize: 12 }} />}
            onClick={onQuickAssociate}
            className="ai-context-quick-btn"
          />
        </PurrTooltip>
      )}
    </div>
  )
}
