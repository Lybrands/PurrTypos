import React from "react";
import { PurrButton, PurrPopover, PurrTooltip } from '@/purr-components';
import "./index.scss";
import { LinkIcon, StoryContextIcon } from '@/purr-components';
import AssociatedChapterSelect from "./AssociatedChapterSelect";
import AssociatedOutlineSelect from "./AssociatedOutlineSelect";
import PromptTemplatePicker from "../PromptTemplatePicker";
import type { PromptTemplateContext } from "../../promptTemplates";
import type { EntityId } from "../../../../types";

/**
 * 关联上下文栏的绑定集合 —— 关联章节/大纲选择、本书设定/伏笔选择、popover 开合。
 *
 * 这一组 prop 是高度内聚的整体，由 book conversation extension 透传给 AiContextBar。
 */
export interface AiContextBarBindings {
  associatedChapterIds: EntityId[];
  setAssociatedChapterIds: (ids: EntityId[]) => void;
  associatedOutlineIds: EntityId[];
  setAssociatedOutlineIds: (ids: EntityId[]) => void;
  chapterSelectOptions: { value: EntityId; label: string }[];
  outlineSelectOptions: { value: EntityId; label: string }[];
  onQuickAssociateChapter: () => void;
  onQuickAssociateOutline: () => void;
  selectedMemoryIds: (number | string)[];
  selectedLongTermMemoryIds: string[];
  selectedForeshadowingIds: (number | string)[];
  onOpenMemoryModal: () => void;
  contextPopoverOpen: boolean;
  onContextPopoverOpenChange: (open: boolean) => void;
}

export interface AiContextBarProps extends AiContextBarBindings {
  bookId: EntityId | null;
  chapterId: EntityId | null;
  /** 提示词模版相关 */
  currentPrompt?: string;
  onInsertPrompt?: (text: string) => void;
  promptTemplateContext?: PromptTemplateContext;
  promptTemplateDisabled?: boolean;
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
  selectedLongTermMemoryIds,
  selectedForeshadowingIds,
  onOpenMemoryModal,
  contextPopoverOpen,
  onContextPopoverOpenChange,
  currentPrompt,
  onInsertPrompt,
  promptTemplateContext,
  promptTemplateDisabled,
}: AiContextBarProps) {
  if (bookId == null) return null;
  return (
    <div className="ai-context-bar">
      <PurrTooltip title="关联章节与大纲">
        <span className="purr-popup-trigger">
          <PurrPopover
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
          <PurrButton
            type="text"
            size="small"
            icon={<LinkIcon style={{ fontSize: 14 }} />}
            className="ai-context-icon-btn"
            aria-label="关联章节与大纲"
          />
          </PurrPopover>
        </span>
      </PurrTooltip>
      <PurrTooltip title="注入设定">
        <PurrButton
          type="text"
          size="small"
          icon={<StoryContextIcon />}
          aria-label="注入设定"
          onClick={onOpenMemoryModal}
          className={`ai-context-icon-btn ${selectedLongTermMemoryIds.length || selectedMemoryIds.length || selectedForeshadowingIds.length ? "ai-memory-btn--has-selection" : ""}`}
        />
      </PurrTooltip>
      {onInsertPrompt ? (
        <PromptTemplatePicker
          currentPrompt={currentPrompt ?? ""}
          onInsert={onInsertPrompt}
          context={promptTemplateContext ?? {}}
          disabled={promptTemplateDisabled}
        />
      ) : null}
    </div>
  );
}
