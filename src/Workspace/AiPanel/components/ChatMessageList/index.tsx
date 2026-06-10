import React from "react";
import { VerticalAlignBottomOutlined } from "@ant-design/icons";
import { Button, Tooltip } from "antd";
import type { TextAreaRef } from "antd/es/input/TextArea";
import { Virtuoso, type ListProps, type VirtuosoHandle } from "react-virtuoso";
import type { AiModelConfig, EntityId } from "../../../../types";
import { type ChatMessage } from "../../hooks";
import { type AiContextBarBindings } from "../AiContextBar";
import { type ModelSelectionBindings } from "../AiComposeBottom";
import ChatMessageBubble from "./ChatMessageBubble";

export interface ChatMessageListProps {
  virtuosoRef: React.RefObject<VirtuosoHandle | null>;
  combinedData: ChatMessage[];
  firstItemIndex: number;
  prependedHistoryLength: number;
  loading: boolean;
  userHasScrolledUp: boolean;
  isAtBottom: boolean;
  setIsAtBottom: React.Dispatch<React.SetStateAction<boolean>>;
  setScrolledUpByReason: (nextValue: boolean, reason: string) => void;
  onScrollToBottom: () => void;
  bookId: EntityId | null | undefined;
  chapterId: EntityId | null | undefined;
  /** 关联上下文栏的整组绑定（关联章节/大纲、设定选择、popover）。 */
  contextBar: AiContextBarBindings;
  editingMessageIndex: number | null;
  setEditingMessageIndex: React.Dispatch<React.SetStateAction<number | null>>;
  editingMessageDraftRef: React.MutableRefObject<string>;
  editTextareaRef: React.RefObject<TextAreaRef | null>;
  onEditSend: (editIndex: number, content?: string) => void;
  modelConfigs: AiModelConfig[];
  /** 模型选择的整组绑定（对话模式、所选模型、思考开关）。 */
  modelSelection: ModelSelectionBindings;
  onAbort: () => void;
  onAddFavorite: (prompt: string, content: string) => void;
}

export default function ChatMessageList({
  virtuosoRef,
  combinedData,
  firstItemIndex,
  prependedHistoryLength,
  loading,
  userHasScrolledUp,
  isAtBottom,
  setIsAtBottom,
  setScrolledUpByReason,
  onScrollToBottom,
  bookId,
  chapterId,
  contextBar,
  editingMessageIndex,
  setEditingMessageIndex,
  editingMessageDraftRef,
  editTextareaRef,
  onEditSend,
  modelConfigs,
  modelSelection,
  onAbort,
  onAddFavorite,
}: ChatMessageListProps) {
  if (combinedData.length === 0) return null;

  return (
    <>
      <Virtuoso
        ref={virtuosoRef as React.RefObject<VirtuosoHandle>}
        data={combinedData}
        firstItemIndex={firstItemIndex}
        initialTopMostItemIndex={{
          index: combinedData.length - 1,
          align: "end",
        }}
        alignToBottom={!userHasScrolledUp}
        followOutput="auto"
        atBottomThreshold={40}
        atBottomStateChange={(atBottom) => {
          setIsAtBottom(atBottom);
          if (atBottom) return;
          if (loading) {
            setScrolledUpByReason(true, "atBottom-false-while-loading");
          } else {
            setScrolledUpByReason(true, "atBottom-false-idle");
          }
        }}
        atTopStateChange={() => {
          /* 向上滚动加载历史：可在此接入分页 API */
        }}
        computeItemKey={(index) => firstItemIndex + index}
        components={{
          List: React.forwardRef<HTMLDivElement, ListProps>(
            ({ style, children, ...rest }, ref) => (
              <div
                ref={ref}
                style={style}
                className="chat-virtuoso-list"
                {...rest}
              >
                {children}
              </div>
            ),
          ),
        }}
        style={{ flex: 1, minHeight: 0 }}
        itemContent={(index, msg) => {
          const dataIndex = index - firstItemIndex;
          const convIndex = dataIndex - prependedHistoryLength;
          return (
            <ChatMessageBubble
              index={index}
              dataIndex={dataIndex}
              convIndex={convIndex}
              message={msg}
              combinedData={combinedData}
              loading={loading}
              bookId={bookId}
              chapterId={chapterId}
              contextBar={contextBar}
              editingMessageIndex={editingMessageIndex}
              setEditingMessageIndex={setEditingMessageIndex}
              editingMessageDraftRef={editingMessageDraftRef}
              editTextareaRef={editTextareaRef}
              onEditSend={onEditSend}
              modelConfigs={modelConfigs}
              modelSelection={modelSelection}
              onAbort={onAbort}
              onAddFavorite={onAddFavorite}
              setScrolledUpByReason={setScrolledUpByReason}
            />
          );
        }}
      />
      {userHasScrolledUp && !isAtBottom && (
        <Tooltip title="回到底部">
          <Button
            type="primary"
            size="small"
            icon={<VerticalAlignBottomOutlined />}
            className="chat-scroll-to-bottom-btn"
            onClick={onScrollToBottom}
          />
        </Tooltip>
      )}
    </>
  );
}
