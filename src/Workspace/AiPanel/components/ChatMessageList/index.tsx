import React from "react";
import { VerticalAlignBottomOutlined } from "../../../../ui";
import { Button, Tooltip } from "../../../../ui";
import type { TextAreaRef } from "../../../../ui";
import { Virtuoso, type ListProps, type VirtuosoHandle } from "react-virtuoso";
import type { AiModelConfig, EntityId } from "../../../../types";
import { type ChatMessage } from "../../hooks";
import { type AiContextBarBindings } from "../AiContextBar";
import { type ModelSelectionBindings } from "../AiComposeBottom";
import ChatMessageBubble from "./ChatMessageBubble";
import ConversationTurnIndex, {
  type ConversationTurnIndexItem,
} from "./ConversationTurnIndex";

const VirtuosoList = React.forwardRef<HTMLDivElement, ListProps>(
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
);
VirtuosoList.displayName = "AiChatVirtuosoList";

const VIRTUOSO_COMPONENTS = {
  List: VirtuosoList,
};

function toIndexPreview(
  value: string | undefined,
  fallback: string,
  maxLength: number,
): string {
  const normalized = (value ?? "")
    .slice(0, maxLength * 4)
    .replace(/```[\s\S]*?```/g, " 代码片段 ")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/!\[[^\]]*\]\([^)]*\)/g, " 图片 ")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/^\s{0,3}(?:#{1,6}|>|[-*+]\s|\d+[.)]\s)\s*/gm, "")
    .replace(/[*_~]+/g, "")
    .replace(/\s+/g, " ")
    .trim();
  return normalized ? normalized.slice(0, maxLength) : fallback;
}

function buildConversationTurnIndex(
  messages: ChatMessage[],
): ConversationTurnIndexItem[] {
  const turns: ConversationTurnIndexItem[] = [];

  messages.forEach((message, dataIndex) => {
    if (message.role !== "user") return;

    let assistant: ChatMessage | undefined;
    for (let index = dataIndex + 1; index < messages.length; index += 1) {
      const candidate = messages[index];
      if (candidate.role === "user") break;
      if (candidate.role === "assistant") {
        assistant = candidate;
        break;
      }
    }
    const toolSummary = assistant?.toolCallSegments
      ?.flatMap((segment) => segment.labels)
      .join("、");
    const assistantSource =
      assistant?.contentAfterToolCalls ||
      assistant?.content ||
      assistant?.thinking ||
      toolSummary;

    turns.push({
      dataIndex,
      userText: toIndexPreview(message.content, "未命名提问", 180),
      assistantText: toIndexPreview(
        assistantSource,
        assistant ? "AI 正在整理回复…" : "等待 AI 回复…",
        240,
      ),
    });
  });

  return turns;
}

function findTurnAtDataIndex(
  turns: ConversationTurnIndexItem[],
  dataIndex: number,
): number {
  let activeIndex = 0;
  for (let index = 0; index < turns.length; index += 1) {
    if (turns[index].dataIndex > dataIndex) break;
    activeIndex = index;
  }
  return activeIndex;
}

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
  const turnIndexItems = React.useMemo(
    () => buildConversationTurnIndex(combinedData),
    [combinedData],
  );
  const [activeTurnIndex, setActiveTurnIndex] = React.useState(0);

  React.useEffect(() => {
    if (turnIndexItems.length === 0) {
      setActiveTurnIndex(0);
      return;
    }
    setActiveTurnIndex((current) =>
      isAtBottom
        ? turnIndexItems.length - 1
        : Math.min(current, turnIndexItems.length - 1),
    );
  }, [isAtBottom, turnIndexItems.length]);

  const handleVisibleRangeChange = React.useCallback(
    ({ startIndex, endIndex }: { startIndex: number; endIndex: number }) => {
      if (turnIndexItems.length === 0) return;
      const normalizeIndex = (index: number) =>
        index >= firstItemIndex &&
        index < firstItemIndex + combinedData.length
          ? index - firstItemIndex
          : index;
      const visibleCenter = Math.round(
        (normalizeIndex(startIndex) + normalizeIndex(endIndex)) / 2,
      );
      setActiveTurnIndex(
        findTurnAtDataIndex(turnIndexItems, visibleCenter),
      );
    },
    [combinedData.length, firstItemIndex, turnIndexItems],
  );

  const handleSelectTurn = React.useCallback(
    (item: ConversationTurnIndexItem) => {
      setScrolledUpByReason(true, "conversation-index-jump");
      setActiveTurnIndex(findTurnAtDataIndex(turnIndexItems, item.dataIndex));
      virtuosoRef.current?.scrollToIndex({
        index: item.dataIndex,
        align: "center",
        behavior: "smooth",
      });
    },
    [setScrolledUpByReason, turnIndexItems, virtuosoRef],
  );

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
        rangeChanged={handleVisibleRangeChange}
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
        components={VIRTUOSO_COMPONENTS}
        style={{ flex: 1, minHeight: 0 }}
        itemContent={(index, msg) => {
          const dataIndex = index - firstItemIndex;
          const convIndex = dataIndex - prependedHistoryLength;
          const isLast = dataIndex === combinedData.length - 1;
          const prevMsg =
            dataIndex > 0 ? combinedData[dataIndex - 1] : undefined;
          const prevUserContent =
            prevMsg?.role === "user" ? (prevMsg.content ?? "") : "";
          return (
            <ChatMessageBubble
              index={index}
              convIndex={convIndex}
              message={msg}
              isLast={isLast}
              prevUserContent={prevUserContent}
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
      <ConversationTurnIndex
        items={turnIndexItems}
        activeIndex={activeTurnIndex}
        onSelect={handleSelectTurn}
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
