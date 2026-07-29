import React from "react";
import { Popover } from "../../../../ui";
import Markdown from "../Markdown";

export interface ConversationTurnIndexItem {
  dataIndex: number;
  userText: string;
  userMarkdown: string;
  assistantText: string;
  assistantMarkdown: string;
}

interface ConversationTurnIndexProps {
  items: ConversationTurnIndexItem[];
  activeIndex: number;
  onSelect: (item: ConversationTurnIndexItem) => void;
}

/**
 * Popover 会被渲染到 document.body，不能只依赖对话面板的样式选择器。
 * 这里直接向 Purr UI 的弹层根节点、内层和内容区写入尺寸边界，
 * 以保证长提问不会把悬浮卡按原文宽度撑开。
 */
const TURN_INDEX_POPOVER_STYLES = {
  root: {
    width: 320,
    maxWidth: "calc(100vw - 32px)",
  },
  container: {
    boxSizing: "border-box" as const,
    width: "100%",
    maxWidth: "100%",
    maxHeight: 220,
    overflow: "hidden",
  },
  content: {
    width: "100%",
    maxWidth: "100%",
    maxHeight: 192,
    overflow: "hidden",
  },
};

const TURN_INDEX_PREVIEW_STYLE: React.CSSProperties = {
  width: "100%",
  maxWidth: "100%",
  overflow: "hidden",
};

function ConversationTurnPreview({
  item,
  turnNumber,
}: {
  item: ConversationTurnIndexItem;
  turnNumber: number;
}) {
  return (
    <div className="chat-turn-index-preview" style={TURN_INDEX_PREVIEW_STYLE}>
      <div className="chat-turn-index-preview-meta">第 {turnNumber} 轮对话</div>
      <div className="chat-turn-index-preview-question">
        <Markdown>{item.userMarkdown}</Markdown>
      </div>
      <div className="chat-turn-index-preview-answer">
        <Markdown>{item.assistantMarkdown}</Markdown>
      </div>
    </div>
  );
}

export default function ConversationTurnIndex({
  items,
  activeIndex,
  onSelect,
}: ConversationTurnIndexProps) {
  const listRef = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    const list = listRef.current;
    const activeItem = list?.querySelector<HTMLElement>(
      '[data-active="true"]',
    );
    if (!list || !activeItem) return;

    const itemTop = activeItem.offsetTop;
    const itemBottom = itemTop + activeItem.offsetHeight;
    const visibleTop = list.scrollTop;
    const visibleBottom = visibleTop + list.clientHeight;

    if (itemTop < visibleTop) {
      list.scrollTop = Math.max(0, itemTop - 8);
    } else if (itemBottom > visibleBottom) {
      list.scrollTop = itemBottom - list.clientHeight + 8;
    }
  }, [activeIndex]);

  if (items.length < 2) return null;

  return (
    <nav className="chat-turn-index" aria-label="对话索引">
      <div className="chat-turn-index-list" ref={listRef}>
        {items.map((item, index) => {
          const isActive = index === activeIndex;
          return (
            <Popover
              key={`${item.dataIndex}-${index}`}
              placement="right"
              trigger="hover"
              mouseEnterDelay={0.08}
              mouseLeaveDelay={0.06}
              arrow={{ pointAtCenter: true }}
              destroyOnHidden
              classNames={{ root: "chat-turn-index-popover" }}
              styles={TURN_INDEX_POPOVER_STYLES}
              content={
                <ConversationTurnPreview
                  item={item}
                  turnNumber={index + 1}
                />
              }
            >
              <button
                type="button"
                className={`chat-turn-index-item${isActive ? " is-active" : ""}`}
                data-active={isActive ? "true" : "false"}
                aria-label={`跳转到第 ${index + 1} 轮对话：${item.userText}`}
                aria-current={isActive ? "location" : undefined}
                onClick={(event) => {
                  event.currentTarget.blur();
                  onSelect(item);
                }}
              >
                <span className="chat-turn-index-mark" />
              </button>
            </Popover>
          );
        })}
      </div>
    </nav>
  );
}
