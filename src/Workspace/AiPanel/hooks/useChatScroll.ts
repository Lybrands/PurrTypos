import React from "react";
import type { VirtuosoHandle } from "react-virtuoso";
import {
  createScrollFollowState,
  detachScrollFollow,
  observeScrollBottom,
  type ScrollFollowState,
} from "../../../components/AgentConversation/scrollFollowPolicy";
import type { ChatMessage } from "./chat.types";

/**
 * 聊天滚动 / 跟随 + 输入区拖拽高度的整组 UI 状态。
 *
 * 收纳原先散落在 AiPanel 里的几块相互关联的视图逻辑：
 * - Virtuoso 流式贴底跟随（用户未主动上滑时）；
 * - 发送新一轮后先定位本轮用户消息，再随首个流式增量贴底；
 * - 「回到底部」；
 *
 * 纯 UI 状态，不涉及业务数据。`combinedData` 由调用方合并历史+当前会话后传入。
 */
export function useChatScroll({
  loading,
  combinedData,
}: {
  loading: boolean;
  combinedData: ChatMessage[];
}) {
  const virtuosoRef = React.useRef<VirtuosoHandle>(null);
  /** 用户主动上滚后为 true，不再自动滚到底部；滚回底部或点击「回到底部」后恢复为 false。 */
  const [userHasScrolledUp, setUserHasScrolledUp] = React.useState(false);
  const [isAtBottom, setIsAtBottom] = React.useState(true);
  const isAtBottomRef = React.useRef(true);
  const scrollFollowStateRef = React.useRef<ScrollFollowState>(
    createScrollFollowState(),
  );
  const pinNewTurnToTopRef = React.useRef(false);

  const applyScrollFollowState = React.useCallback(
    (nextState: ScrollFollowState) => {
      scrollFollowStateRef.current = nextState;
      setUserHasScrolledUp((previous) =>
        previous === nextState.userDetached
          ? previous
          : nextState.userDetached,
      );
    },
    [],
  );

  const setScrolledUpByReason = React.useCallback(
    (nextValue: boolean, _reason: string) => {
      applyScrollFollowState(
        nextValue
          ? detachScrollFollow(
              scrollFollowStateRef.current,
              isAtBottomRef.current,
            )
          : createScrollFollowState(),
      );
    },
    [applyScrollFollowState],
  );

  const handleAtBottomStateChange = React.useCallback(
    (atBottom: boolean) => {
      isAtBottomRef.current = atBottom;
      setIsAtBottom(atBottom);
      applyScrollFollowState(
        observeScrollBottom(scrollFollowStateRef.current, atBottom),
      );
    },
    [applyScrollFollowState],
  );

  /** 发送/编辑发送时调用：下一次列表增长时把本轮用户消息钉到顶部。 */
  const pinNewTurnToTop = React.useCallback(() => {
    pinNewTurnToTopRef.current = true;
  }, []);

  // 回到底部：使用 Virtuoso 的 scrollToIndex，一次性跳到底部（不 smooth，避免断断续续）
  const handleScrollToBottom = React.useCallback(() => {
    const total = combinedData.length;
    if (total > 0) {
      virtuosoRef.current?.scrollToIndex({
        index: total - 1,
        align: "end",
        behavior: "auto",
      });
    }
    setScrolledUpByReason(false, "manual-scroll-to-bottom");
  }, [combinedData.length, setScrolledUpByReason]);

  React.useEffect(() => {
    if (!pinNewTurnToTopRef.current) return;
    const total = combinedData.length;
    if (total < 2) return;
    // 这次定位不是用户主动上滚，不能关闭随后思考/正文增量的自动跟随。
    setScrolledUpByReason(false, "pin-new-turn-before-stream");
    requestAnimationFrame(() => {
      virtuosoRef.current?.scrollToIndex({
        index: total - 2,
        align: "start",
        behavior: "auto",
      });
    });
    pinNewTurnToTopRef.current = false;
  }, [combinedData.length, setScrolledUpByReason]);

  const streamFollowKey = React.useMemo(() => {
    if (!loading || userHasScrolledUp || combinedData.length === 0) return "";
    const last = combinedData[combinedData.length - 1] as
      | ChatMessage
      | undefined;
    if (!last || last.role !== "assistant" || last.isError) return "";
    return [
      combinedData.length,
      last.content ?? "",
      last.commentary ?? "",
      last.toolCalling ? "1" : "0",
    ].join("|");
  }, [loading, userHasScrolledUp, combinedData]);

  // 流式输出期间（用户未主动上滑）保持视图贴底
  React.useEffect(() => {
    if (!streamFollowKey) return;
    const raf = requestAnimationFrame(() => {
      if (scrollFollowStateRef.current.userDetached) return;
      virtuosoRef.current?.scrollToIndex({
        index: combinedData.length - 1,
        align: "end",
        behavior: "auto",
      });
    });
    return () => cancelAnimationFrame(raf);
  }, [streamFollowKey, combinedData.length]);

  return {
    virtuosoRef,
    userHasScrolledUp,
    setScrolledUpByReason,
    isAtBottom,
    handleAtBottomStateChange,
    handleScrollToBottom,
    pinNewTurnToTop,
  };
}
