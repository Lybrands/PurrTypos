import React from "react";
import { Collapse } from "antd";
import "./index.scss";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

interface ThinkingRegionProps {
  regionKey: string;
  content: string;
  streaming: boolean;
  streamingHeader?: boolean;
  showCursor?: boolean;
  defaultOpen?: boolean;
  onWheelUp?: () => void;
}

const openStateStore = new Map<string, boolean>();
const followLatestStore = new Map<string, boolean>();
const scrollTopStore = new Map<string, number>();
const manualOpenDuringStreamStore = new Map<string, boolean>();

export default function ThinkingRegion({
  regionKey,
  content,
  streaming,
  streamingHeader = false,
  showCursor = false,
  defaultOpen = false,
  onWheelUp,
}: ThinkingRegionProps) {
  const [open, setOpen] = React.useState(
    openStateStore.get(regionKey) ?? defaultOpen,
  );
  const [followLatest, setFollowLatest] = React.useState(
    followLatestStore.get(regionKey) ?? true,
  );
  const contentRef = React.useRef<HTMLDivElement | null>(null);

  React.useEffect(() => {
    const restoredOpen = openStateStore.get(regionKey) ?? defaultOpen;
    const restoredFollow = followLatestStore.get(regionKey) ?? true;
    setOpen(restoredOpen);
    setFollowLatest(restoredFollow);
  }, [regionKey, defaultOpen]);

  React.useLayoutEffect(() => {
    const el = contentRef.current;
    if (!el) return;
    const savedTop = scrollTopStore.get(regionKey);
    if (followLatest) {
      el.scrollTop = el.scrollHeight;
    } else if (typeof savedTop === "number") {
      el.scrollTop = savedTop;
    }
  }, [regionKey, content, followLatest]);

  React.useEffect(() => {
    if (!streaming || !followLatest) return;
    const el = contentRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
    scrollTopStore.set(regionKey, el.scrollTop);
  }, [streaming, followLatest, content]);

  const prevStreamingRef = React.useRef(streaming);
  React.useEffect(() => {
    const wasStreaming = prevStreamingRef.current;
    if (!wasStreaming && streaming) {
      manualOpenDuringStreamStore.set(regionKey, false);
    }
    if (wasStreaming && !streaming) {
      const keepOpen = manualOpenDuringStreamStore.get(regionKey) === true;
      if (!keepOpen) {
        openStateStore.set(regionKey, false);
        setOpen(false);
      }
    }
    prevStreamingRef.current = streaming;
  }, [streaming, regionKey]);

  const handleWheel = React.useCallback(
    (e: React.WheelEvent<HTMLDivElement>) => {
      e.stopPropagation();
      const isUp = e.deltaY < 0;
      if (isUp) {
        followLatestStore.set(regionKey, false);
        setFollowLatest(false);
        onWheelUp?.();
      }
    },
    [onWheelUp, regionKey],
  );

  const thinkingContent = (
    <div
      ref={contentRef}
      className="bubble-thinking-content"
      onWheel={handleWheel}
      onScroll={(e) => {
        e.stopPropagation();
        scrollTopStore.set(regionKey, e.currentTarget.scrollTop);
      }}
    >
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
      {showCursor && <span className="a-thinking-cursor" />}
    </div>
  );

  if (streaming && streamingHeader) {
    return (
      <div className="bubble-thinking-streaming">
        <div className="bubble-thinking-label">
          正在思考
          <span className="a-blink-dots">...</span>
        </div>
        {thinkingContent}
      </div>
    );
  }

  return (
    <Collapse
      size="small"
      className="bubble-thinking-collapse"
      activeKey={open ? ["t"] : []}
      onChange={(keys) => {
        const nextOpen = (keys as string[]).includes("t");
        if (streaming) {
          manualOpenDuringStreamStore.set(regionKey, nextOpen);
        }
        openStateStore.set(regionKey, nextOpen);
        setOpen(nextOpen);
      }}
      items={[
        {
          key: "t",
          label: "思考过程",
          children: thinkingContent,
        },
      ]}
    />
  );
}
