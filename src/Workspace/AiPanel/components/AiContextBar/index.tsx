import React from 'react'
import { Button, Checkbox, Popover, Tooltip } from 'antd'
import './index.scss'
import { BranchesOutlined, BulbOutlined, LinkOutlined } from '@ant-design/icons'
import AssociatedChapterSelect from './AssociatedChapterSelect'
import AssociatedOutlineSelect from './AssociatedOutlineSelect'
import type { EntityId } from '../../../../types'
import {
  PIPELINE_SELECT_OPTIONS,
  applyPipelineCheckboxToggle,
  pipelineIsNonDefaultFull,
  type PipelineStageId,
} from '../../pipelineStages'

export interface AiContextBarProps {
  bookId: EntityId | null
  chapterId: EntityId | null
  associatedChapterIds: EntityId[]
  setAssociatedChapterIds: (ids: EntityId[]) => void
  associatedOutlineIds: EntityId[]
  setAssociatedOutlineIds: (ids: EntityId[]) => void
  chapterSelectOptions: { value: EntityId; label: string }[]
  outlineSelectOptions: { value: EntityId; label: string }[]
  onQuickAssociateChapter: () => void
  onQuickAssociateOutline: () => void
  selectedMemoryIds: (number | string)[]
  selectedForeshadowingIds: (number | string)[]
  onOpenMemoryModal: () => void
  contextPopoverOpen: boolean
  onContextPopoverOpenChange: (open: boolean) => void
  /** 写作专家模式：管线 Popover 与关联章节一致，由父级控制显隐 */
  pipelinePopoverOpen?: boolean
  onPipelinePopoverOpenChange?: (open: boolean) => void
  /** 写作专家模式：管线阶段多选 */
  pipelineAgentEnabled?: boolean
  pipelineSelectedStages?: PipelineStageId[]
  onPipelineStagesChange?: (stages: PipelineStageId[]) => void
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
  pipelinePopoverOpen = false,
  onPipelinePopoverOpenChange,
  pipelineAgentEnabled = false,
  pipelineSelectedStages = ['full'],
  onPipelineStagesChange,
}: AiContextBarProps) {
  if (bookId == null) return null
  return (
    <div className="ai-context-bar">
      {pipelineAgentEnabled && onPipelineStagesChange && onPipelinePopoverOpenChange ? (
        <Popover
          trigger="click"
          open={pipelinePopoverOpen}
          onOpenChange={(open) => {
            onPipelinePopoverOpenChange(open)
            if (open) onContextPopoverOpenChange(false)
          }}
          arrow={false}
          placement="topLeft"
          overlayClassName="ai-context-popover"
          content={
            <div className="ai-context-popover-content ai-context-pipeline-popover">
              <div className="ai-context-pipeline-heading">专家管线</div>
              <div className="ai-context-pipeline-grid">
                {PIPELINE_SELECT_OPTIONS.map((opt) => (
                  <div
                    key={opt.value}
                    className={
                      opt.value === 'full'
                        ? 'ai-context-pipeline-slot ai-context-pipeline-slot--full'
                        : 'ai-context-pipeline-slot'
                    }
                  >
                    <Checkbox
                      checked={pipelineSelectedStages.includes(opt.value)}
                      onChange={(e) => {
                        onPipelineStagesChange(
                          applyPipelineCheckboxToggle(
                            pipelineSelectedStages,
                            opt.value,
                            e.target.checked,
                          ),
                        )
                      }}
                    >
                      {opt.label}
                    </Checkbox>
                  </div>
                ))}
              </div>
            </div>
          }
        >
          <Tooltip title="写作专家管线（可多选）">
            <Button
              type="text"
              size="small"
              icon={<BranchesOutlined style={{ fontSize: 14 }} />}
              className={`ai-context-icon-btn${pipelineIsNonDefaultFull(pipelineSelectedStages) ? ' ai-pipeline-btn--active' : ''}`}
            />
          </Tooltip>
        </Popover>
      ) : null}
      <Popover
        trigger="click"
        open={contextPopoverOpen}
        onOpenChange={(open) => {
          onContextPopoverOpenChange(open)
          if (open && onPipelinePopoverOpenChange) onPipelinePopoverOpenChange(false)
        }}
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
