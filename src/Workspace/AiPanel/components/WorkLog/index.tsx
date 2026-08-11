import React from "react";
import { AlertCircleIcon, ChevronRightIcon } from '@/purr-components';
import {
  applyWorkLogAutoOpen,
  getInitialWorkLogOpenState,
  readWorkLogOpenState,
  toggleWorkLogOpenState,
  writeWorkLogOpenState,
  type WorkLogOpenState,
} from "./state";
import "./index.scss";

export interface WorkLogProps {
  logKey: string;
  active: boolean;
  autoOpen: boolean;
  stepCount: number;
  startedAt?: number;
  durationMs?: number;
  hasError?: boolean;
  children: React.ReactNode;
}

const openStateStore = new Map<string, WorkLogOpenState>();

function formatDuration(ms: number): string {
  if (ms < 1000) return `${Math.max(1, Math.round(ms))}ms`;
  const totalSeconds = Math.max(1, Math.round(ms / 1000));
  if (totalSeconds < 60) return `${totalSeconds}秒`;
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return seconds > 0 ? `${minutes}分${seconds}秒` : `${minutes}分钟`;
}

function formatActiveElapsed(ms: number): string {
  const totalSeconds = Math.max(0, Math.floor(ms / 1000));
  if (totalSeconds < 60) return `${totalSeconds}秒`;
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return seconds > 0 ? `${minutes}分${seconds}秒` : `${minutes}分钟`;
}

function useTicker(enabled: boolean): number {
  const [now, setNow] = React.useState(() => performance.now());

  React.useEffect(() => {
    if (!enabled) return;
    setNow(performance.now());
    const timer = window.setInterval(() => setNow(performance.now()), 500);
    return () => window.clearInterval(timer);
  }, [enabled]);

  return now;
}

export default function WorkLog({
  logKey,
  active,
  autoOpen,
  stepCount,
  startedAt,
  durationMs,
  hasError = false,
  children,
}: WorkLogProps) {
  const [openState, setOpenState] = React.useState(() =>
    getInitialWorkLogOpenState(
      openStateStore.get(logKey),
      autoOpen,
    ),
  );
  const previousAutoOpenRef = React.useRef(autoOpen);
  const contentId = React.useId();
  const now = useTicker(active && startedAt != null);

  React.useEffect(() => {
    setOpenState(getInitialWorkLogOpenState(
      readWorkLogOpenState(openStateStore, logKey),
      autoOpen,
    ));
    previousAutoOpenRef.current = autoOpen;
  }, [logKey]);

  React.useEffect(() => {
    const previousAutoOpen = previousAutoOpenRef.current;
    previousAutoOpenRef.current = autoOpen;
    const nextState = applyWorkLogAutoOpen(
      openState,
      previousAutoOpen,
      autoOpen,
    );
    if (nextState === openState) return;
    writeWorkLogOpenState(openStateStore, logKey, nextState);
    setOpenState(nextState);
  }, [autoOpen, logKey, openState]);

  const elapsedMs = active && startedAt != null
    ? Math.max(0, now - startedAt)
    : durationMs;
  const hasDetails = React.Children.count(children) > 0;
  const durationText = active
    ? formatActiveElapsed(elapsedMs ?? 0)
    : elapsedMs != null && elapsedMs > 0
      ? formatDuration(elapsedMs)
      : null;
  const title = active
    ? "正在进行"
    : stepCount > 0
      ? `执行了 ${stepCount} 个步骤`
      : "用时";

  const toggleOpen = () => {
    const nextState = toggleWorkLogOpenState(openState);
    writeWorkLogOpenState(openStateStore, logKey, nextState);
    setOpenState(nextState);
  };

  return (
    <section
      className={`work-log ${openState.open ? "work-log--open" : ""} ${active ? "work-log--active" : ""} ${hasError ? "work-log--error" : ""}`}
    >
      {hasDetails ? (
        <button
          type="button"
          className="work-log__toggle"
          onClick={toggleOpen}
          aria-expanded={openState.open}
          aria-controls={contentId}
        >
          <ChevronRightIcon className="work-log__chevron" />
          {hasError ? <AlertCircleIcon className="work-log__error-icon" /> : null}
          <span>{title}</span>
          {durationText ? (
            <span className="work-log__duration">· {durationText}</span>
          ) : null}
          {active ? <span className="a-blink-dots">...</span> : null}
        </button>
      ) : (
        <div className="work-log__toggle work-log__toggle--static">
          <span>{title}</span>
          {durationText ? (
            <span className="work-log__duration">· {durationText}</span>
          ) : null}
          {active ? <span className="a-blink-dots">...</span> : null}
        </div>
      )}
      {hasDetails ? (
        <div
          id={contentId}
          className="work-log__collapsible"
          hidden={!openState.open}
        >
          <div className="work-log__body">{children}</div>
        </div>
      ) : null}
    </section>
  );
}
