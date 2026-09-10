import React from "react";
import { AlertCircleIcon, ChevronRightIcon } from '@/purr-components';
import {
  applyExecutionLogAutoOpen,
  getInitialExecutionLogOpenState,
  readExecutionLogOpenState,
  toggleExecutionLogOpenState,
  writeExecutionLogOpenState,
  type ExecutionLogOpenState,
} from "./state";
import "./index.scss";

export interface ExecutionLogProps {
  logKey: string;
  title: string;
  active: boolean;
  autoOpen: boolean;
  startedAt?: number;
  durationMs?: number;
  hasError?: boolean;
  children: React.ReactNode;
}

const openStateStore = new Map<string, ExecutionLogOpenState>();
const stepGroupOpenStateStore = new Map<string, boolean>();

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

export default function ExecutionLog({
  logKey,
  title,
  active,
  autoOpen,
  startedAt,
  durationMs,
  hasError = false,
  children,
}: ExecutionLogProps) {
  const [openState, setOpenState] = React.useState(() =>
    getInitialExecutionLogOpenState(
      openStateStore.get(logKey),
      autoOpen,
    ),
  );
  const contentId = React.useId();
  const now = useTicker(active && startedAt != null);

  React.useEffect(() => {
    setOpenState(getInitialExecutionLogOpenState(
      readExecutionLogOpenState(openStateStore, logKey),
      autoOpen,
    ));
  }, [logKey]);

  React.useEffect(() => {
    const nextState = applyExecutionLogAutoOpen(
      openState,
      autoOpen,
    );
    if (nextState === openState) return;
    writeExecutionLogOpenState(openStateStore, logKey, nextState);
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
  const toggleOpen = () => {
    const nextState = toggleExecutionLogOpenState(openState);
    writeExecutionLogOpenState(openStateStore, logKey, nextState);
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
        </button>
      ) : (
        <div className="work-log__toggle work-log__toggle--static">
          <span>{title}</span>
          {durationText ? (
            <span className="work-log__duration">· {durationText}</span>
          ) : null}
        </div>
      )}
      {hasDetails ? (
        <div
          id={contentId}
          className="work-log__collapsible"
          hidden={!openState.open}
        >
          <div className="work-log__body">{openState.open ? children : null}</div>
        </div>
      ) : null}
    </section>
  );
}

export interface ExecutionLogStepGroupProps {
  groupKey: string;
  stepCount: number;
  completedDurationMs: number;
  activeStartedAt?: number;
  active?: boolean;
  activeLabel?: string;
  hasError?: boolean;
  children: React.ReactNode;
}

export function ExecutionLogStepGroup({
  groupKey,
  stepCount,
  completedDurationMs,
  activeStartedAt,
  active = false,
  activeLabel,
  hasError = false,
  children,
}: ExecutionLogStepGroupProps) {
  const [open, setOpen] = React.useState(
    () => stepGroupOpenStateStore.get(groupKey) ?? false,
  );
  const contentId = React.useId();
  const now = useTicker(active && activeStartedAt != null);

  React.useEffect(() => {
    setOpen(stepGroupOpenStateStore.get(groupKey) ?? false);
  }, [groupKey]);

  const toggleOpen = () => {
    const nextOpen = !open;
    stepGroupOpenStateStore.set(groupKey, nextOpen);
    setOpen(nextOpen);
  };
  const activeElapsedMs = active && activeStartedAt != null
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
        aria-controls={contentId}
      >
        <ChevronRightIcon className="work-log-step-group__chevron" />
        {hasError ? (
          <AlertCircleIcon className="work-log-step-group__error-icon" />
        ) : null}
        <span>
          {active
            ? activeLabel
              ? `正在执行 ${activeLabel}`
              : "正在执行"
            : "执行了"}
        </span>
        {!active ? (
          <span className="work-log-step-group__count">{stepCount} 个步骤</span>
        ) : null}
        {totalDurationMs > 0 ? (
          <span className="work-log-step-group__duration">
            · {formatDuration(totalDurationMs)}
          </span>
        ) : null}
        {active ? <span className="a-blink-dots">...</span> : null}
      </button>
      <div
        id={contentId}
        className="work-log-step-group__collapsible"
        hidden={!open}
      >
        <div className="work-log-step-group__body">{open ? children : null}</div>
      </div>
    </div>
  );
}
