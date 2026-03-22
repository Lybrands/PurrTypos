import React from 'react'
import { Button, Select, Tooltip } from 'antd'
import { ThunderboltOutlined } from '@ant-design/icons'

export interface AssociatedOutlineSelectProps {
  value: number[]
  onChange: (ids: number[]) => void
  options: { label: string; value: number }[]
  chapterId: number | null | undefined
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
      <Select
        className="ai-context-select"
        size="small"
        mode="multiple"
        placeholder="章节大纲"
        allowClear
        maxTagCount="responsive"
        value={value}
        onChange={onChange}
        options={options}
        styles={{ popup: { root: { minWidth: 160 } } }}
      />
      {chapterId != null && (
        <Tooltip title="快捷关联当前章节大纲">
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
