import React from "react";
import { AlertCircleIcon, ChevronRightIcon } from '@/purr-components';
import "./index.scss";

const stepGroupOpenStateStore = new Map<string, boolean>();

function formatDuration(ms: number): string {
  if (ms < 1000) return `${Math.max(1, Math.round(ms))}ms`;
  const totalSeconds = Math.max(1, Math.round(ms / 1000));
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

export interface WorkLogStepGroupProps {
  groupKey: string;
  stepCount: number;
  currentStepCount: number;
  completedDurationMs: number;
  activeStartedAt?: number;
  active?: boolean;
  hasError?: boolean;
  children: React.ReactNode;
}

export function WorkLogStepGroup({
  groupKey,
  stepCount,
  currentStepCount,
  completedDurationMs,
  activeStartedAt,
  active = false,
  hasError = false,
  children,
}: WorkLogStepGroupProps) {
  const [open, setOpen] = React.useState(
    () => stepGroupOpenStateStore.get(groupKey) ?? false,
  );
  const now = useTicker(active && activeStartedAt != null);

  React.useEffect(() => {
    setOpen(stepGroupOpenStateStore.get(groupKey) ?? false);
  }, [groupKey]);

  const toggleOpen = () => {
    const nextOpen = !open;
    stepGroupOpenStateStore.set(groupKey, nextOpen);
    setOpen(nextOpen);
  };
  const activeElapsedMs =
    active && activeStartedAt != null
      ? Math.max(0, now - activeStartedAt)
      : 0;
  const totalDurationMs = completedDurationMs + activeElapsedMs;

  return (
    <div
      className={`work-log-step-group ${open ? "work-log-step-group--open" : ""} ${active ? "work-log-step-group--active" : ""}`}
    >
      <button
        type="button"
        className="work-log-step-group__toggle"
        onClick={toggleOpen}
        aria-expanded={open}
      >
        <ChevronRightIcon className="work-log-step-group__chevron" />
        {hasError ? (
          <AlertCircleIcon className="work-log-step-group__error-icon" />
        ) : null}
        <span>
          {active
            ? `正在执行 ${currentStepCount}/${stepCount} 个步骤`
            : `执行了 ${stepCount} 个步骤`}
        </span>
        {totalDurationMs > 0 ? (
          <span className="work-log-step-group__duration">
            · {formatDuration(totalDurationMs)}
          </span>
        ) : null}
        {active ? <span className="a-blink-dots">...</span> : null}
      </button>
      <div className="work-log-step-group__collapsible">
        <div className="work-log-step-group__body">{children}</div>
      </div>
    </div>
  );
}
