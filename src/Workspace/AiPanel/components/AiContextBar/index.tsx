import React from "react";
import { Button, Popover, Tooltip } from "../../../../ui";
import "./index.scss";
import { BulbOutlined, LinkOutlined } from "../../../../ui";
import AssociatedChapterSelect from "./AssociatedChapterSelect";
import AssociatedOutlineSelect from "./AssociatedOutlineSelect";
import PromptTemplatePicker from "../PromptTemplatePicker";
import type { PromptTemplateContext } from "../../promptTemplates";
import type { EntityId } from "../../../../types";

/**
 * 关联上下文栏的绑定集合 —— 关联章节/大纲选择、本书设定/伏笔选择、popover 开合。
 *
 * 这一组 prop 是高度内聚的整体，会从 AiPanel 一路透传到 ChatMessageBubble 里的
 * AiContextBar。打成一个对象后沿途只需传一个 prop，叶子处用 `{...bindings}` 展开。
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
      <Tooltip title="关联章节与大纲">
        <span className="purr-popup-trigger">
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
          <Button
            type="text"
            size="small"
            icon={<LinkOutlined style={{ fontSize: 14 }} />}
            className="ai-context-icon-btn"
            aria-label="关联章节与大纲"
          />
          </Popover>
        </span>
      </Tooltip>
      <Tooltip
        title={
          selectedMemoryIds.length || selectedForeshadowingIds.length
            ? `已选 ${selectedMemoryIds.length} 条本书设定、${selectedForeshadowingIds.length} 条伏笔，发送时将注入`
            : "选用本书设定注入"
        }
      >
        <Button
          type="text"
          size="small"
          icon={<BulbOutlined style={{ fontSize: 14 }} />}
          onClick={onOpenMemoryModal}
          className={`ai-context-icon-btn ${selectedMemoryIds.length || selectedForeshadowingIds.length ? "ai-memory-btn--has-selection" : ""}`}
        />
      </Tooltip>
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
