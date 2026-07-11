import React from "react";
import { PlusOutlined } from "@ant-design/icons";
import { Button, Input, Tabs, Tooltip } from "antd";
import type { AiSession, EntityId } from "../../../../types";
import SessionHistoryPopover from "../SessionHistoryPopover";

interface SessionTabsBarProps {
  bookId: EntityId;
  /** 全局作用域会话不绑章节，传 null */
  chapterId: EntityId | null;
  /** 会话作用域；setting 时历史列表查询不绑章节的全局会话 */
  scope?: "chapter" | "setting";
  /** 章节作用域下显示在标签栏左侧的当前章节标题 */
  chapterTitle?: string;
  sessions: AiSession[];
  activeSessionId: number | null;
  loading: boolean;
  isCurrentSessionEmpty: boolean;
  editingTabId: number | null;
  editingTitle: string;
  onActiveSessionChange: (sessionId: number) => void;
  onEditingTabIdChange: (sessionId: number | null) => void;
  onEditingTitleChange: (title: string) => void;
  onSaveTabTitle: () => void;
  onNewSession: () => void;
  onCloseTab: (session: AiSession) => void;
  onOpenFromHistory: (session: AiSession) => void;
  onDeleteFromHistory: (session: AiSession) => void;
  onBlockedByLoading: () => void;
}

export default function SessionTabsBar({
  bookId,
  chapterId,
  scope = "chapter",
  chapterTitle,
  sessions,
  activeSessionId,
  loading,
  isCurrentSessionEmpty,
  editingTabId,
  editingTitle,
  onActiveSessionChange,
  onEditingTabIdChange,
  onEditingTitleChange,
  onSaveTabTitle,
  onNewSession,
  onCloseTab,
  onOpenFromHistory,
  onDeleteFromHistory,
  onBlockedByLoading,
}: SessionTabsBarProps) {
  const hasBlankSession = sessions.length > 0 && isCurrentSessionEmpty;

  return (
    <Tabs
      type="editable-card"
      hideAdd
      className="session-tabs-bar"
      size="small"
      activeKey={activeSessionId ? String(activeSessionId) : undefined}
      onChange={(key) => {
        if (loading) {
          onBlockedByLoading();
          return;
        }
        onActiveSessionChange(Number(key));
      }}
      onEdit={(targetKey, action) => {
        if (action !== "remove") return;
        const session = sessions.find((s) => String(s.id) === targetKey);
        if (session) onCloseTab(session);
      }}
      tabBarExtraContent={{
        left: chapterTitle ? (
          <span className="session-tabs-chapter-title" title={chapterTitle}>
            {chapterTitle}
          </span>
        ) : undefined,
        right: (
          <div className="session-tab-actions">
            <Tooltip
              title={
                hasBlankSession ? "当前对话尚未开始" : "新建对话"
              }
            >
              <Button
                type="text"
                size="small"
                icon={<PlusOutlined style={{ fontSize: 13 }} />}
                onClick={onNewSession}
                disabled={loading || hasBlankSession}
                className="session-new-btn"
              />
            </Tooltip>
            <SessionHistoryPopover
              bookId={bookId}
              chapterId={chapterId}
              scope={scope}
              activeSessionId={activeSessionId}
              onOpen={onOpenFromHistory}
              onDelete={onDeleteFromHistory}
            />
          </div>
        ),
      }}
      items={sessions.map((s) => ({
        key: String(s.id),
        label:
          editingTabId === s.id ? (
            <Input
              size="small"
              value={editingTitle}
              onChange={(e) => onEditingTitleChange(e.target.value)}
              onBlur={onSaveTabTitle}
              onPressEnter={onSaveTabTitle}
              onClick={(e) => e.stopPropagation()}
              onDoubleClick={(e) => e.stopPropagation()}
              onKeyDown={(e) => e.stopPropagation()}
              className="session-tab-edit-input"
              autoFocus
            />
          ) : (
            <Tooltip
              title={s.title}
              placement="bottom"
              mouseEnterDelay={0.6}
              styles={{
                root: { maxWidth: 320 },
                container: {
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-all",
                },
              }}
            >
              <span
                className="session-tab-title"
                onDoubleClick={(e) => {
                  e.stopPropagation();
                  onEditingTabIdChange(s.id);
                  onEditingTitleChange(s.title);
                }}
              >
                {s.title}
              </span>
            </Tooltip>
          ),
        closable: true,
      }))}
    />
  );
}
