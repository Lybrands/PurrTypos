import React from 'react'
import { Button, MultiSelect, Tooltip } from '../../../../../ui'
import { ThunderboltOutlined } from '../../../../../ui'
import type { EntityId } from '../../../../../types'

export interface AssociatedChapterSelectProps {
  value: EntityId[]
  onChange: (ids: EntityId[]) => void
  options: { label: string; value: EntityId }[]
  chapterId: EntityId | null | undefined
  onQuickAssociate: () => void
}

export default function AssociatedChapterSelect({
  value,
  onChange,
  options,
  chapterId,
  onQuickAssociate,
}: AssociatedChapterSelectProps) {
  return (
    <div className="ai-context-group">
      <MultiSelect
        className="ai-context-select"
        size="small"
        placeholder="章节内容"
        allowClear
        value={value}
        onChange={onChange}
        options={options}
      />
      {chapterId != null && (
        <Tooltip title="快捷关联当前章节内容">
          <Button
            type="text"
            size="small"
            icon={<ThunderboltOutlined style={{ fontSize: 12 }} />}
            onClick={onQuickAssociate}
            className="ai-context-quick-btn"
          />
        </Tooltip>
      )}
    </div>
  )
}
