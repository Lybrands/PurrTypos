import React from "react";
import { AlignBottomIcon } from '@/purr-components';
import { PurrButton, PurrTooltip } from '@/purr-components';
import type { PurrTextAreaRef } from '@/purr-components';
import { Virtuoso, type ListProps, type VirtuosoHandle } from "react-virtuoso";
import type { AiModelConfig, EntityId } from "../../../../types";
import AgentConversationTurnIndex, {
  buildAgentConversationTurnIndex,
  type AgentConversationTurnIndexItem,
} from '../../../../components/AgentConversationTurnIndex';
import { type ChatMessage } from "../../hooks";
import { type AiContextBarBindings } from "../AiContextBar";
import { type ModelSelectionBindings } from "../AiComposeBottom";
import ChatMessageBubble from "./ChatMessageBubble";

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

function findTurnAtDataIndex(
  turns: AgentConversationTurnIndexItem[],
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
  editTextareaRef: React.RefObject<PurrTextAreaRef | null>;
  onEditSend: (editIndex: number, content?: string) => void;
  modelConfigs: AiModelConfig[];
  /** 模型选择的整组绑定（对话模式、所选模型、思考开关）。 */
  modelSelection: ModelSelectionBindings;
  onAbort: () => void;
  onAddFavorite: (prompt: string, content: string) => void;
  onStructuredAnswer: (answer: string) => void;
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
  onStructuredAnswer,
}: ChatMessageListProps) {
  const scrollerCleanupRef = React.useRef<(() => void) | null>(null);
  const turnIndexItems = React.useMemo(
    () => buildAgentConversationTurnIndex(combinedData),
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
    ({ startIndex }: { startIndex: number; endIndex: number }) => {
      if (turnIndexItems.length === 0) return;
      const normalizeIndex = (index: number) =>
        index >= firstItemIndex &&
        index < firstItemIndex + combinedData.length
          ? index - firstItemIndex
          : index;
      setActiveTurnIndex(
        findTurnAtDataIndex(turnIndexItems, normalizeIndex(startIndex)),
      );
    },
    [combinedData.length, firstItemIndex, turnIndexItems],
  );

  const handleSelectTurn = React.useCallback(
    (item: AgentConversationTurnIndexItem) => {
      setScrolledUpByReason(true, "conversation-index-jump");
      setActiveTurnIndex(findTurnAtDataIndex(turnIndexItems, item.dataIndex));
      virtuosoRef.current?.scrollToIndex({
        index: item.dataIndex,
        align: "start",
        behavior: "smooth",
      });
    },
    [setScrolledUpByReason, turnIndexItems, virtuosoRef],
  );

  const handleScrollerRef = React.useCallback(
    (ref: HTMLElement | Window | null) => {
      scrollerCleanupRef.current?.();
      scrollerCleanupRef.current = null;
      if (!ref || ref instanceof Window) return;

      const handleWheel = (event: WheelEvent) => {
        if (event.deltaY < 0) {
          setScrolledUpByReason(true, "chat-scroller-wheel-up");
        }
      };
      const handleKeyDown = (event: KeyboardEvent) => {
        if (
          event.key === "ArrowUp" ||
          event.key === "PageUp" ||
          event.key === "Home"
        ) {
          setScrolledUpByReason(true, "chat-scroller-keyboard-up");
        }
      };
      ref.addEventListener("wheel", handleWheel, { passive: true });
      ref.addEventListener("keydown", handleKeyDown);
      scrollerCleanupRef.current = () => {
        ref.removeEventListener("wheel", handleWheel);
        ref.removeEventListener("keydown", handleKeyDown);
      };
    },
    [setScrolledUpByReason],
  );

  React.useEffect(
    () => () => {
      scrollerCleanupRef.current?.();
      scrollerCleanupRef.current = null;
    },
    [],
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
        scrollerRef={handleScrollerRef}
        rangeChanged={handleVisibleRangeChange}
        atBottomThreshold={40}
        atBottomStateChange={(atBottom) => {
          setIsAtBottom(atBottom);
          if (atBottom) {
            setScrolledUpByReason(false, "chat-scroller-returned-to-bottom");
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
              onStructuredAnswer={onStructuredAnswer}
              setScrolledUpByReason={setScrolledUpByReason}
            />
          );
        }}
      />
      <AgentConversationTurnIndex
        items={turnIndexItems}
        activeIndex={activeTurnIndex}
        onSelect={handleSelectTurn}
      />
      {userHasScrolledUp && !isAtBottom && (
        <PurrTooltip title="回到底部">
          <PurrButton
            type="primary"
            size="small"
            icon={<AlignBottomIcon />}
            className="chat-scroll-to-bottom-btn"
            onClick={onScrollToBottom}
          />
        </PurrTooltip>
      )}
    </>
  );
}
