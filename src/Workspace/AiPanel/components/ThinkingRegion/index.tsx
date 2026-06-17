import React from "react";
import { RightOutlined } from "@ant-design/icons";
import Markdown from "../Markdown";
import "./index.scss";

interface ThinkingRegionProps {
  regionKey: string;
  content: string;
  streaming: boolean;
  startedAt?: number;
  durationMs?: number;
  showCursor?: boolean;
  onWheelUp?: () => void;
}

const openStateStore = new Map<string, boolean>();
const scrollTopStore = new Map<string, number>();

function formatDurationSec(ms: number | undefined): string | null {
  if (ms == null || ms <= 0) return null;
  const sec = ms / 1000;
  return `${Math.max(1, Math.round(sec))}`;
}

function formatElapsedSec(ms: number): string {
  return String(Math.floor(ms / 1000));
}

function headerLabel(streaming: boolean, elapsedMs: number, durationMs?: number): string {
  if (streaming) {
    return "思考中";
  }
  const dur = formatDurationSec(durationMs);
  return dur ? "已思考" : "思考过程";
}

function useTicker(enabled: boolean): number {
  const [now, setNow] = React.useState(() => performance.now());

  React.useEffect(() => {
    if (!enabled) return;
    setNow(performance.now());
    const timer = window.setInterval(() => {
      setNow(performance.now());
    }, 500);
    return () => window.clearInterval(timer);
  }, [enabled]);

  return now;
}

export default function ThinkingRegion({
  regionKey,
  content,
  streaming,
  startedAt,
  durationMs,
  showCursor = false,
  onWheelUp,
}: ThinkingRegionProps) {
  const [open, setOpen] = React.useState(
    () => openStateStore.get(regionKey) ?? false,
  );
  const contentRef = React.useRef<HTMLDivElement | null>(null);
  const now = useTicker(streaming && typeof startedAt === "number");
  const elapsedMs =
    streaming && typeof startedAt === "number"
      ? Math.max(0, now - startedAt)
      : 0;

  React.useEffect(() => {
    setOpen(openStateStore.get(regionKey) ?? false);
  }, [regionKey]);

  React.useLayoutEffect(() => {
    if (!open || !streaming) return;
    const el = contentRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [open, streaming, content]);

  const handleWheel = React.useCallback(
    (e: React.WheelEvent<HTMLDivElement>) => {
      e.stopPropagation();
      if (e.deltaY < 0) onWheelUp?.();
    },
    [onWheelUp],
  );

  const toggleOpen = () => {
    const next = !open;
    openStateStore.set(regionKey, next);
    setOpen(next);
  };

  return (
    <div
      className={`thinking-region ${streaming ? "thinking-region--streaming" : ""} ${open ? "thinking-region--open" : ""}`}
    >
      <button
        type="button"
        className="thinking-region__toggle"
        onClick={toggleOpen}
        aria-expanded={open}
      >
        <RightOutlined
          className={`thinking-region__chevron ${open ? "thinking-region__chevron--open" : ""}`}
        />
        <span>{headerLabel(streaming, elapsedMs, durationMs)}</span>
        <span className="thinking-region__timer">
          {streaming
            ? `${formatElapsedSec(elapsedMs)} 秒`
            : formatDurationSec(durationMs)
              ? `${formatDurationSec(durationMs)} 秒`
              : ""}
        </span>
        {streaming && <span className="a-blink-dots">...</span>}
      </button>
      <div className="thinking-region__body">
        <div
          ref={contentRef}
          className="thinking-region__content"
          onWheel={handleWheel}
          onScroll={(e) => {
            e.stopPropagation();
            scrollTopStore.set(regionKey, e.currentTarget.scrollTop);
          }}
        >
          <Markdown>{content}</Markdown>
          {streaming && showCursor && open && (
            <span className="a-thinking-cursor" />
          )}
        </div>
      </div>
    </div>
  );
}
