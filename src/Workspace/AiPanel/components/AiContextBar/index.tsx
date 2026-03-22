import React from 'react'
import { Button, Popover, Tooltip } from 'antd'
import './index.scss'
import { BulbOutlined, LinkOutlined } from '@ant-design/icons'
import AssociatedChapterSelect from './AssociatedChapterSelect'
import AssociatedOutlineSelect from './AssociatedOutlineSelect'

export interface AiContextBarProps {
  bookId: number | null
  chapterId: number | null
  associatedChapterIds: number[]
  setAssociatedChapterIds: (ids: number[]) => void
  associatedOutlineIds: number[]
  setAssociatedOutlineIds: (ids: number[]) => void
  chapterSelectOptions: { value: number; label: string }[]
  outlineSelectOptions: { value: number; label: string }[]
  onQuickAssociateChapter: () => void
  onQuickAssociateOutline: () => void
  selectedMemoryIds: (number | string)[]
  selectedForeshadowingIds: (number | string)[]
  onOpenMemoryModal: () => void
  contextPopoverOpen: boolean
  onContextPopoverOpenChange: (open: boolean) => void
}

export default function AiContextBar({
  bookId,
  chapterId,
  associatedChapterIds,
  setAssociatedChapterIds,
  associatedOutlineIds,
  setAssociatedOutlineIds,
  chapterSelectOptions,
  outlineSelectOptions,
  onQuickAssociateChapter,
  onQuickAssociateOutline,
  selectedMemoryIds,
  selectedForeshadowingIds,
  onOpenMemoryModal,
  contextPopoverOpen,
  onContextPopoverOpenChange,
}: AiContextBarProps) {
  if (bookId == null) return null
  return (
    <div className="ai-context-bar">
      <Popover
        trigger="click"
        open={contextPopoverOpen}
        onOpenChange={onContextPopoverOpenChange}
        arrow={false}
        placement="topLeft"
        overlayClassName="ai-context-popover"
        content={
          <div className="ai-context-popover-content">
            <div className="ai-context-popover-row">
              <span className="ai-context-popover-label">章节内容</span>
              <AssociatedChapterSelect
                value={associatedChapterIds}
                onChange={setAssociatedChapterIds}
                options={chapterSelectOptions}
                chapterId={chapterId}
                onQuickAssociate={onQuickAssociateChapter}
              />
            </div>
            <div className="ai-context-popover-row">
              <span className="ai-context-popover-label">章节大纲</span>
              <AssociatedOutlineSelect
                value={associatedOutlineIds}
                onChange={setAssociatedOutlineIds}
                options={outlineSelectOptions}
                chapterId={chapterId}
                onQuickAssociate={onQuickAssociateOutline}
              />
            </div>
          </div>
        }
      >
        <Tooltip title="关联章节与大纲">
          <Button
            type="text"
            size="small"
            icon={<LinkOutlined style={{ fontSize: 14 }} />}
            className="ai-context-icon-btn"
          />
        </Tooltip>
      </Popover>
      <Tooltip
        title={
          selectedMemoryIds.length || selectedForeshadowingIds.length
            ? `已选 ${selectedMemoryIds.length} 条记忆、${selectedForeshadowingIds.length} 条伏笔，发送时将注入`
            : '选用长期记忆注入'
        }
      >
        <Button
          type="text"
          size="small"
          icon={<BulbOutlined style={{ fontSize: 14 }} />}
          onClick={onOpenMemoryModal}
          className={`ai-context-icon-btn ${selectedMemoryIds.length || selectedForeshadowingIds.length ? 'ai-memory-btn--has-selection' : ''}`}
        />
      </Tooltip>
    </div>
  )
}
