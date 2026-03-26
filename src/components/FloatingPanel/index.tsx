import React from "react";
import { Button, Tooltip } from "antd";
import {
  CloseOutlined,
  PushpinFilled,
  PushpinOutlined,
} from "@ant-design/icons";
import "./index.scss";

interface FloatingPanelProps {
  title: React.ReactNode;
  open: boolean;
  onClose: () => void;
  children: React.ReactNode;
  width?: number;
  initialPinned?: boolean;
  initialTop?: number;
  initialPosition?: { x: number; y: number };
  pinned?: boolean;
  onPinnedChange?: (next: boolean) => void;
  /** 持久化键：不同面板请传不同 key，避免共享缓存 */
  storageKey?: string;
}

interface FloatingPanelCachedState {
  pinned: boolean;
  pos: { x: number; y: number };
}

const FLOATING_PANEL_STORAGE_PREFIX = "floating-panel:";

function readCachedState(storageKey?: string): FloatingPanelCachedState | null {
  if (!storageKey) return null;
  try {
    const raw = localStorage.getItem(FLOATING_PANEL_STORAGE_PREFIX + storageKey);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<FloatingPanelCachedState>;
    const x = Number(parsed?.pos?.x);
    const y = Number(parsed?.pos?.y);
    const pinned = typeof parsed?.pinned === "boolean" ? parsed.pinned : null;
    if (pinned == null || !Number.isFinite(x) || !Number.isFinite(y)) return null;
    return { pinned, pos: { x, y } };
  } catch {
    return null;
  }
}

export default function FloatingPanel({
  title,
  open,
  onClose,
  children,
  width = 400,
  initialPinned = false,
  initialTop = 72,
  initialPosition,
  pinned,
  onPinnedChange,
  storageKey,
}: FloatingPanelProps) {
  const cachedRef = React.useRef<FloatingPanelCachedState | null>(
    readCachedState(storageKey),
  );
  const [innerPinned, setInnerPinned] = React.useState(
    cachedRef.current?.pinned ?? initialPinned,
  );
  const [pos, setPos] = React.useState({
    x: cachedRef.current?.pos.x ?? initialPosition?.x ?? 8,
    y: cachedRef.current?.pos.y ?? initialPosition?.y ?? initialTop,
  });
  const panelRef = React.useRef<HTMLDivElement | null>(null);
  const draggingRef = React.useRef(false);
  const dragStartRef = React.useRef({ x: 0, y: 0 });
  const isPinned = pinned ?? innerPinned;

  const setPinnedState = React.useCallback(
    (next: boolean) => {
      if (pinned == null) setInnerPinned(next);
      onPinnedChange?.(next);
    },
    [pinned, onPinnedChange],
  );

  React.useEffect(() => {
    const cached = readCachedState(storageKey);
    cachedRef.current = cached;
    if (cached) {
      if (pinned == null) setInnerPinned(cached.pinned);
      setPos(cached.pos);
      return;
    }
    if (pinned == null) setInnerPinned(initialPinned);
    setPos({ x: initialPosition?.x ?? 8, y: initialPosition?.y ?? initialTop });
  }, [storageKey, pinned, initialPinned, initialPosition, initialTop]);

  React.useEffect(() => {
    if (open) return;
    draggingRef.current = false;
  }, [open]);

  React.useEffect(() => {
    if (!open || isPinned) return;
    const onPointerDown = (e: MouseEvent) => {
      const panel = panelRef.current;
      if (!panel) return;
      const target = e.target as Node | null;
      if (target && panel.contains(target)) return;
      onClose();
    };
    window.addEventListener("mousedown", onPointerDown);
    return () => {
      window.removeEventListener("mousedown", onPointerDown);
    };
  }, [open, isPinned, onClose]);

  React.useEffect(() => {
    if (!open || !initialPosition) return;
    if (cachedRef.current) return;
    const nextX = Math.min(
      Math.max(initialPosition.x, 8),
      Math.max(window.innerWidth - width - 8, 8),
    );
    const nextY = Math.min(
      Math.max(initialPosition.y, 8),
      Math.max(window.innerHeight - 120, 8),
    );
    setPos({ x: nextX, y: nextY });
  }, [open, initialPosition, width]);

  React.useEffect(() => {
    if (!storageKey) return;
    try {
      const value: FloatingPanelCachedState = { pinned: isPinned, pos };
      localStorage.setItem(
        FLOATING_PANEL_STORAGE_PREFIX + storageKey,
        JSON.stringify(value),
      );
    } catch {
      // ignore storage write errors
    }
  }, [storageKey, isPinned, pos]);

  React.useEffect(() => {
    const onMouseMove = (e: MouseEvent) => {
      if (!draggingRef.current) return;
      const nextX = Math.min(
        Math.max(e.clientX - dragStartRef.current.x, 8),
        Math.max(window.innerWidth - width - 8, 8),
      );
      const nextY = Math.min(
        Math.max(e.clientY - dragStartRef.current.y, 8),
        Math.max(window.innerHeight - 120, 8),
      );
      setPos({ x: nextX, y: nextY });
    };
    const onMouseUp = () => {
      draggingRef.current = false;
    };
    window.addEventListener("mousemove", onMouseMove);
    window.addEventListener("mouseup", onMouseUp);
    return () => {
      window.removeEventListener("mousemove", onMouseMove);
      window.removeEventListener("mouseup", onMouseUp);
    };
  }, [width]);

  const handleHeaderMouseDown = React.useCallback(
    (e: React.MouseEvent<HTMLDivElement>) => {
      draggingRef.current = true;
      dragStartRef.current = {
        x: e.clientX - pos.x,
        y: e.clientY - pos.y,
      };
    },
    [pos.x, pos.y],
  );

  if (!open) return null;

  return (
    <div
      ref={panelRef}
      className="floating-panel"
      style={{ width, left: pos.x, top: pos.y }}
    >
      <div
        className="floating-panel__header"
        onMouseDown={handleHeaderMouseDown}
      >
        <span className="floating-panel__title">{title}</span>
        <div className="floating-panel__actions">
          <Button
            type="text"
            size="small"
            icon={isPinned ? <PushpinFilled /> : <PushpinOutlined />}
            onClick={(e) => {
              e.stopPropagation();
              setPinnedState(!isPinned);
            }}
          />
          <Tooltip title="关闭">
            <Button
              type="text"
              size="small"
              icon={<CloseOutlined />}
              onClick={(e) => {
                e.stopPropagation();
                onClose();
              }}
            />
          </Tooltip>
        </div>
      </div>
      <div className="floating-panel__body">{children}</div>
    </div>
  );
}
